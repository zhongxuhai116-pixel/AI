from __future__ import annotations

import argparse
import contextvars
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageStat
from . import director, director_plan, security, storage
from .director_plan import CameraPath, ProductPose, SceneSpec, Vector3
from .providers import comfyui


ROOT = Path(__file__).resolve().parents[3]
VAR = ROOT / "var"
UPLOADS = VAR / "uploads"
RUNS = VAR / "runs"
DB_PATH = VAR / "productdirector.db"
BLENDER_SCRIPT = ROOT / "blender" / "scripts" / "render_product.py"

DEFAULT_OWNER_ID = "owner-default"
DEFAULT_WORKSPACE_ID = "workspace-default"
DEFAULT_PROJECT_ID = "project-default"
LEASE_SECONDS = 60
TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
# 长任务续租间隔：至少每 1 秒一次，且不小于租约的三分之一，避免渲染期间租约失效。
HEARTBEAT_SECONDS = max(1.0, LEASE_SECONDS / 3)
# A06 媒体质量门：只拦截“明显坏了”的成片，避免把低对比度的正常产品画面误判为失败。
QA_MIN_STDDEV = float(os.getenv("PRODUCTDIRECTOR_QA_MIN_STDDEV", "2.0"))
QA_MIN_MEAN = float(os.getenv("PRODUCTDIRECTOR_QA_MIN_MEAN", "6.0"))
QA_MAX_BLACK_RATIO = float(os.getenv("PRODUCTDIRECTOR_QA_MAX_BLACK_RATIO", "0.35"))
QA_BLACK_PIXEL_LEVEL = 16
# 渲染前的最低可用磁盘空间；不足时快速失败，避免写出半截成片。
MIN_FREE_DISK_MB = float(os.getenv("PRODUCTDIRECTOR_MIN_FREE_DISK_MB", "1024"))
MINIMAX_API_BASE_URL = os.getenv("MINIMAX_API_BASE_URL", "https://api.minimaxi.com").rstrip("/")
# 设为真值时 API 不再进程内执行作业，任务留给独立 Worker（供 worker 模式与演练使用）。
INLINE_EXECUTOR_DISABLED = os.getenv("PRODUCTDIRECTOR_DISABLE_INLINE_EXECUTOR", "").strip().lower() in {"1", "true", "yes"}
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "PRODUCTDIRECTOR_ALLOWED_ORIGINS",
        "http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    if origin.strip()
]
for folder in (VAR, UPLOADS, RUNS):
    folder.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def lease_expiry_text(seconds: float | None = None) -> str:
    """租约到期时间。

    必须保留微秒：SQL 里用 ISO 字符串直接比较到期时间，而当前时间带微秒；
    若到期时间被截断到整秒，同一秒内会因 "+00:00" 与 ".123456+00:00" 的
    字符差异被误判为已过期。
    """
    span = LEASE_SECONDS if seconds is None else seconds
    return (datetime.now(timezone.utc) + timedelta(seconds=span)).isoformat()


def find_executable(name: str, candidates: list[str]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for raw in candidates:
        path = Path(os.path.expandvars(raw))
        if path.exists():
            return str(path)
    return None


BLENDER = find_executable(
    "blender",
    [r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"],
)
FFMPEG = find_executable(
    "ffmpeg",
    [
        r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe",
    ],
)


def _find_winget_ffmpeg(packages_root: str) -> str | None:
    try:
        packages = Path(os.path.expandvars(packages_root))
        if not packages.exists():
            return None
        for match in packages.rglob("ffmpeg.exe"):
            return str(match)
    except OSError:
        return None
    return None


if not FFMPEG:
    match = _find_winget_ffmpeg(r"%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages")
    if match:
        FFMPEG = match
FFPROBE = None
if FFMPEG:
    ffprobe_candidate = Path(FFMPEG).with_name("ffprobe.exe")
    FFPROBE = str(ffprobe_candidate) if ffprobe_candidate.exists() else find_executable("ffprobe", [])


def postgres_schema_sql() -> str:
    """PostgreSQL 建表脚本（与 SQLite 版逐列对应）。"""
    path = ROOT / "db" / "schema.postgres.sql"
    if not path.exists():
        raise RuntimeError(f"缺少 PostgreSQL schema 文件: {path}")
    return path.read_text(encoding="utf-8")


def connect():
    """返回数据库连接：默认 SQLite；设置 PRODUCTDIRECTOR_DATABASE_URL 可切到 PostgreSQL。"""
    return storage.connect(DB_PATH)


def begin_immediate(db) -> None:
    """升级为写事务，确保读取租约状态与写入结果在同一事务内完成。"""
    storage.begin_writer(db)


@dataclass(frozen=True)
class LeaseContext:
    """当前执行线程持有的租约；用于把状态写入绑定到具体 worker 与 epoch。"""

    job_id: str
    worker_id: str
    lease_epoch: int


CURRENT_LEASE: contextvars.ContextVar[LeaseContext | None] = contextvars.ContextVar("current_lease", default=None)


@contextmanager
def lease_scope(lease: LeaseContext):
    token = CURRENT_LEASE.set(lease)
    try:
        yield lease
    finally:
        CURRENT_LEASE.reset(token)


def ensure_column(db, table: str, column: str, definition: str) -> None:
    columns = set(storage.table_columns(db, table))
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_db() -> None:
    schema_sql = postgres_schema_sql() if storage.is_postgres() else """
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              kind TEXT NOT NULL,
              mime TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              path TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owners (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspaces (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(owner_id) REFERENCES owners(id)
            );
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              workspace_id TEXT NOT NULL,
              name TEXT NOT NULL,
              created_at TEXT NOT NULL,
              deleted_at TEXT,
              FOREIGN KEY(owner_id) REFERENCES owners(id),
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );
            CREATE TABLE IF NOT EXISTS plans (
              id TEXT PRIMARY KEY,
              product_asset_id TEXT NOT NULL,
              intent TEXT NOT NULL,
              payload TEXT NOT NULL,
              approved INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY(product_asset_id) REFERENCES assets(id)
            );
            CREATE TABLE IF NOT EXISTS product_versions (
              id TEXT PRIMARY KEY,
              product_asset_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              schema_version TEXT NOT NULL DEFAULT '1.0',
              version INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'ACTIVE',
              created_at TEXT NOT NULL,
              snapshot_sha256 TEXT,
              UNIQUE(product_asset_id, owner_id, project_id, version),
              FOREIGN KEY(product_asset_id) REFERENCES assets(id),
              FOREIGN KEY(owner_id) REFERENCES owners(id),
              FOREIGN KEY(project_id) REFERENCES projects(id)
            );
            CREATE TABLE IF NOT EXISTS plan_contracts (
              id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL,
              product_version_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              schema_version TEXT NOT NULL,
              contract_status TEXT NOT NULL DEFAULT 'DRAFT',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(plan_id) REFERENCES plans(id),
              FOREIGN KEY(product_version_id) REFERENCES product_versions(id)
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              stage TEXT NOT NULL,
              progress INTEGER NOT NULL,
              output_path TEXT,
              manifest_path TEXT,
              error TEXT,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(plan_id) REFERENCES plans(id),
              FOREIGN KEY(asset_id) REFERENCES assets(id)
            );
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL,
              plan_contract_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              idempotency_key TEXT,
              status TEXT NOT NULL,
              stage TEXT NOT NULL,
              progress INTEGER NOT NULL,
              job_id TEXT NOT NULL,
              attempt_count INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(plan_id) REFERENCES plans(id),
              FOREIGN KEY(plan_contract_id) REFERENCES plan_contracts(id),
              FOREIGN KEY(owner_id) REFERENCES owners(id),
              FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency ON runs(idempotency_key);
            CREATE TABLE IF NOT EXISTS run_jobs (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              job_id TEXT NOT NULL,
              attempt INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL,
              stage TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              error TEXT,
              lease_owner TEXT,
              lease_expires_at TEXT,
              lease_epoch INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(run_id) REFERENCES runs(id),
              FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            CREATE TABLE IF NOT EXISTS job_attempts (
              id TEXT PRIMARY KEY,
              run_job_id TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              completed_at TEXT,
              error TEXT,
              metadata TEXT,
              FOREIGN KEY(run_job_id) REFERENCES run_jobs(id)
            );
            CREATE TABLE IF NOT EXISTS job_events (
              id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              status TEXT NOT NULL,
              stage TEXT NOT NULL,
              progress INTEGER NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(job_id, sequence),
              FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            CREATE TABLE IF NOT EXISTS provider_credentials (
              provider TEXT PRIMARY KEY,
              encrypted_key BLOB NOT NULL,
              base_url TEXT NOT NULL,
              last_status TEXT,
              last_message TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
              token_hash TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              csrf_token TEXT NOT NULL,
              expires_at REAL NOT NULL,
              key_fingerprint TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_jobs (
              id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              operation TEXT NOT NULL,
              external_id TEXT,
              status TEXT NOT NULL,
              stage TEXT NOT NULL,
              request_payload TEXT NOT NULL,
              artifact_asset_id TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fidelity_policies (
              id TEXT PRIMARY KEY,
              product_version_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              mode TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(product_version_id, version),
              FOREIGN KEY(product_version_id) REFERENCES product_versions(id)
            );
            """
    with connect() as db:
        db.executescript(schema_sql)
        ensure_column(db, "run_jobs", "lease_owner", "TEXT")
        ensure_column(db, "run_jobs", "lease_expires_at", "TEXT")
        ensure_column(db, "run_jobs", "lease_epoch", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "assets", "owner_id", "TEXT NOT NULL DEFAULT 'owner-default'")
        ensure_column(db, "provider_jobs", "artifact_path", "TEXT")
        ensure_column(db, "provider_jobs", "artifact_sha256", "TEXT")
        ensure_column(db, "provider_jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(db, "product_versions", "approved_at", "TEXT")
        # v1 的后续阶段要求幂等以（plan_id, idempotency_key）为最小键，避免不同计划共享同一个 idempotency 误判。
        if not storage.is_postgres():
            legacy_index = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_runs_idempotency'"
            ).fetchone()
            if legacy_index:
                db.execute("DROP INDEX idx_runs_idempotency")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_plan_id_idempotency ON runs(plan_id, idempotency_key)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_contracts_plan_version ON plan_contracts(plan_id, version)")
        now = utc_now()
        db.execute(
            "INSERT OR IGNORE INTO owners(id, name, created_at) VALUES (?, ?, ?)",
            (DEFAULT_OWNER_ID, "ProductDirector Default Owner", now),
        )
        db.execute(
            "INSERT OR IGNORE INTO workspaces(id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
            (DEFAULT_WORKSPACE_ID, DEFAULT_OWNER_ID, "ProductDirector Default Workspace", now),
        )
        db.execute(
            "INSERT OR IGNORE INTO projects(id, owner_id, workspace_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
            (DEFAULT_PROJECT_ID, DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID, "ProductDirector Default Project", now),
        )


initialize_db()
processes: dict[str, subprocess.Popen] = {}
process_lock = threading.Lock()


DEFAULT_OUTPUT_WIDTH = 540
DEFAULT_OUTPUT_HEIGHT = 960
DEFAULT_FPS = 24
DEFAULT_DURATION_SECONDS = 6
# 主规划 V1 约束：三个镜头、总时长 5–8 秒、固定 24fps（即 120–192 帧）。
ALLOWED_DURATION_SECONDS = (5, 6, 7, 8)


class Shot(BaseModel):
    id: str
    name: str
    duration_frames: int = Field(ge=24, le=144)
    camera: Literal["dolly_in", "side_track", "hero_orbit", "static"]
    focal_length_mm: int = Field(ge=15, le=120)
    # 目标 3D 合同的显式字段；不填时渲染器沿用历史默认取景。
    sensor_width_mm: float = Field(default=36, gt=0, le=200)
    camera_target_m: Vector3 | None = None
    camera_path: CameraPath | None = None


class CropAnchor(str, Enum):
    """横向/纵向素材裁切到 9:16 时保留哪一侧（对应 CSS object-position 语义）。"""

    center = "center"
    top = "top"
    bottom = "bottom"
    left = "left"
    right = "right"


# FFmpeg crop 的 x/y 偏移表达式；图片路径先用它把源素材裁进 9:16 画布。
CROP_ANCHOR_OFFSETS: dict[str, tuple[str, str]] = {
    "center": ("(iw-ow)/2", "(ih-oh)/2"),
    "top": ("(iw-ow)/2", "0"),
    "bottom": ("(iw-ow)/2", "ih-oh"),
    "left": ("0", "(ih-oh)/2"),
    "right": ("iw-ow", "(ih-oh)/2"),
}


class OutputSpec(BaseModel):
    width: Literal[540, 1080] = DEFAULT_OUTPUT_WIDTH
    height: Literal[960, 1920] = DEFAULT_OUTPUT_HEIGHT
    fps: Literal[24] = DEFAULT_FPS
    duration_seconds: Literal[5, 6, 7, 8] = DEFAULT_DURATION_SECONDS

    @model_validator(mode="after")
    def validate_output_ratio(self):
        if self.width * 16 != self.height * 9:
            raise ValueError("输出分辨率必须为 9:16")
        return self

    @property
    def frame_count(self) -> int:
        return self.fps * self.duration_seconds


class PlanRequest(BaseModel):
    product_asset_id: str
    intent: str = Field(min_length=1, max_length=4000)
    ratio: Literal["9:16"] = "9:16"
    duration_seconds: Literal[5, 6, 7, 8] = 6
    output: OutputSpec = Field(default_factory=OutputSpec)
    crop_anchor: CropAnchor = CropAnchor.center
    product_pose: ProductPose = Field(default_factory=ProductPose)
    scene: SceneSpec = Field(default_factory=SceneSpec)
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID

    @model_validator(mode="after")
    def validate_output_duration(self):
        if self.duration_seconds != self.output.duration_seconds:
            raise ValueError("duration_seconds 与 output.duration_seconds 必须保持一致")
        return self


class PlanApproval(BaseModel):
    approved: bool = True
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID


class PlanUpdate(BaseModel):
    intent: str = Field(min_length=1, max_length=4000)
    shots: list[Shot] = Field(min_length=3, max_length=3)
    crop_anchor: CropAnchor = CropAnchor.center
    product_pose: ProductPose = Field(default_factory=ProductPose)
    scene: SceneSpec = Field(default_factory=SceneSpec)
    duration_seconds: Literal[5, 6, 7, 8] = 6
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID

    @model_validator(mode="after")
    def validate_total_duration(self):
        expected = DEFAULT_FPS * self.duration_seconds
        if sum(shot.duration_frames for shot in self.shots) != expected:
            raise ValueError(f"三个镜头的总帧数必须恰好为 {expected} 帧（{self.duration_seconds} 秒 × 24fps）")
        return self


class RunRequest(BaseModel):
    plan_id: str
    idempotency_key: str | None = None
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID


class H3ReconstructRequest(BaseModel):
    """V2：用现有 H3/ComfyUI 环境从产品图重建 3D 模型。"""

    product_asset_id: str
    crop: tuple[int, int, int, int] = (0, 0, 512, 512)
    steps: int = Field(default=50, ge=1, le=200)
    cfg: float = Field(default=5.0, ge=0.0, le=100.0)
    seed: int = Field(default=20260912, ge=0)
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID


class H3VideoRequest(BaseModel):
    """V2：用现有 H3 环境生成视频（参考视频 + 产品图 → 成片）。"""

    product_asset_id: str
    reference_video: str
    prompt: str = Field(min_length=1, max_length=8000)
    crop: tuple[int, int, int, int] = (0, 0, 512, 512)
    width: int = Field(default=576, ge=32, le=4096)
    height: int = Field(default=1024, ge=32, le=4096)
    length: int = Field(default=124, ge=5, le=362)
    steps: int = Field(default=4, ge=1, le=100)
    seed: int = Field(default=20260912, ge=0)
    # 先重建产品网格、再用 Blender 出正/45° 视图作为 H3 参考图（现场工作流做法）。
    with_product_views: bool = False
    mesh_steps: int = Field(default=50, ge=1, le=200)
    mesh_octree: int = Field(default=256, ge=16, le=512)
    scene: Literal["studio", "living", "bedroom"] = "studio"
    motion: Literal["pan", "push", "orbit"] = "pan"
    size_cm: float = Field(default=35.0, gt=0, le=300)
    blender_seconds: int = Field(default=4, ge=1, le=15)
    quality: Literal["preview", "720", "1080"] = "preview"
    photo_texture: Literal["sheet", "none"] = "sheet"
    framing: Literal["product", "room"] = "product"
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID


class ProtectedRegion(BaseModel):
    """归一化保护区域（0–1 相对坐标），用于严格保真合成时锁定产品像素。"""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    label: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def stays_inside_the_frame(self):
        if self.x + self.width > 1.0 + 1e-6 or self.y + self.height > 1.0 + 1e-6:
            raise ValueError("保护区域必须完全落在画面内（0–1 归一化）")
        return self


FIDELITY_MODES = ("STRICT", "CONTROLLED", "CREATIVE")
FIDELITY_OPERATIONS = (
    "color_transform", "edge_composite", "shadow_layer",
    "reflection_layer", "occlusion_layer", "background_generation",
)


class FidelityPolicyRequest(BaseModel):
    """V3-03：保真策略版本。STRICT 只允许确定性操作。"""

    mode: Literal["STRICT", "CONTROLLED", "CREATIVE"]
    protected_regions: list[ProtectedRegion] = Field(default_factory=list, max_length=64)
    allowed_operations: list[str] = Field(default_factory=list, max_length=16)
    notes: str = Field(default="", max_length=2000)
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID

    @model_validator(mode="after")
    def validate_operations(self):
        unknown = [item for item in self.allowed_operations if item not in FIDELITY_OPERATIONS]
        if unknown:
            raise ValueError(f"未知的允许操作: {', '.join(unknown)}")
        if self.mode == "STRICT":
            forbidden = [item for item in self.allowed_operations if item == "background_generation"]
            if forbidden:
                raise ValueError("STRICT 模式不允许背景生成直接覆盖产品像素")
        return self


class DirectorPlanRequest(BaseModel):
    """V2：把自然语言描述转成合法、可编辑的分镜计划草稿。"""

    intent: str = Field(min_length=1, max_length=4000)
    product_asset_id: str | None = None
    asset_kind: Literal["image", "model"] = "image"
    duration_seconds: Literal[5, 6, 7, 8] = 6
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID


class WorkerClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    job_id: str | None = None


class WorkerLeaseRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    lease_epoch: int = Field(ge=1)


class WorkerCompleteRequest(WorkerLeaseRequest):
    output_path: str | None = None
    manifest_path: str | None = None


class WorkerFailRequest(WorkerLeaseRequest):
    error: str = Field(min_length=1, max_length=4000)


class ProviderCredentialInput(BaseModel):
    base_url: str | None = None
    api_key: str = Field(min_length=20, max_length=512)


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def protect_secret(value: str) -> bytes:
    if os.name != "nt":
        return b"fernetv1:" + linux_cipher().encrypt(value.encode("utf-8"))
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "ProductDirectorAI MiniMax", None, None, None, 0,
        ctypes.byref(target),
    ):
        raise RuntimeError("Windows 凭证加密失败")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def unprotect_secret(value: bytes) -> str:
    if os.name != "nt":
        if not value.startswith(b"fernetv1:"):
            raise HTTPException(409, "凭证格式不兼容，请在当前系统重新配置")
        try:
            return linux_cipher().decrypt(value[len(b"fernetv1:"):]).decode("utf-8")
        except InvalidToken:
            raise HTTPException(409, "凭证无法解密，请核对密钥或重新配置") from None
    buffer = ctypes.create_string_buffer(value)
    source = DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target),
    ):
        raise RuntimeError("Windows 凭证解密失败")
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def linux_cipher() -> Fernet:
    key = os.getenv("PRODUCTDIRECTOR_SECRET_KEY", "")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError):
        raise HTTPException(503, "Linux 凭证存储需要独立配置 Fernet 密钥") from None


def minimax_call(api_key: str, path: str, payload: dict | None = None, base_url: str | None = None) -> tuple[int, dict]:
    base = _normalize_minimax_base_url(base_url or MINIMAX_API_BASE_URL)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method="POST" if payload is not None else "GET",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": {"message": raw[-500:]}}


def provider_row() -> sqlite3.Row | None:
    with connect() as db:
        return db.execute("SELECT * FROM provider_credentials WHERE provider = 'minimax'").fetchone()


def provider_public_status(row: sqlite3.Row | None = None) -> dict:
    row = row or provider_row()
    return {
        "provider": "minimax",
        "region": "中国大陆",
        "base_url": row["base_url"] if row else MINIMAX_API_BASE_URL,
        "configured": bool(row),
        "last_status": row["last_status"] if row else "NOT_CONFIGURED",
        "last_message": row["last_message"] if row else "尚未配置 MiniMax API Key",
        "updated_at": row["updated_at"] if row else None,
    }


def _normalize_allowed_origin(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


def _is_allowed_origin(origin: str | None) -> bool:
    normalized = _normalize_allowed_origin(origin)
    if not normalized:
        return False
    return normalized in ALLOWED_ORIGINS


def _is_browser_client(request: Request) -> bool:
    user_agent = (request.headers.get("user-agent") or "").lower()
    return "mozilla" in user_agent or "chrome" in user_agent or "safari" in user_agent


def _normalize_minimax_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise HTTPException(400, "MiniMax API 基础地址不能为空")
    parsed = urlsplit(normalized)
    if (parsed.scheme != "https" or parsed.hostname not in {"api.minimaxi.com", "api.minimax.io"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.netloc != parsed.hostname or parsed.path not in {"", "/v1"}):
        raise HTTPException(400, "仅允许官方 MiniMax HTTPS API 地址")
    return f"https://{parsed.hostname}"


def _safe_artifact_name(value: str) -> str:
    if not value:
        raise HTTPException(400, "产物路径不能为空")
    safe = str(Path(value).name).strip()
    if not safe:
        raise HTTPException(400, "产物路径无效")
    if re.search(r"^\.\.?(?:[\\\\/]|$)", safe):
        raise HTTPException(400, "产物路径不允许目录穿越")
    return safe


def _storage_reference(job_id: str, value: str | None) -> str | None:
    if not value:
        return None
    safe_name = _safe_artifact_name(value)
    return str(Path(job_id) / safe_name).replace("\\", "/")


def _resolve_job_artifact_path(job_id: str, value: str | None) -> Path:
    if not value:
        raise HTTPException(409, "任务尚未生成产物")
    reference = Path(value)
    if reference.is_absolute():
        # 历史数据可能写入绝对路径，先尝试兼容；但要求必须在 RUNS 目录内。
        absolute_path = reference
    else:
        absolute_path = RUNS / value
    absolute_path = absolute_path.resolve()
    try:
        absolute_path.relative_to((RUNS / job_id).resolve())
    except ValueError:
        raise HTTPException(403, "产物路径不在授权目录内")
    if not absolute_path.exists() or not absolute_path.is_file():
        raise HTTPException(409, "产物文件不存在")
    return absolute_path


_ensure_worker_token = security.ensure_worker


def resolve_job_owner_scope(job_id: str, owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> None:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        row = db.execute("SELECT r.owner_id, pv.project_id FROM runs r "
                         "JOIN plan_contracts pc ON pc.id = r.plan_contract_id "
                         "JOIN product_versions pv ON pv.id = pc.product_version_id "
                         "WHERE r.job_id = ? ORDER BY r.created_at DESC LIMIT 1", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问任务")


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def canonical_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalize_contract_context(owner_id: str, project_id: str, workspace_id: str | None = None) -> tuple[str, str, str]:
    if security.current_owner.get() is not None and owner_id != security.current_owner.get():
        raise HTTPException(403, "Owner 与登录身份不匹配")
    with connect() as db:
        owner = db.execute(
            "SELECT id FROM owners WHERE id = ?", (owner_id,),
        ).fetchone()
        if not owner:
            raise HTTPException(404, "owner 不存在")
        if workspace_id is None:
            workspace = db.execute(
                "SELECT id FROM workspaces WHERE owner_id = ? AND id = ?",
                (owner_id, DEFAULT_WORKSPACE_ID),
            ).fetchone()
            if not workspace:
                raise HTTPException(404, "默认 workspace 不存在")
            workspace_id = workspace["id"]
        else:
            workspace = db.execute(
                "SELECT id FROM workspaces WHERE owner_id = ? AND id = ?",
                (owner_id, workspace_id),
            ).fetchone()
            if not workspace:
                raise HTTPException(404, "workspace 不存在")
        project = db.execute(
            "SELECT id, workspace_id, owner_id FROM projects WHERE id = ?", (project_id,),
        ).fetchone()
        if not project:
            raise HTTPException(404, "project 不存在")
        if project["owner_id"] != owner_id or project["workspace_id"] != workspace_id:
            raise HTTPException(403, "越权访问计划项目")
        return owner_id, workspace_id, project_id


def plan_contract_snapshot_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_contract_payload_hash(payload: dict) -> tuple[str, str]:
    payload_text = canonical_payload(payload)
    return payload_text, plan_contract_snapshot_hash(payload_text)


def get_default_contract(plan_id: str, db: sqlite3.Connection | None = None, owner_id: str | None = None, project_id: str | None = None) -> sqlite3.Row | None:
    connection = db or connect()
    close_connection = db is None
    try:
        if owner_id is None or project_id is None:
            row = connection.execute(
                """
                SELECT * FROM plan_contracts
                WHERE plan_id = ?
                ORDER BY version DESC, created_at DESC
                LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            return row
        return connection.execute(
            """
            SELECT pc.*
            FROM plan_contracts pc
            JOIN product_versions pv ON pv.id = pc.product_version_id
            WHERE pc.plan_id = ? AND pv.owner_id = ? AND pv.project_id = ?
            ORDER BY pc.version DESC, pc.created_at DESC
            LIMIT 1
            """,
            (plan_id, owner_id, project_id),
        ).fetchone()
    finally:
        if close_connection:
            connection.close()


def next_contract_version(plan_id: str, db: sqlite3.Connection | None = None) -> int:
    connection = db or connect()
    close_connection = db is None
    try:
        row = connection.execute(
            "SELECT MAX(version) AS latest_version FROM plan_contracts WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row and row["latest_version"] is not None:
            return int(row["latest_version"]) + 1
        return 1
    finally:
        if close_connection:
            connection.close()


def upsert_plan_contract(
    plan_id: str,
    product_version_id: str,
    payload: dict,
    db: sqlite3.Connection | None = None,
) -> dict:
    now = utc_now()
    payload_text, payload_sha256 = plan_contract_payload_hash(payload)
    contract_id = str(uuid.uuid4())
    connection = db or connect()
    close_connection = db is None
    try:
        contract_version = next_contract_version(plan_id, connection)
        connection.execute(
            """
            INSERT INTO plan_contracts
            (id, plan_id, product_version_id, version, schema_version, contract_status, payload, payload_sha256, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)
            """,
            (
                contract_id,
                plan_id,
                product_version_id,
                contract_version,
                "1.0",
                payload_text,
                payload_sha256,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE plans SET intent = ?, payload = ?, approved = 0 WHERE id = ?",
            (payload["intent"], json.dumps(payload, ensure_ascii=False), plan_id),
        )
    finally:
        if close_connection:
            connection.close()
    return {
        "contract_id": contract_id,
        "contract_version": contract_version,
        "snapshot": payload,
        "snapshot_sha256": payload_sha256,
        "created_at": now,
    }


def create_product_version(
    product_asset_id: str,
    owner_id: str,
    project_id: str,
    snapshot_sha256: str,
    db: sqlite3.Connection | None = None,
) -> str:
    connection = db or connect()
    close_connection = db is None
    try:
        latest = connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM product_versions
            WHERE product_asset_id = ? AND owner_id = ? AND project_id = ?
            """,
            (product_asset_id, owner_id, project_id),
        ).fetchone()
        version = int(latest["version"]) + 1
        product_version_id = str(uuid.uuid4())
        now = utc_now()
        connection.execute(
            """
            INSERT INTO product_versions
            (id, product_asset_id, owner_id, project_id, version, status, schema_version, created_at, snapshot_sha256)
            VALUES (?, ?, ?, ?, ?, 'ACTIVE', '1.0', ?, ?)
            """,
            (
                product_version_id,
                product_asset_id,
                owner_id,
                project_id,
                version,
                now,
                snapshot_sha256,
            ),
        )
        return product_version_id
    finally:
        if close_connection:
            connection.close()


def ensure_plan_contract(
    db: sqlite3.Connection,
    plan_row: sqlite3.Row,
    owner_id: str,
    project_id: str,
) -> sqlite3.Row:
    contract = get_default_contract(plan_row["id"], db, owner_id, project_id)
    if contract:
        return contract

    has_contract = db.execute("SELECT 1 FROM plan_contracts WHERE plan_id = ?", (plan_row["id"],)).fetchone()
    if has_contract:
        raise HTTPException(403, "越权访问计划项目")

    payload = json.loads(plan_row["payload"])
    product_version_id = create_product_version(
        plan_row["product_asset_id"],
        owner_id,
        project_id,
        plan_contract_payload_hash(payload)[1],
        db,
    )
    upsert_plan_contract(plan_row["id"], product_version_id, payload, db=db)
    contract = get_default_contract(plan_row["id"], db, owner_id, project_id)
    if not contract:
        raise HTTPException(409, "计划合同创建失败")
    return contract


def append_job_event(db: sqlite3.Connection, job_id: str, event_type: str, payload: dict | None = None) -> None:
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return
    latest = db.execute("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM job_events WHERE job_id = ?", (job_id,)).fetchone()
    sequence = int(latest["sequence"]) + 1
    event_payload = payload or {}
    db.execute(
        """
        INSERT INTO job_events
        (id, job_id, sequence, event_type, status, stage, progress, payload, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            job_id,
            sequence,
            event_type,
            job["status"],
            job["stage"],
            job["progress"],
            json.dumps(event_payload, ensure_ascii=False),
            utc_now(),
        ),
    )


def latest_run_job(db: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT *
        FROM run_jobs
        WHERE job_id = ?
        ORDER BY created_at DESC, attempt DESC
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()


def require_active_lease(db: sqlite3.Connection, job_id: str, worker_id: str, lease_epoch: int) -> sqlite3.Row:
    run_job = latest_run_job(db, job_id)
    if not run_job:
        raise HTTPException(404, "run_job 不存在")
    if run_job["lease_owner"] != worker_id or int(run_job["lease_epoch"]) != lease_epoch:
        raise HTTPException(409, "租约已过期或不是当前 worker")
    if run_job["lease_expires_at"] and parse_utc(run_job["lease_expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(409, "租约已过期")
    return run_job


def claim_job(worker_id: str, job_id: str | None = None) -> dict:
    now = utc_now()
    lease_expires_text = lease_expiry_text()
    with connect() as db:
        begin_immediate(db)
        params: list[object] = []
        filter_sql = ""
        if job_id:
            filter_sql = "AND j.id = ?"
            params.append(job_id)
        row = db.execute(
            f"""
            SELECT rj.*, j.status AS job_status
            FROM run_jobs rj
            JOIN jobs j ON j.id = rj.job_id
            WHERE j.status IN ('QUEUED', 'RUNNING')
              AND j.cancel_requested = 0
              AND (
                rj.lease_owner IS NULL
                OR rj.lease_expires_at IS NULL
                OR rj.lease_expires_at < ?
              )
              {filter_sql}
            ORDER BY j.created_at ASC
            LIMIT 1
            """,
            [now, *params],
        ).fetchone()
        if not row:
            if job_id:
                existing = db.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if not existing:
                    raise HTTPException(404, "任务不存在")
            return {"claimed": False}
        lease_epoch = int(row["lease_epoch"]) + 1
        db.execute(
            """
            UPDATE run_jobs
            SET lease_owner = ?, lease_expires_at = ?, lease_epoch = ?, status = 'RUNNING', updated_at = ?
            WHERE id = ?
            """,
            (worker_id, lease_expires_text, lease_epoch, now, row["id"]),
        )
        db.execute(
            "UPDATE job_attempts SET status = 'RUNNING', started_at = ? WHERE run_job_id = ? AND attempt = ?",
            (now, row["id"], row["attempt"]),
        )
        db.execute(
            "UPDATE jobs SET status = 'RUNNING', updated_at = ? WHERE id = ?",
            (now, row["job_id"]),
        )
        db.execute(
            "UPDATE runs SET status = 'RUNNING', updated_at = ? WHERE id = ?",
            (now, row["run_id"]),
        )
        append_job_event(
            db,
            row["job_id"],
            "worker.claimed",
            {"worker_id": worker_id, "lease_epoch": lease_epoch, "lease_expires_at": lease_expires_text},
        )
        return {
            "claimed": True,
            "job_id": row["job_id"],
            "run_id": row["run_id"],
            "run_job_id": row["id"],
            "lease_epoch": lease_epoch,
            "lease_expires_at": lease_expires_text,
        }


def heartbeat_job(job_id: str, worker_id: str, lease_epoch: int) -> dict:
    now = utc_now()
    lease_expires_text = lease_expiry_text()
    with connect() as db:
        run_job = require_active_lease(db, job_id, worker_id, lease_epoch)
        db.execute(
            "UPDATE run_jobs SET lease_expires_at = ?, updated_at = ? WHERE id = ?",
            (lease_expires_text, now, run_job["id"]),
        )
        append_job_event(
            db,
            job_id,
            "worker.heartbeat",
            {"worker_id": worker_id, "lease_epoch": lease_epoch, "lease_expires_at": lease_expires_text},
        )
    return {"ok": True, "job_id": job_id, "lease_epoch": lease_epoch, "lease_expires_at": lease_expires_text}


def complete_leased_job(job_id: str, request: WorkerCompleteRequest) -> dict:
    values: dict[str, object] = {"status": "SUCCEEDED", "stage": "ARTIFACT", "progress": 100, "error": None}
    if request.output_path:
        values["output_path"] = _storage_reference(job_id, request.output_path)
    if request.manifest_path:
        values["manifest_path"] = _storage_reference(job_id, request.manifest_path)
    with connect() as db:
        begin_immediate(db)
        run_job = require_active_lease(db, job_id, request.worker_id, request.lease_epoch)
        _apply_job_update(db, job_id, values)
        db.execute(
            "UPDATE run_jobs SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), run_job["id"]),
        )
        append_job_event(
            db,
            job_id,
            "worker.completed",
            {"worker_id": request.worker_id, "lease_epoch": request.lease_epoch},
        )
    return get_job(job_id)


def fail_leased_job(job_id: str, request: WorkerFailRequest) -> dict:
    with connect() as db:
        begin_immediate(db)
        run_job = require_active_lease(db, job_id, request.worker_id, request.lease_epoch)
        _apply_job_update(job_id=job_id, db=db, values={"status": "FAILED", "stage": "FAILED", "error": request.error[-4000:]})
        db.execute(
            "UPDATE run_jobs SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), run_job["id"]),
        )
        append_job_event(
            db,
            job_id,
            "worker.failed",
            {"worker_id": request.worker_id, "lease_epoch": request.lease_epoch, "error": request.error[-4000:]},
        )
    return get_job(job_id)


def release_worker_lease(job_id: str, worker_id: str, lease_epoch: int, event_type: str, payload: dict | None = None) -> None:
    with connect() as db:
        run_job = latest_run_job(db, job_id)
        if not run_job or run_job["lease_owner"] != worker_id or int(run_job["lease_epoch"]) != lease_epoch:
            return
        db.execute(
            "UPDATE run_jobs SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), run_job["id"]),
        )
        event_payload = {"worker_id": worker_id, "lease_epoch": lease_epoch}
        if payload:
            event_payload.update(payload)
        append_job_event(db, job_id, event_type, event_payload)


def is_lease_conflict(exc: BaseException) -> bool:
    """识别“租约已不属于当前 worker”的错误，避免接管后仍改写任务状态。"""
    return isinstance(exc, HTTPException) and exc.status_code in {404, 409}


class HeartbeatKeeper(threading.Thread):
    """长任务执行期间的续租线程。

    真实 Blender/FFmpeg 渲染可能远超 LEASE_SECONDS；没有周期性心跳时，
    其他 worker 会在渲染中途合法抢走租约，导致同一任务被执行两次。
    """

    def __init__(self, job_id: str, worker_id: str, lease_epoch: int, interval: float | None = None) -> None:
        super().__init__(name=f"heartbeat-{job_id}", daemon=True)
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_epoch = lease_epoch
        self.interval = float(interval if interval is not None else HEARTBEAT_SECONDS)
        self.lease_lost = threading.Event()
        self.last_error: str | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            try:
                heartbeat_job(self.job_id, self.worker_id, self.lease_epoch)
            except Exception as exc:  # noqa: BLE001 - 续租失败必须停止而不是重试抢占
                self.last_error = str(exc)
                self.lease_lost.set()
                return

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=5)


def reconcile_stale_jobs() -> dict:
    now = utc_now()
    reconciled: list[str] = []
    with connect() as db:
        rows = db.execute(
            """
            SELECT rj.*
            FROM run_jobs rj
            JOIN jobs j ON j.id = rj.job_id
            WHERE j.status = 'RUNNING'
              AND rj.lease_owner IS NOT NULL
              AND rj.lease_expires_at IS NOT NULL
              AND rj.lease_expires_at < ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            db.execute(
                """
                UPDATE run_jobs
                SET lease_owner = NULL, lease_expires_at = NULL, status = 'QUEUED', updated_at = ?
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            db.execute("UPDATE jobs SET status = 'QUEUED', updated_at = ? WHERE id = ?", (now, row["job_id"]))
            db.execute("UPDATE runs SET status = 'QUEUED', updated_at = ? WHERE id = ?", (now, row["run_id"]))
            append_job_event(
                db,
                row["job_id"],
                "worker.reconciled",
                {
                    "previous_worker_id": row["lease_owner"],
                    "lease_epoch": row["lease_epoch"],
                    "lease_expires_at": row["lease_expires_at"],
                },
            )
            reconciled.append(row["job_id"])
    return {"reconciled": len(reconciled), "job_ids": reconciled}


def _apply_job_update(db: sqlite3.Connection, job_id: str, values: dict) -> None:
    """把一次任务字段变更写入 jobs/runs/run_jobs/job_attempts 与事件表。

    该函数只负责写入，事务由调用方控制，便于在租约校验后原子落库。
    """
    values = dict(values)
    reopen = bool(values.pop("_reopen_terminal", False))
    values.pop("updated_at", None)
    current = db.execute(
        "SELECT status, cancel_requested FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if current is None:
        raise HTTPException(404, "任务不存在")
    if not reopen:
        if current["status"] in TERMINAL_JOB_STATUSES:
            # 终态是最终事实：迟到的心跳/进度/完成回报不能把任务改回进行中。
            values = {
                key: value
                for key, value in values.items()
                if key not in {"status", "stage", "progress", "cancel_requested"}
            }
        if values.get("status") == "SUCCEEDED" and (
            current["cancel_requested"] or current["status"] == "CANCEL_REQUESTED"
        ):
            # 取消与完成竞争：取消胜出，不把用户已取消的任务改回成功。
            values = {
                key: value for key, value in values.items() if key not in {"output_path", "manifest_path"}
            }
            values.update({"status": "CANCELLED", "stage": "CANCELLED", "progress": 0, "error": None})
    if not values:
        return
    values["updated_at"] = utc_now()
    # 同步到 runs 的字段不能包含 error；error 仅用于 jobs / run_jobs / job_attempts
    run_updates = {k: values[k] for k in values if k in {"status", "stage", "progress"}}
    run_job_updates = {
        key: value
        for key, value in (("status", values.get("status")), ("stage", values.get("stage")), ("error", values.get("error")))
        if value is not None
    }
    status_terminal = TERMINAL_JOB_STATUSES
    latest_run_status = None
    latest_run = None
    db.execute("UPDATE jobs SET " + ", ".join(f"{key} = ?" for key in values) + " WHERE id = ?", [*values.values(), job_id])
    if run_updates:
        run_assignments = ", ".join(f"{key} = ?" for key in run_updates)
        row = db.execute(
            "SELECT run_id FROM run_jobs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row:
            latest_run = row["run_id"]
            latest_run_job = db.execute(
                """
                SELECT id AS run_job_id, status, attempt
                FROM run_jobs
                WHERE run_id = ?
                ORDER BY created_at DESC, attempt DESC
                LIMIT 1
                """,
                (latest_run,),
            ).fetchone()
            latest_run_status = latest_run_job["status"] if latest_run_job else None
            run_job = db.execute(
                """
                SELECT rj.id AS run_job_id, rj.attempt
                FROM run_jobs rj
                WHERE rj.run_id = ?
                ORDER BY rj.created_at DESC, rj.attempt DESC
                LIMIT 1
                """,
                (latest_run,),
            ).fetchone()
            now = utc_now()
            db.execute(
                f"UPDATE runs SET {run_assignments}, updated_at = ? WHERE id = ?",
                [*run_updates.values(), now, latest_run],
            )
            if run_job:
                if run_job_updates:
                    db.execute(
                        "UPDATE run_jobs SET " + ", ".join(f"{key} = ?" for key in run_job_updates) + ", updated_at = ? WHERE id = ?",
                        [*run_job_updates.values(), now, run_job["run_job_id"]],
                    )
                if "status" in run_updates:
                    status = run_updates["status"]
                    if status == "RUNNING":
                        db.execute(
                            "UPDATE job_attempts SET status = ?, completed_at = NULL WHERE run_job_id = ? AND attempt = ?",
                            ("RUNNING", run_job["run_job_id"], run_job["attempt"]),
                        )
                    elif status in status_terminal and latest_run_status not in status_terminal:
                        db.execute(
                            "UPDATE job_attempts SET status = ?, error = ?, completed_at = ? WHERE run_job_id = ? AND attempt = ?",
                            (status, run_updates.get("error"), now, run_job["run_job_id"], run_job["attempt"]),
                        )
    if "status" in values and values["status"] in status_terminal and latest_run and latest_run_status not in status_terminal:
        db.execute(
            "UPDATE runs SET attempt_count = COALESCE(attempt_count, 0) + 1 WHERE id = ?",
            (latest_run,),
        )
    append_job_event(db, job_id, "job.updated", {key: value for key, value in values.items() if key != "updated_at"})


def update_job(job_id: str, **values) -> None:
    """更新任务字段；若当前线程持有该任务的租约，则在同一写事务内校验租约。"""
    lease = CURRENT_LEASE.get()
    if lease is not None and lease.job_id == job_id:
        update_job_with_lease(job_id, lease.worker_id, lease.lease_epoch, **values)
        return
    if not values:
        return
    with connect() as db:
        _apply_job_update(db, job_id, values)


def update_job_with_lease(job_id: str, worker_id: str, lease_epoch: int, **values) -> None:
    """先在校验租约的同一事务里确认 epoch 仍有效，再落库，关闭校验与写入间的竞争窗口。"""
    if not values:
        return
    with connect() as db:
        begin_immediate(db)
        require_active_lease(db, job_id, worker_id, lease_epoch)
        _apply_job_update(db, job_id, values)


def get_job(job_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    return row_to_dict(row)


def create_run_record(
    plan_row: sqlite3.Row,
    owner_id: str,
    project_id: str,
    idempotency_key: str | None,
    request_hash: str,
) -> tuple[str, str, bool]:
    now = utc_now()
    run_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    with connect() as db:
        contract = ensure_plan_contract(db, plan_row, owner_id, project_id)
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (plan_row["product_asset_id"],)).fetchone()
        if not asset:
            raise HTTPException(409, "计划绑定的产品素材不存在")
        if idempotency_key:
            existing = db.execute(
                "SELECT id, job_id, request_hash, status FROM runs WHERE idempotency_key = ? AND plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (idempotency_key, plan_row["id"]),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(409, "同一 idempotency_key 的计划内容冲突")
                return existing["id"], existing["job_id"], False
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, NULL, NULL, NULL, 0, ?, ?)",
            (job_id, plan_row["id"], asset["id"], asset["kind"], now, now),
        )
        db.execute(
            "INSERT INTO runs (id, plan_id, plan_contract_id, owner_id, request_hash, idempotency_key, status, stage, progress, job_id, attempt_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, ?, 1, ?, ?)",
            (
                run_id,
                plan_row["id"],
                contract["id"],
                owner_id,
                request_hash,
                idempotency_key,
                job_id,
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO run_jobs (id, run_id, job_id, attempt, status, stage, created_at, updated_at, error) "
            "VALUES (?, ?, ?, 1, 'QUEUED', 'PREPARE', ?, ?, NULL)",
            (str(uuid.uuid4()), run_id, job_id, now, now),
        )
        db.execute(
            "INSERT INTO job_attempts (id, run_job_id, attempt, status, started_at, completed_at, error, metadata) "
            "VALUES (?, (SELECT id FROM run_jobs WHERE run_id = ? ORDER BY created_at DESC LIMIT 1), 1, 'CREATED', ?, NULL, NULL, NULL)",
            (str(uuid.uuid4()), run_id, now),
        )
        append_job_event(db, job_id, "job.created", {"run_id": run_id, "plan_id": plan_row["id"]})
    return run_id, job_id, True


def image_base_zoom(focal_length_mm: int) -> float:
    """Map the editable V1 focal-length field to a conservative 2D crop."""
    return round(1.05 + (focal_length_mm - 15) / 105 * 0.35, 4)


def image_shot_filter(shot: Shot, index: int, fps: int, size: str) -> str:
    """Compile one frozen shot into deterministic FFmpeg 2D camera motion.

    Image previews cannot perform a physical 3D orbit.  The orbit template
    therefore combines eased lateral and vertical parallax with a light zoom.
    GLB previews continue to use the physical Blender camera implementation.
    """
    duration = shot.duration_frames
    denominator = max(1, duration - 1)
    zoom = f"{image_base_zoom(shot.focal_length_mm):.4f}"
    center_x = "iw/2-(iw/zoom/2)"
    center_y = "ih/2-(ih/zoom/2)"
    if shot.camera == "dolly_in":
        zoom = f"{zoom}+0.16*on/{denominator}"
        x, y = center_x, center_y
    elif shot.camera == "side_track":
        zoom = f"{zoom}+0.03*on/{denominator}"
        x = f"(iw-iw/zoom)*(0.08+0.84*on/{denominator})"
        y = center_y
    elif shot.camera == "hero_orbit":
        zoom = f"{zoom}+0.08*sin(PI*on/{denominator})"
        x = f"(iw-iw/zoom)*(0.50+0.32*sin(PI*(on/{denominator}-0.5)))"
        y = f"(ih-ih/zoom)*(0.50+0.07*sin(2*PI*on/{denominator}))"
    else:  # static
        x, y = center_x, center_y
    return (
        f"[source_{index}]zoompan=z='{zoom}':x='{x}':y='{y}':"
        f"d={duration}:s={size}:fps={fps},trim=end_frame={duration},"
        f"setpts=PTS-STARTPTS[shot_{index}]"
    )


def build_image_filtergraph(plan_snapshot: dict, output: OutputSpec) -> str:
    """Turn the persisted DirectorPlan snapshot into a fixed 144-frame edit."""
    validated = PlanUpdate.model_validate(
        {
            "intent": plan_snapshot.get("intent"),
            "shots": plan_snapshot.get("shots"),
            "crop_anchor": plan_snapshot.get("crop_anchor", CropAnchor.center.value),
            "duration_seconds": (plan_snapshot.get("output") or {}).get("duration_seconds", 6),
        }
    )
    sources = "".join(f"[source_{index}]" for index in range(3))
    preview_width = int(round(output.width * 64 / 27))
    preview_height = int(round(output.height * 64 / 27))
    # 横向素材在 cover 缩放后会比画布宽，纵向素材则会比画布高；
    # 锚点决定先保留源素材的哪一部分，之后的镜头运动会在这个区域内进行。
    anchor = validated.crop_anchor.value
    offset_x, offset_y = CROP_ANCHOR_OFFSETS.get(anchor, CROP_ANCHOR_OFFSETS["center"])
    filters = [
        f"[0:v]scale={preview_width}:{preview_height}:force_original_aspect_ratio=increase,"
        f"crop={preview_width}:{preview_height}:{offset_x}:{offset_y},split=3" + sources,
    ]
    filters.extend(image_shot_filter(shot, index, output.fps, f"{output.width}x{output.height}") for index, shot in enumerate(validated.shots))
    filters.append("[shot_0][shot_1][shot_2]concat=n=3:v=1:a=0,format=yuv420p[video]")
    return ";".join(filters)


def parse_r_frame_rate(value: str) -> float:
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            if not numerator or not denominator:
                return 0.0
            return float(numerator) / float(denominator)
        return float(value)
    except ValueError:
        return 0.0
    except ZeroDivisionError:
        return 0.0


def qa_sample_frames(plan_snapshot: dict, frame_count: int) -> list[int]:
    """按冻结计划的三段镜头边界取检查帧：每段首帧 + 整片末帧。"""
    frames = {1}
    total = 0
    for shot in plan_snapshot.get("shots", []):
        try:
            total += int(shot.get("duration_frames", 0))
        except (TypeError, ValueError):
            continue
        frames.add(min(frame_count, max(1, total)))
    frames.add(min(frame_count, max(1, total or frame_count)))
    return sorted(frames)


def measure_black_ratio(video: Path, duration: float) -> float:
    """用 ffmpeg blackdetect 统计黑屏时长占比。"""
    if not FFMPEG or duration <= 0:
        return 0.0
    result = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(video), "-vf", "blackdetect=d=0.05:pix_th=0.10",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    black_seconds = 0.0
    for line in result.stderr.splitlines():
        match = re.search(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)", line)
        if match:
            black_seconds += max(0.0, float(match.group(2)) - float(match.group(1)))
    return min(1.0, black_seconds / duration)


def media_quality_report(video: Path, plan_snapshot: dict, output_spec: OutputSpec, workdir: Path) -> dict:
    """A06 质量门：黑帧、近乎单色（不可见）与整体黑屏占比检查。

    只拦截明显故障（全黑/几乎无内容），不替代人工审美与产品保真审核。
    """
    if not FFMPEG:
        raise RuntimeError("FFmpeg 未安装或未找到")
    workdir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    samples: list[dict] = []
    for frame in qa_sample_frames(plan_snapshot, output_spec.frame_count):
        target = workdir / f"qa-{frame:04d}.png"
        result = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", str(video),
             "-vf", f"select=eq(n\\,{frame - 1})", "-frames:v", "1", str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not target.exists():
            failures.append(f"帧 {frame} 无法解码: {result.stderr[-200:]}")
            continue
        with Image.open(target) as image:
            grey = image.convert("L")
            stats = ImageStat.Stat(grey)
            mean = float(stats.mean[0])
            stddev = float(stats.stddev[0])
            histogram = grey.histogram()
            total_pixels = sum(histogram) or 1
            black_ratio = sum(histogram[:QA_BLACK_PIXEL_LEVEL]) / total_pixels
        samples.append({
            "frame": frame,
            "mean": round(mean, 2),
            "stddev": round(stddev, 2),
            "black_pixel_ratio": round(black_ratio, 4),
        })
        if stddev < QA_MIN_STDDEV:
            failures.append(f"帧 {frame} 近乎单色，可能不可见 (stddev={stddev:.2f})")
        if mean < QA_MIN_MEAN:
            failures.append(f"帧 {frame} 接近全黑 (mean={mean:.2f})")

    duration = float(output_spec.duration_seconds)
    black_ratio = measure_black_ratio(video, duration)
    if black_ratio > QA_MAX_BLACK_RATIO:
        failures.append(f"黑屏时长占比过高 ({black_ratio:.2%})")
    return {
        "passed": not failures,
        "failures": failures,
        "samples": samples,
        "black_seconds_ratio": round(black_ratio, 4),
        "thresholds": {
            "min_stddev": QA_MIN_STDDEV,
            "min_mean": QA_MIN_MEAN,
            "max_black_ratio": QA_MAX_BLACK_RATIO,
        },
    }


def render_image_job(job_id: str, asset: dict, output: Path, plan_snapshot: dict, output_spec: OutputSpec) -> None:
    if not FFMPEG:
        raise RuntimeError("FFmpeg 未安装或未找到")
    input_path = Path(asset["path"])
    filtergraph = build_image_filtergraph(plan_snapshot, output_spec)
    command = [
        FFMPEG,
        "-y",
        "-loop", "1",
        "-framerate", "1",
        "-i", str(input_path),
        "-filter_complex", filtergraph,
        "-map", "[video]",
        "-frames:v", str(output_spec.frame_count),
        "-r", str(output_spec.fps),
        "-s", f"{output_spec.width}x{output_spec.height}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-movflags", "+faststart",
        str(output),
    ]
    update_job(job_id, status="RUNNING", stage="PREVIZ", progress=22)
    with process_lock:
        processes[job_id] = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process = processes[job_id]
    _, stderr = process.communicate()
    with process_lock:
        processes.pop(job_id, None)
    if get_job(job_id)["cancel_requested"]:
        update_job(job_id, status="CANCELLED", stage="PREVIZ", progress=0)
        return
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace")[-2000:])


def render_glb_job(job_id: str, asset: dict, run_dir: Path, output: Path, plan_path: Path, output_spec: OutputSpec) -> None:
    if not BLENDER:
        raise RuntimeError("Blender 未安装或未找到")
    if not FFMPEG:
        raise RuntimeError("FFmpeg 未安装或未找到")
    frames = run_dir / "frames"
    frames.mkdir(exist_ok=True)
    command = [
        BLENDER,
        "--background",
        "--python", str(BLENDER_SCRIPT),
        "--",
        "--input", asset["path"],
        "--output", str(frames),
        "--width", str(output_spec.width),
        "--height", str(output_spec.height),
        "--frames", str(output_spec.frame_count),
        "--plan", str(plan_path),
    ]
    update_job(job_id, status="RUNNING", stage="RENDER", progress=12)
    with process_lock:
        processes[job_id] = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        process = processes[job_id]
    rendered = 0
    output_lines: list[str] = []
    assert process.stdout
    for line in process.stdout:
        output_lines.append(line)
        if "Saved:" in line or "Time:" in line and "Rendering" not in line:
            rendered = min(output_spec.frame_count, rendered + 1)
            if rendered % 4 == 0:
                update_job(job_id, progress=12 + int(rendered / output_spec.frame_count * 70))
        if get_job(job_id)["cancel_requested"]:
            process.terminate()
            break
    process.wait()
    with process_lock:
        processes.pop(job_id, None)
    if get_job(job_id)["cancel_requested"]:
        update_job(job_id, status="CANCELLED", stage="RENDER", progress=0)
        return
    if process.returncode != 0:
        raise RuntimeError("".join(output_lines[-80:])[-4000:])
    update_job(job_id, stage="ENCODE", progress=86)
    encode = subprocess.run(
        [
            FFMPEG, "-y", "-framerate", "24",
            "-i", str(frames / "frame_%04d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output),
        ],
        capture_output=True,
        text=True,
    )
    if encode.returncode != 0:
        raise RuntimeError(encode.stderr[-2000:])
def execute_claimed_job(job_id: str, worker_id: str, lease_epoch: int) -> None:
    lease = LeaseContext(job_id=job_id, worker_id=worker_id, lease_epoch=lease_epoch)
    keeper = HeartbeatKeeper(job_id, worker_id, lease_epoch)
    lease_token = CURRENT_LEASE.set(lease)
    try:
        with connect() as db:
            require_active_lease(db, job_id, worker_id, lease_epoch)
        keeper.start()
        job = get_job(job_id)
        with connect() as db:
            asset_row = db.execute("SELECT * FROM assets WHERE id = ?", (job["asset_id"],)).fetchone()
            plan_row = db.execute("SELECT * FROM plans WHERE id = ?", (job["plan_id"],)).fetchone()
        if not asset_row or not plan_row:
            raise RuntimeError("任务输入不存在")
        asset = row_to_dict(asset_row)
        asset["path"] = str(resolve_asset_path(asset))
        run_dir = RUNS / job_id
        # 先确认磁盘写得出，再启动真实渲染，避免产出半截帧序列或空视频。
        ensure_disk_space(run_dir)
        plan_path = run_dir / "director_plan.json"
        try:
            plan_snapshot_bytes = plan_path.read_bytes()
            plan_snapshot = json.loads(plan_snapshot_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"任务缺少有效的 DirectorPlan 快照: {exc}") from exc
        output_spec = OutputSpec.model_validate(plan_snapshot.get("output", {}))
        output = run_dir / "preview.mp4"
        if asset["kind"] == "model":
            render_glb_job(job_id, asset, run_dir, output, plan_path, output_spec)
        else:
            render_image_job(job_id, asset, output, plan_snapshot, output_spec)
        if get_job(job_id)["status"] == "CANCELLED":
            return
        update_job(job_id, stage="QA", progress=94)
        if not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError("输出视频缺失或无效")
        if not FFPROBE:
            raise RuntimeError("ffprobe 未安装或未找到")
        probe_result = subprocess.run(
            [
                FFPROBE, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,nb_frames:format=duration",
                "-of", "json", str(output),
            ],
            capture_output=True,
            text=True,
        )
        if probe_result.returncode != 0:
            raise RuntimeError(probe_result.stderr[-2000:])
        probe = json.loads(probe_result.stdout)
        stream = probe.get("streams", [{}])[0]
        duration = float(probe.get("format", {}).get("duration", 0))
        framerate = parse_r_frame_rate(stream.get("r_frame_rate", f"{output_spec.fps}/1"))
        frame_count = int(stream.get("nb_frames", output_spec.frame_count))
        if stream.get("width") != output_spec.width or stream.get("height") != output_spec.height:
            raise RuntimeError(f"视频技术检查失败: {stream.get('width')}x{stream.get('height')}, {duration:.3f}s")
        if abs(framerate - output_spec.fps) > 0.05 or frame_count != output_spec.frame_count:
            raise RuntimeError(
                f"视频技术检查失败: fps={framerate}, frames={frame_count}, "
                f"duration={duration:.3f}s"
            )
        expected_duration = float(output_spec.duration_seconds)
        if abs(duration - expected_duration) > 0.2:
            raise RuntimeError(
                f"视频技术检查失败: 时长 {duration:.3f}s 与计划要求的 {expected_duration:.1f}s 不符"
            )
        qa_report = media_quality_report(output, plan_snapshot, output_spec, run_dir / "qa")
        if not qa_report["passed"]:
            raise RuntimeError("媒体质量检查失败: " + "；".join(qa_report["failures"]))
        try:
            director_plan_3d = director_plan.to_target_document(plan_snapshot)
            director_plan_3d_error = None
        except director_plan.TargetContractError as exc:
            # 运行时支持但目标合同尚未放开的值：如实记录，不输出不合规文档。
            director_plan_3d = None
            director_plan_3d_error = str(exc)
        manifest = {
            "schema_version": "1.0",
            "job_id": job_id,
            "plan_id": job["plan_id"],
            "asset_id": asset["id"],
            "asset_sha256": asset["sha256"],
            "preview_kind": "BLENDER_3D" if asset["kind"] == "model" else "IMAGE_2D",
            "width": output_spec.width,
            "height": output_spec.height,
            "fps": output_spec.fps,
            "frame_count": output_spec.frame_count,
            "duration_seconds": output_spec.duration_seconds,
            "ffprobe": {"duration": duration, "video_stream": stream},
            "qa": qa_report,
            "director_plan": plan_snapshot,
            # 目标 3D 合同视图：把运行时快照映射成 contracts/director-plan.v1.schema.json 的形状
            "director_plan_3d": director_plan_3d,
            "director_plan_3d_error": director_plan_3d_error,
            "director_plan_snapshot_sha256": hashlib.sha256(plan_snapshot_bytes).hexdigest(),
            "blender": Path(BLENDER).name if BLENDER else None,
            "ffmpeg": Path(FFMPEG).name if FFMPEG else None,
            "created_at": utc_now(),
        }
        manifest_path = run_dir / "metadata.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        update_job(
            job_id,
            status="SUCCEEDED",
            stage="ARTIFACT",
            progress=100,
            output_path=_storage_reference(job_id, str(output)),
            manifest_path=_storage_reference(job_id, str(manifest_path)),
            error=None,
        )
        release_worker_lease(job_id, worker_id, lease_epoch, "worker.completed")
    except Exception as exc:
        if is_lease_conflict(exc):
            # 租约已被其他 worker 接管：不得再改写任务状态或产物引用。
            return
        try:
            if get_job(job_id)["status"] != "CANCELLED":
                update_job(job_id, status="FAILED", stage="FAILED", error=str(exc)[-4000:])
        except HTTPException:
            pass
        release_worker_lease(job_id, worker_id, lease_epoch, "worker.failed", {"error": str(exc)[-4000:]})
    finally:
        keeper.stop()
        CURRENT_LEASE.reset(lease_token)


def execute_job(job_id: str) -> None:
    claim = claim_job("local-background", job_id)
    if not claim.get("claimed"):
        return
    execute_claimed_job(job_id, "local-background", int(claim["lease_epoch"]))


def run_worker_once(worker_id: str) -> dict:
    reconcile_stale_jobs()
    claim = claim_job(worker_id)
    if not claim.get("claimed"):
        return {"claimed": False}
    execute_claimed_job(claim["job_id"], worker_id, int(claim["lease_epoch"]))
    return {"claimed": True, "job_id": claim["job_id"], "lease_epoch": claim["lease_epoch"], "status": get_job(claim["job_id"])["status"]}


app = FastAPI(title="ProductDirectorAI V1", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    context = None
    try:
        if request.method != "OPTIONS" and not request.url.path.startswith("/internal/"):
            if not (request.url.path == "/api/v1/session" and request.method == "POST"):
                owner = security.authenticate(request, connect, ALLOWED_ORIGINS)
                context = security.current_owner.set(owner)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers={"Cache-Control": "no-store"})
    finally:
        if context is not None:
            security.current_owner.reset(context)


class SessionInput(BaseModel):
    token: str = Field(min_length=1, max_length=512)


@app.post("/api/v1/session")
def login(body: SessionInput, request: Request, response: Response):
    return security.create_session(request, response, body.token, connect, ALLOWED_ORIGINS)


@app.get("/api/v1/session")
def session_status(request: Request):
    return {"owner_id": security.current_owner.get(), "csrf_token": request.state.csrf_token}


@app.delete("/api/v1/session")
def logout(request: Request, response: Response):
    with connect() as db:
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?",
                   (security.digest(request.cookies.get(security.COOKIE, "")),))
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/v1/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": "1.0.0",
        "blender": {"available": bool(BLENDER), "path": BLENDER},
        "ffmpeg": {"available": bool(FFMPEG), "path": FFMPEG},
        "ffprobe": {"available": bool(FFPROBE), "path": FFPROBE},
        "storage": str(VAR),
    }


@app.get("/api/v1/providers/minimax/status")
def minimax_status() -> dict:
    return provider_public_status()


@app.post("/api/v1/providers/minimax/credentials")
def save_minimax_credentials(
    request: ProviderCredentialInput,
) -> dict:
    base_url = _normalize_minimax_base_url(request.base_url or MINIMAX_API_BASE_URL)
    encrypted = protect_secret(request.api_key)
    status, response = minimax_call(request.api_key, "/v1/models", base_url=base_url)
    if status != 200 or not response.get("data"):
        message = response.get("error", {}).get("message", "密钥验证失败")
        raise HTTPException(status, message)
    now = utc_now()
    message = f"认证通过，可访问 {len(response['data'])} 个模型"
    with connect() as db:
        db.execute(
            """INSERT INTO provider_credentials(provider, encrypted_key, base_url, last_status, last_message, updated_at)
               VALUES('minimax', ?, ?, 'AUTHENTICATED', ?, ?)
               ON CONFLICT(provider) DO UPDATE SET encrypted_key=excluded.encrypted_key,
               base_url=excluded.base_url, last_status=excluded.last_status,
               last_message=excluded.last_message, updated_at=excluded.updated_at""",
            (encrypted, base_url, message, now),
        )
    return provider_public_status()


@app.post("/api/v1/providers/minimax/test")
def test_minimax_generation() -> dict:
    row = provider_row()
    if not row:
        raise HTTPException(409, "请先保存 MiniMax API Key")
    api_key = unprotect_secret(row["encrypted_key"])
    base_url = row["base_url"]
    status, response = minimax_call(
        api_key,
        "/v1/chat/completions",
        {
            "model": "MiniMax-M2.7",
            "messages": [{"role": "user", "content": "只回复：连接成功"}],
            "max_completion_tokens": 64,
            "temperature": 0.1,
        },
        base_url=base_url,
    )
    now = utc_now()
    if status == 200:
        state = "GENERATION_READY"
        message = "认证与文本生成均可用"
    elif status == 429:
        state = "QUOTA_LIMITED"
        message = response.get("error", {}).get("message", "认证通过，但当前额度受限")
    else:
        state = "TEST_FAILED"
        message = response.get("error", {}).get("message", f"调用失败（HTTP {status}）")
    with connect() as db:
        db.execute(
            "UPDATE provider_credentials SET last_status = ?, last_message = ?, updated_at = ? WHERE provider = 'minimax'",
            (state, message, now),
        )
    result = provider_public_status()
    result["http_status"] = status
    return result


def provider_job_public(row) -> dict:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "operation": row["operation"],
        "external_id": row["external_id"],
        "status": row["status"],
        "stage": row["stage"],
        "artifact_asset_id": row["artifact_asset_id"],
        "artifact_sha256": row["artifact_sha256"] if "artifact_sha256" in row.keys() else None,
        "artifact_ready": bool(row["artifact_asset_id"] or (row["artifact_path"] if "artifact_path" in row.keys() else None)),
        "cancel_requested": int(row["cancel_requested"]) if "cancel_requested" in row.keys() and row["cancel_requested"] is not None else 0,
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def load_provider_job(provider_job_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM provider_jobs WHERE id = ?", (provider_job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Provider 任务不存在")
    return dict(row)


def insert_provider_job(
    provider_job_id: str, external_id: str | None, status: str, stage: str,
    payload: dict, operation: str = "RECONSTRUCT_3D",
    error: str | None = None, artifact_asset_id: str | None = None,
) -> None:
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO provider_jobs (id, provider, operation, external_id, status, stage, request_payload, "
            "artifact_asset_id, error, created_at, updated_at) "
            "VALUES (?, 'h3-comfyui', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (provider_job_id, operation, external_id, status, stage, json.dumps(payload, ensure_ascii=False),
             artifact_asset_id, error, now, now),
        )


def update_provider_job(provider_job_id: str, **values) -> None:
    if not values:
        return
    values["updated_at"] = utc_now()
    with connect() as db:
        db.execute(
            "UPDATE provider_jobs SET " + ", ".join(f"{key} = ?" for key in values) + " WHERE id = ?",
            [*values.values(), provider_job_id],
        )


def build_director_llm():
    """按配置返回 LLM 生成函数；未启用或缺凭证时返回 None（退到规则生成）。"""
    if director.provider_from_env() != "minimax":
        return None
    row = provider_row()
    if not row:
        return None
    try:
        api_key = unprotect_secret(row["encrypted_key"])
    except HTTPException:
        return None
    model = os.getenv("PRODUCTDIRECTOR_DIRECTOR_MODEL", "MiniMax-Text-01")

    def llm(intent: str, asset_kind: str) -> dict:
        status, payload = minimax_call(
            api_key,
            "/v1/chat/completions",
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": director.LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": f"产品素材类型：{asset_kind}。描述：{intent}"},
                ],
                "temperature": 0.7,
            },
            base_url=row["base_url"],
        )
        if status != 200:
            raise RuntimeError(f"LLM 返回 HTTP {status}")
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        return director.parse_llm_content(content)

    return llm


@app.post("/api/v1/director/plan")
def generate_director_plan(request: DirectorPlanRequest) -> dict:
    """描述 → 合法可编辑计划草稿。

    服务端校验是必经步骤：候选计划不合法会被修复（最多两次），
    仍不合法则退回模板。默认使用本地规则生成器，不调用收费 API。
    """
    owner_id, _, _project_id = normalize_contract_context(request.owner_id, request.project_id)
    asset_kind = request.asset_kind
    if request.product_asset_id:
        with connect() as db:
            asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.product_asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "产品素材不存在")
        if asset["owner_id"] != owner_id:
            raise HTTPException(403, "越权使用素材")
        asset_kind = asset["kind"]

    result = director.build_plan(
        request.intent,
        asset_kind,
        validator=PlanUpdate,
        llm=build_director_llm(),
        duration_seconds=request.duration_seconds,
    )
    public = result.public()
    public["asset_kind"] = asset_kind
    public["validated"] = True
    public["configured_provider"] = director.provider_from_env()
    return public


@app.get("/api/v1/providers/h3/status")
def h3_provider_status() -> dict:
    """H3/ComfyUI 可用性与队列概览（不含凭证与文件内容）。"""
    return comfyui.health()


@app.post("/api/v1/providers/h3/reconstruct", status_code=202)
def h3_reconstruct(request: H3ReconstructRequest) -> dict:
    """把一个产品图素材提交给现有 H3/ComfyUI 环境做 3D 重建。"""
    owner_id, _, _project_id = normalize_contract_context(request.owner_id, request.project_id)
    with connect() as db:
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.product_asset_id,)).fetchone()
    if not asset:
        raise HTTPException(404, "产品素材不存在")
    if asset["owner_id"] != owner_id:
        raise HTTPException(403, "越权使用素材")
    if asset["kind"] != "image":
        raise HTTPException(422, "H3 重建目前只接受图片素材")
    x, y, width, height = request.crop
    if x < 0 or y < 0 or width < 64 or height < 64:
        raise HTTPException(422, "裁切区域无效")

    path = resolve_asset_path(asset)
    provider_job_id = str(uuid.uuid4())
    payload = {
        "asset_id": asset["id"],
        "asset_sha256": asset["sha256"],
        "crop": [x, y, width, height],
        "steps": request.steps,
        "cfg": request.cfg,
        "seed": request.seed,
    }
    try:
        remote_name = comfyui.upload_image(f"{provider_job_id}{path.suffix}", path.read_bytes())
        graph = comfyui.build_hunyuan3d_graph(
            remote_name, (x, y, width, height), f"productdirector/{provider_job_id}",
            seed=request.seed, steps=request.steps, cfg=request.cfg,
        )
        external_id = comfyui.submit(graph, client_id=provider_job_id)
    except comfyui.ComfyUIError as exc:
        # 在独立事务里记录失败，避免异常回滚掉这条记录。
        insert_provider_job(provider_job_id, None, "FAILED", "SUBMIT", payload, error=str(exc)[-2000:])
        raise HTTPException(503, f"H3 Provider 提交失败: {exc}") from exc

    insert_provider_job(provider_job_id, external_id, "RUNNING", "GENERATE", payload)
    return {
        "provider_job_id": provider_job_id,
        "external_id": external_id,
        "status": "RUNNING",
        "status_url": f"/api/v1/providers/h3/jobs/{provider_job_id}",
    }


def provider_artifact_dir(provider_job_id: str) -> Path:
    folder = VAR / "providers" / provider_job_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def mark_provider_failed(provider_job_id: str, stage: str, message: str) -> dict:
    update_provider_job(provider_job_id, status="FAILED", stage=stage, error=str(message)[-2000:])
    return provider_job_public(load_provider_job(provider_job_id))


def collect_mesh_artifact(row: dict, record: dict, owner_id: str) -> dict:
    provider_job_id = row["id"]
    item = next((entry for entry in comfyui.outputs(record) if entry["filename"].lower().endswith(".glb")), None)
    if not item:
        return mark_provider_failed(provider_job_id, "COLLECT", "Provider 完成但没有 GLB 产物")
    try:
        data = comfyui.download(item)
        info = inspect_glb(data)
    except (comfyui.ComfyUIError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return mark_provider_failed(provider_job_id, "COLLECT", detail)

    asset_id = str(uuid.uuid4())
    stored = UPLOADS / f"{asset_id}.glb"
    stored.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    source_asset_id = (json.loads(row["request_payload"]) or {}).get("asset_id")
    with connect() as db:
        source = db.execute("SELECT name FROM assets WHERE id = ?", (source_asset_id,)).fetchone()
    display = f"H3 重建 · {Path(source['name']).stem if source else 'product'}.glb"
    with connect() as db:
        db.execute(
            "INSERT INTO assets (id, name, kind, mime, size_bytes, sha256, path, created_at, owner_id) "
            "VALUES (?, ?, 'model', 'model/gltf-binary', ?, ?, ?, ?, ?)",
            (asset_id, display, len(data), digest, stored.name, utc_now(), owner_id),
        )
    update_provider_job(
        provider_job_id, status="SUCCEEDED", stage="ARTIFACT",
        artifact_asset_id=asset_id, artifact_sha256=digest, error=None,
    )
    result = provider_job_public(load_provider_job(provider_job_id))
    result["artifact"] = {"kind": "model", "asset_id": asset_id, "sha256": digest, "bytes": len(data), "glb": info}
    return result


def collect_video_artifact(row: dict, record: dict, owner_id: str) -> dict:
    provider_job_id = row["id"]
    item = next(
        (entry for entry in comfyui.outputs(record)
         if entry["filename"].lower().endswith((".mp4", ".webm", ".mov"))),
        None,
    )
    if not item:
        return mark_provider_failed(provider_job_id, "COLLECT", "Provider 完成但没有视频产物")
    try:
        data = comfyui.download(item)
    except comfyui.ComfyUIError as exc:
        return mark_provider_failed(provider_job_id, "COLLECT", exc)

    target = provider_artifact_dir(provider_job_id) / Path(item["filename"]).name
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    probe: dict = {}
    if FFPROBE:
        try:
            result = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,nb_frames,codec_name:format=duration",
                 "-of", "json", str(target)],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                payload = json.loads(result.stdout)
                stream = (payload.get("streams") or [{}])[0]
                probe = {
                    "codec": stream.get("codec_name"),
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "frames": int(stream.get("nb_frames") or 0),
                    "duration": float(payload.get("format", {}).get("duration") or 0),
                }
        except (OSError, ValueError, json.JSONDecodeError):
            probe = {}
    # 视频同样回收到素材库：先落成受管素材，再登记到任务记录。
    asset_id = str(uuid.uuid4())
    stored = UPLOADS / f"{asset_id}.mp4"
    stored.write_bytes(data)
    with connect() as db:
        db.execute(
            "INSERT INTO assets (id, name, kind, mime, size_bytes, sha256, path, created_at, owner_id) "
            "VALUES (?, ?, 'video', 'video/mp4', ?, ?, ?, ?, ?)",
            (asset_id, f"H3 视频 · {provider_job_id[:8]}.mp4", len(data), digest, stored.name, utc_now(), owner_id),
        )
    update_provider_job(
        provider_job_id, status="SUCCEEDED", stage="ARTIFACT",
        artifact_path=str(target), artifact_sha256=digest,
        artifact_asset_id=asset_id, error=None,
    )
    result = provider_job_public(load_provider_job(provider_job_id))
    result["artifact"] = {
        "kind": "video",
        "filename": target.name,
        "sha256": digest,
        "bytes": len(data),
        "probe": probe,
        "asset_id": asset_id,
        "library_url": f"/api/v1/assets/{asset_id}/content",
        "download_url": f"/api/v1/providers/h3/jobs/{provider_job_id}/artifact",
    }
    return result


@app.post("/api/v1/providers/h3/video", status_code=202)
def h3_video(request: H3VideoRequest) -> dict:
    """用现有 H3 环境生成视频：参考视频 + 产品图 → 成片。"""
    owner_id, _, _project_id = normalize_contract_context(request.owner_id, request.project_id)
    with connect() as db:
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.product_asset_id,)).fetchone()
    if not asset:
        raise HTTPException(404, "产品素材不存在")
    if asset["owner_id"] != owner_id:
        raise HTTPException(403, "越权使用素材")
    if asset["kind"] != "image":
        raise HTTPException(422, "参考视频生成目前只接受图片素材作为产品输入")
    if not request.reference_video.strip():
        raise HTTPException(422, "参考视频不能为空")
    x, y, width, height = request.crop
    if x < 0 or y < 0 or width < 64 or height < 64:
        raise HTTPException(422, "裁切区域无效")

    path = resolve_asset_path(asset)
    provider_job_id = str(uuid.uuid4())
    payload = {
        "asset_id": asset["id"],
        "asset_sha256": asset["sha256"],
        "reference_video": request.reference_video,
        "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
        "crop": [x, y, width, height],
        "width": request.width,
        "height": request.height,
        "length": request.length,
        "steps": request.steps,
        "seed": request.seed,
        "with_product_views": request.with_product_views,
    }
    try:
        image_name = comfyui.upload_image(f"{provider_job_id}{path.suffix}", path.read_bytes())
        common = {
            "reference_video": request.reference_video,
            "product_image": image_name,
            "prompt": request.prompt,
            "prefix": f"productdirector/{provider_job_id}",
            "crop": (x, y, width, height),
            "width": request.width,
            "height": request.height,
            "length": request.length,
            "steps": request.steps,
            "seed": request.seed,
        }
        if request.with_product_views:
            graph = comfyui.build_product_video_graph(
                **common,
                mesh_steps=request.mesh_steps,
                mesh_octree=request.mesh_octree,
                scene=request.scene,
                motion=request.motion,
                size_cm=request.size_cm,
                seconds=request.blender_seconds,
                quality=request.quality,
                photo_texture=request.photo_texture,
                framing=request.framing,
            )
        else:
            graph = comfyui.build_h3_video_graph(**common)
        external_id = comfyui.submit(graph, client_id=provider_job_id)
    except comfyui.ComfyUIError as exc:
        insert_provider_job(provider_job_id, None, "FAILED", "SUBMIT", payload,
                            operation="GENERATE_VIDEO", error=str(exc)[-2000:])
        raise HTTPException(503, f"H3 Provider 提交失败: {exc}") from exc

    insert_provider_job(provider_job_id, external_id, "RUNNING", "GENERATE", payload, operation="GENERATE_VIDEO")
    return {
        "provider_job_id": provider_job_id,
        "external_id": external_id,
        "operation": "GENERATE_VIDEO",
        "status": "RUNNING",
        "status_url": f"/api/v1/providers/h3/jobs/{provider_job_id}",
    }


@app.get("/api/v1/providers/h3/jobs/{provider_job_id}")
def h3_job_status(
    provider_job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """查询重建进度；完成时下载 GLB、校验并登记为素材库资产。"""
    normalize_contract_context(owner_id, project_id)
    row = load_provider_job(provider_job_id)
    if row["status"] in TERMINAL_JOB_STATUSES or not row["external_id"]:
        return provider_job_public(row)

    try:
        record = comfyui.history(row["external_id"])
    except comfyui.ComfyUIError as exc:
        update_provider_job(provider_job_id, error=str(exc)[-2000:])
        raise HTTPException(503, f"无法查询 H3 Provider: {exc}") from exc

    status = comfyui.status_text(record)
    if status == "RUNNING":
        # 区分"排队中"与"正在生成"：只看 history 会把两者都当成 RUNNING。
        stage = row["stage"]
        try:
            snapshot = comfyui.queue_snapshot()
            if row["external_id"] in snapshot["running"]:
                stage = "GENERATE"
            elif row["external_id"] in snapshot["pending"]:
                stage = "QUEUED"
        except comfyui.ComfyUIError:
            stage = row["stage"]
        if stage != row["stage"]:
            update_provider_job(provider_job_id, stage=stage)
        return provider_job_public(load_provider_job(provider_job_id))
    if status == "FAILED":
        update_provider_job(provider_job_id, status="FAILED", stage="GENERATE", error="Provider 报告执行失败")
        return provider_job_public(load_provider_job(provider_job_id))

    if row["operation"] == "GENERATE_VIDEO":
        return collect_video_artifact(row, record, owner_id)
    return collect_mesh_artifact(row, record, owner_id)


@app.get("/api/v1/providers/h3/jobs")
def list_provider_jobs(limit: int = 20) -> list[dict]:
    """最近的 Provider 任务，供工作台展示与取消。"""
    capped = max(1, min(limit, 100))
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM provider_jobs ORDER BY created_at DESC LIMIT ?", (capped,)
        ).fetchall()
    return [provider_job_public(row) for row in rows]


@app.get("/api/v1/providers/h3/queue")
def h3_queue() -> dict:
    """队列与单卡产能视图：ComfyUI 全局队列 + 本项目的任务分布。"""
    try:
        snapshot = comfyui.queue_snapshot()
    except comfyui.ComfyUIError as exc:
        raise HTTPException(503, f"无法读取 H3 队列: {exc}") from exc
    with connect() as db:
        rows = db.execute(
            "SELECT id, external_id, operation, status FROM provider_jobs "
            "WHERE status = 'RUNNING' ORDER BY created_at"
        ).fetchall()
    running_ids = set(snapshot["running"])
    pending_ids = set(snapshot["pending"])
    ours = []
    for row in rows:
        state = "unknown"
        if row["external_id"] in running_ids:
            state = "running"
        elif row["external_id"] in pending_ids:
            state = "pending"
        ours.append({"provider_job_id": row["id"], "operation": row["operation"], "queue_state": state})
    return {
        "comfyui_running": len(snapshot["running"]),
        "comfyui_pending": len(snapshot["pending"]),
        "queue_depth": snapshot["depth"],
        "single_gpu_serial": True,
        "our_running_jobs": ours,
    }


@app.post("/api/v1/providers/h3/jobs/{provider_job_id}/cancel")
def h3_job_cancel(
    provider_job_id: str,
    force: bool = False,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """取消 Provider 任务。

    - 仍在排队：通过 `/queue` 精确删除该 prompt，**不影响其他任务**。
    - 已在执行：ComfyUI 只有全局 `/interrupt`，会影响该实例上正在运行的所有任务，
      因此默认拒绝并记录取消请求；私有单租户部署可显式 `?force=true` 中断。
    """
    normalize_contract_context(owner_id, project_id)
    row = load_provider_job(provider_job_id)
    if row["status"] in TERMINAL_JOB_STATUSES:
        return provider_job_public(row)
    if not row["external_id"]:
        update_provider_job(provider_job_id, status="CANCELLED", stage="SUBMIT",
                            cancel_requested=1, error="提交阶段取消")
        return provider_job_public(load_provider_job(provider_job_id))

    try:
        snapshot = comfyui.queue_snapshot()
    except comfyui.ComfyUIError as exc:
        raise HTTPException(503, f"无法读取 H3 队列: {exc}") from exc

    if row["external_id"] in snapshot["pending"]:
        try:
            removed = comfyui.delete_pending(row["external_id"])
        except comfyui.ComfyUIError as exc:
            raise HTTPException(503, f"队列删除失败: {exc}") from exc
        if not removed:
            raise HTTPException(409, "队列删除未生效，任务可能已开始执行，请重新查询状态")
        update_provider_job(provider_job_id, status="CANCELLED", stage="GENERATE",
                            cancel_requested=1, error="用户在排队阶段取消")
        return provider_job_public(load_provider_job(provider_job_id))

    if row["external_id"] in snapshot["running"]:
        if not force:
            update_provider_job(provider_job_id, cancel_requested=1)
            raise HTTPException(
                409,
                "任务已在执行。ComfyUI 的 /interrupt 是全局中断，会影响该实例上正在运行的所有任务，"
                "因此默认不代为中断；私有单租户部署可显式使用 force=true。取消请求已记录。",
            )
        try:
            comfyui.interrupt()
        except comfyui.ComfyUIError as exc:
            raise HTTPException(503, f"中断失败: {exc}") from exc
        update_provider_job(provider_job_id, status="CANCELLED", stage="GENERATE",
                            cancel_requested=1, error="用户强制中断（全局 interrupt）")
        return provider_job_public(load_provider_job(provider_job_id))

    raise HTTPException(409, "任务既不在运行也不在排队，请重新查询状态后再决定")


@app.get("/api/v1/providers/h3/jobs/{provider_job_id}/artifact")
def h3_job_artifact(
    provider_job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
):
    """下载 Provider 任务产出的视频产物。"""
    normalize_contract_context(owner_id, project_id)
    row = load_provider_job(provider_job_id)
    path = row.get("artifact_path")
    if not path:
        raise HTTPException(409, "该任务还没有可下载的产物")
    file_path = Path(path)
    if not file_path.is_file():
        raise HTTPException(410, "产物文件已不存在")
    return FileResponse(file_path, media_type="video/mp4", filename=file_path.name)


@app.post("/api/v1/assets", status_code=201)
async def create_asset(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".glb"}:
        raise HTTPException(422, "V1 支持 PNG、JPEG、WebP 和 GLB")
    data = await file.read()
    limit = 500 * 1024 * 1024 if suffix == ".glb" else 25 * 1024 * 1024
    if not data or len(data) > limit:
        raise HTTPException(422, "文件为空或超过 V1 大小限制")
    asset_id = str(uuid.uuid4())
    stored = UPLOADS / f"{asset_id}{suffix}"
    stored.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    kind = "model" if suffix == ".glb" else "image"
    glb_info = inspect_glb(data) if kind == "model" else None
    mime = mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    created = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO assets (id, name, kind, mime, size_bytes, sha256, path, created_at, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, file.filename or stored.name, kind, mime, len(data), digest, stored.name, created,
             security.current_owner.get() or DEFAULT_OWNER_ID),
        )
    return {
        "id": asset_id,
        "name": file.filename,
        "kind": kind,
        "mime": mime,
        "size_bytes": len(data),
        "sha256": digest,
        "created_at": created,
        "glb": glb_info,
    }


@app.get("/api/v1/assets")
def list_assets() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM assets WHERE owner_id = ? ORDER BY created_at DESC",
                          (security.current_owner.get() or DEFAULT_OWNER_ID,)).fetchall()
    return [{key: row[key] for key in row.keys() if key != "path"} for row in rows]


def inspect_glb(data: bytes) -> dict:
    """解析 GLB 头部与 JSON 块，拦截损坏文件与外部资源引用。

    V1 只接受自包含 GLB：单文件上传无法携带 sidecar 纹理或缓冲，
    带外部 URI 的模型在 Blender 里会渲染成无纹理表面，必须在入口拒绝。
    """
    if len(data) < 20:
        raise HTTPException(422, "GLB 文件过小或已损坏")
    magic, version, declared = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise HTTPException(422, "GLB 魔数不正确")
    if declared != len(data):
        raise HTTPException(422, f"GLB 声明长度 {declared} 与实际 {len(data)} 不一致")
    offset = 12
    document = None
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset:offset + chunk_length]
        offset += chunk_length
        if chunk_type == 0x4E4F534A:  # 'JSON'
            try:
                document = json.loads(chunk.decode("utf-8").rstrip("\x00 "))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HTTPException(422, "GLB 的 JSON 块无法解析") from None
            break
    if document is None:
        raise HTTPException(422, "GLB 缺少 JSON 块")
    external = [
        str(image.get("uri"))
        for image in document.get("images", [])
        if image.get("uri") and not str(image.get("uri")).startswith("data:")
    ]
    external += [
        str(buffer.get("uri"))
        for buffer in document.get("buffers", [])
        if buffer.get("uri") and not str(buffer.get("uri")).startswith("data:")
    ]
    if external:
        raise HTTPException(422, "GLB 引用了外部纹理/缓冲文件；V1 只支持自包含 GLB，请导出时内嵌资源")
    if not document.get("meshes"):
        raise HTTPException(422, "GLB 不包含任何网格，无法渲染")
    return {
        "version": version,
        "meshes": len(document.get("meshes", [])),
        "materials": len(document.get("materials", [])),
        "images": len(document.get("images", [])),
        "has_textures": bool(document.get("images") or document.get("textures")),
    }


def ensure_disk_space(target: Path, required_mb: float | None = None) -> float:
    """渲染前检查目标磁盘可用空间，返回可用 MB；不足时抛出可读错误。"""
    needed = MIN_FREE_DISK_MB if required_mb is None else required_mb
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return float("inf")
    free_mb = usage.free / (1024 * 1024)
    if free_mb < needed:
        raise RuntimeError(f"磁盘空间不足：可用 {free_mb:.0f} MB，低于要求的 {needed:.0f} MB")
    return free_mb


def resolve_asset_path(asset) -> Path:
    reference = Path(asset["path"])
    path = (reference if reference.is_absolute() else UPLOADS / reference).resolve()
    try:
        path.relative_to(UPLOADS.resolve())
    except ValueError:
        raise HTTPException(403, "素材路径超出授权目录") from None
    if path.name != f"{asset['id']}{path.suffix}" or not path.is_file():
        raise HTTPException(409, "素材引用无效或文件缺失")
    return path


@app.get("/api/v1/assets/{asset_id}/content")
def asset_content(asset_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "素材不存在")
    if row["owner_id"] != security.current_owner.get():
        raise HTTPException(403, "越权访问素材")
    return FileResponse(resolve_asset_path(row), media_type=row["mime"], filename=row["name"])


@app.post("/api/v1/plans/template", status_code=201)
def create_plan(request: PlanRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    plan_id = str(uuid.uuid4())
    created = utc_now()
    with connect() as db:
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.product_asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "产品素材不存在")
        if asset["owner_id"] != owner_id:
            raise HTTPException(403, "越权使用素材")
        total_frames = DEFAULT_FPS * request.duration_seconds
        # 三段均分总帧数，余数补给最后一段，保证每段 ≥24 且合计等于总帧数。
        base = total_frames // 3
        shares = [base, base, total_frames - base * 2]
        shots = [
            Shot(
                id=f"shot_0{index + 1}",
                name=["正面推近", "侧向观察", "立体环绕展示" if asset["kind"] == "model" else "细节定格"][index],
                duration_frames=max(24, shares[index]),
                camera=["dolly_in", "side_track", "hero_orbit" if asset["kind"] == "model" else "static"][index],
                focal_length_mm=35,
            )
            for index in range(3)
        ]
        payload = {
            "schema_version": "1.0",
            "product_asset_id": request.product_asset_id,
            "intent": request.intent,
            "output": request.output.model_dump(),
            "fidelity_mode": "STRICT_REQUESTED",
            "crop_anchor": request.crop_anchor.value,
            "product_pose": request.product_pose.model_dump(),
            "scene": request.scene.model_dump(),
            "shots": [shot.model_dump() for shot in shots],
        }
        _, payload_sha256 = plan_contract_payload_hash(payload)
        product_version_id = create_product_version(request.product_asset_id, owner_id, project_id, payload_sha256, db)
        db.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, 0, ?)",
            (plan_id, request.product_asset_id, request.intent, json.dumps(payload, ensure_ascii=False), created),
        )
        contract = upsert_plan_contract(plan_id, product_version_id, payload, db=db)
    return {
        "id": plan_id,
        "approved": False,
        "created_at": created,
        "contract_id": contract["contract_id"],
        "contract_version": contract["contract_version"],
        "snapshot_sha256": contract["snapshot_sha256"],
        "schema_version": "1.0",
        "product_asset_id": request.product_asset_id,
        "intent": request.intent,
        "output": request.output.model_dump(),
        "fidelity_mode": "STRICT_REQUESTED",
        "crop_anchor": request.crop_anchor.value,
        "shots": payload["shots"],
    }


@app.post("/api/v1/plans/{plan_id}/approve")
def approve_plan(plan_id: str, request: PlanApproval) -> dict:
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    with connect() as db:
        current = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not current:
            raise HTTPException(404, "计划不存在")
        contract = ensure_plan_contract(db, current, owner_id, project_id)
        db.execute("UPDATE plans SET approved = ? WHERE id = ?", (int(request.approved), plan_id))
        version = None
        if request.approved:
            # 批准计划即批准它对应的产品版本：版本从此不可改写，后续编辑生成新版本。
            version = approve_product_version_row(db, contract["product_version_id"])
    return {
        "id": plan_id,
        "approved": request.approved,
        "product_version_id": contract["product_version_id"],
        "product_version_status": version["status"] if version else None,
        "product_version_approved_at": version["approved_at"] if version else None,
    }


def approve_product_version_row(db, product_version_id: str):
    row = db.execute("SELECT * FROM product_versions WHERE id = ?", (product_version_id,)).fetchone()
    if not row:
        raise HTTPException(404, "产品版本不存在")
    if row["status"] != "APPROVED":
        db.execute(
            "UPDATE product_versions SET status = 'APPROVED', approved_at = ? WHERE id = ?",
            (utc_now(), product_version_id),
        )
    return db.execute("SELECT * FROM product_versions WHERE id = ?", (product_version_id,)).fetchone()


def product_version_public(row) -> dict:
    return {
        "id": row["id"],
        "product_asset_id": row["product_asset_id"],
        "version": row["version"],
        "status": row["status"],
        "schema_version": row["schema_version"],
        "snapshot_sha256": row["snapshot_sha256"],
        "approved_at": row["approved_at"] if "approved_at" in row.keys() else None,
        "created_at": row["created_at"],
    }


@app.get("/api/v1/product-versions")
def list_product_versions(
    product_asset_id: str | None = None,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        if product_asset_id:
            rows = db.execute(
                "SELECT * FROM product_versions WHERE product_asset_id = ? AND owner_id = ? AND project_id = ? "
                "ORDER BY version DESC",
                (product_asset_id, owner_id, project_id),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM product_versions WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC",
                (owner_id, project_id),
            ).fetchall()
    return [product_version_public(row) for row in rows]


def fidelity_policy_public(row) -> dict:
    return {
        "id": row["id"],
        "product_version_id": row["product_version_id"],
        "version": row["version"],
        "mode": row["mode"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "policy": json.loads(row["payload"]),
    }


def load_product_version_for_owner(product_version_id: str, owner_id: str, project_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM product_versions WHERE id = ?", (product_version_id,)).fetchone()
    if not row:
        raise HTTPException(404, "产品版本不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问产品版本")
    return row


@app.post("/api/v1/product-versions/{product_version_id}/fidelity-policies", status_code=201)
def create_fidelity_policy(product_version_id: str, request: FidelityPolicyRequest) -> dict:
    """为产品版本创建一个**不可改写**的保真策略版本。"""
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    policy = {
        "mode": request.mode,
        "protected_regions": [region.model_dump() for region in request.protected_regions],
        "allowed_operations": list(request.allowed_operations),
        "notes": request.notes,
    }
    payload_text = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    policy_id = str(uuid.uuid4())
    with connect() as db:
        begin_immediate(db)
        latest = db.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM fidelity_policies WHERE product_version_id = ?",
            (product_version_id,),
        ).fetchone()
        version = int(latest["version"]) + 1
        db.execute(
            "INSERT INTO fidelity_policies (id, product_version_id, version, mode, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (policy_id, product_version_id, version, request.mode, payload_text, digest, utc_now()),
        )
    with connect() as db:
        row = db.execute("SELECT * FROM fidelity_policies WHERE id = ?", (policy_id,)).fetchone()
    return fidelity_policy_public(row)


@app.get("/api/v1/product-versions/{product_version_id}/fidelity-policies")
def list_fidelity_policies(
    product_version_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM fidelity_policies WHERE product_version_id = ? ORDER BY version DESC",
            (product_version_id,),
        ).fetchall()
    return [fidelity_policy_public(row) for row in rows]


@app.post("/api/v1/product-versions/{product_version_id}/approve")
def approve_product_version(
    product_version_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """批准一个产品版本：批准后不可改写，后续变更会生成新版本。"""
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        row = db.execute("SELECT * FROM product_versions WHERE id = ?", (product_version_id,)).fetchone()
        if not row:
            raise HTTPException(404, "产品版本不存在")
        if row["owner_id"] != owner_id or row["project_id"] != project_id:
            raise HTTPException(403, "越权访问产品版本")
        approved = approve_product_version_row(db, product_version_id)
    return product_version_public(approved)


@app.patch("/api/v1/plans/{plan_id}")
def update_plan(plan_id: str, request: PlanUpdate) -> dict:
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    with connect() as db:
        current = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not current:
            raise HTTPException(404, "计划不存在")
        ensure_plan_contract(db, current, owner_id, project_id)
        payload = json.loads(current["payload"])
        payload["intent"] = request.intent
        payload["shots"] = [shot.model_dump() for shot in request.shots]
        payload["crop_anchor"] = request.crop_anchor.value
        payload["product_pose"] = request.product_pose.model_dump()
        payload["scene"] = request.scene.model_dump()
        _, payload_sha256 = plan_contract_payload_hash(payload)
        contract_version_id = create_product_version(
            current["product_asset_id"],
            owner_id,
            project_id,
            payload_sha256,
            db,
        )
        contract = upsert_plan_contract(plan_id, contract_version_id, payload, db=db)
        ensure_plan_contract(db, current, owner_id, project_id)
        return_data = {
            "id": plan_id,
            "approved": False,
            "created_at": current["created_at"],
            "contract_id": contract["contract_id"],
            "contract_version": contract["contract_version"],
            "snapshot_sha256": contract["snapshot_sha256"],
            **payload,
        }
    return return_data


@app.post("/api/v1/runs", status_code=202)
def create_run(request: RunRequest, background: BackgroundTasks) -> dict:
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    with connect() as db:
        plan = db.execute("SELECT * FROM plans WHERE id = ?", (request.plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "计划不存在")
        if not plan["approved"]:
            raise HTTPException(409, "请先确认分镜计划")
        plan_snapshot = json.loads(plan["payload"])
        try:
            PlanUpdate.model_validate(
                {
                    "intent": plan_snapshot["intent"],
                    "shots": plan_snapshot["shots"],
                    "crop_anchor": plan_snapshot.get("crop_anchor", CropAnchor.center.value),
                    "duration_seconds": (plan_snapshot.get("output") or {}).get("duration_seconds", 6),
                }
            )
        except (KeyError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(409, f"计划快照无效，无法启动任务: {exc}") from exc
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (plan["product_asset_id"],)).fetchone()
        if not asset:
            raise HTTPException(409, "计划绑定的产品素材不存在")
        run_hash = plan_contract_payload_hash(json.loads(plan["payload"]))[1]
        run_id, job_id, created_new = create_run_record(
            plan,
            owner_id,
            project_id,
            request.idempotency_key,
            run_hash,
        )
        if created_new:
            run_dir = RUNS / job_id
            run_dir.mkdir(parents=True, exist_ok=False)
            (run_dir / "director_plan.json").write_text(
                json.dumps(plan_snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    if created_new and not INLINE_EXECUTOR_DISABLED:
        background.add_task(execute_job, job_id)
    return {
        "job_id": job_id,
        "run_id": run_id,
        "status": "QUEUED",
        "status_url": f"/api/v1/jobs/{job_id}",
        "reused_idempotent": not created_new,
        "created": created_new,
    }


@app.get("/api/v1/jobs")
def list_jobs(owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            """
            SELECT j.*
            FROM jobs j
            INNER JOIN runs r ON r.job_id = j.id
            INNER JOIN plan_contracts pc ON pc.id = r.plan_contract_id
            INNER JOIN product_versions pv ON pv.id = pc.product_version_id
            WHERE r.owner_id = ? AND pv.project_id = ?
            GROUP BY j.id
            ORDER BY j.created_at DESC
            """,
            (owner_id, project_id),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.get("/api/v1/jobs/{job_id}")
def job_detail(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    return get_job(job_id)


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    job = get_job(job_id)
    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        return job
    update_job(job_id, cancel_requested=1, status="CANCEL_REQUESTED")
    with process_lock:
        process = processes.get(job_id)
        if process and process.poll() is None:
            process.terminate()
    return get_job(job_id)


@app.post("/api/v1/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    background: BackgroundTasks,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """失败或已取消的任务重新入队为新 attempt。

    保留原 run 与历史 attempt，只新增一次尝试并把任务退回 QUEUED；
    不复用上一次的产物引用，避免把旧成片当成新结果。
    """
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    now = utc_now()
    with connect() as db:
        begin_immediate(db)
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "任务不存在")
        if job["status"] not in {"FAILED", "CANCELLED", "CANCEL_REQUESTED"}:
            raise HTTPException(409, "只有失败或已取消的任务可以重试")
        run_job = latest_run_job(db, job_id)
        if not run_job:
            raise HTTPException(409, "任务缺少运行记录，无法重试")
        attempt = int(run_job["attempt"]) + 1
        db.execute(
            "INSERT INTO job_attempts (id, run_job_id, attempt, status, started_at, completed_at, error, metadata) "
            "VALUES (?, ?, ?, 'CREATED', ?, NULL, NULL, NULL)",
            (str(uuid.uuid4()), run_job["id"], attempt, now),
        )
        db.execute(
            """
            UPDATE run_jobs
            SET attempt = ?, status = 'QUEUED', stage = 'QUEUED', error = NULL,
                lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (attempt, now, run_job["id"]),
        )
        db.execute(
            """
            UPDATE jobs
            SET status = 'QUEUED', stage = 'QUEUED', progress = 0, error = NULL,
                cancel_requested = 0, output_path = NULL, manifest_path = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, job_id),
        )
        db.execute(
            """
            UPDATE runs
            SET status = 'QUEUED', stage = 'QUEUED', progress = 0,
                attempt_count = COALESCE(attempt_count, 0) + 1, updated_at = ?
            WHERE id = ?
            """,
            (now, run_job["run_id"]),
        )
        append_job_event(
            db,
            job_id,
            "job.retry_requested",
            {"attempt": attempt, "previous_status": job["status"]},
        )
    background.add_task(execute_job, job_id)
    return {
        "job_id": job_id,
        "run_id": run_job["run_id"],
        "status": "QUEUED",
        "attempt": attempt,
        "status_url": f"/api/v1/jobs/{job_id}",
    }


ARTIFACT_UPLOADS = {
    # 远程 Worker 只允许回传这两类文件，且文件名固定，避免任意写入。
    "video": ("preview.mp4", 400 * 1024 * 1024),
    "manifest": ("metadata.json", 8 * 1024 * 1024),
}


def _worker_job_and_plan(job_id: str) -> tuple[dict, str, dict]:
    job = get_job(job_id)
    run_dir = RUNS / job_id
    plan_path = run_dir / "director_plan.json"
    try:
        plan_snapshot = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, f"任务缺少有效的 DirectorPlan 快照: {exc}") from None
    return job, plan_path.read_text(encoding="utf-8"), plan_snapshot


@app.get("/internal/v1/workers/jobs/{job_id}/input", dependencies=[Depends(_ensure_worker_token)])
def worker_job_input(job_id: str, worker_id: str, lease_epoch: int) -> dict:
    """远程 Worker 取任务输入：冻结计划 + 素材下载地址（不暴露文件系统路径）。"""
    security.check_worker_id(worker_id)
    with connect() as db:
        require_active_lease(db, job_id, worker_id, lease_epoch)
    job, _plan_text, plan_snapshot = _worker_job_and_plan(job_id)
    with connect() as db:
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (job["asset_id"],)).fetchone()
    if not asset:
        raise HTTPException(409, "任务输入素材不存在")
    return {
        "job_id": job_id,
        "asset": {
            "id": asset["id"],
            "name": asset["name"],
            "kind": asset["kind"],
            "mime": asset["mime"],
            "sha256": asset["sha256"],
            "size_bytes": asset["size_bytes"],
            "download_url": f"/internal/v1/workers/jobs/{job_id}/input/asset",
        },
        "plan": plan_snapshot,
        "output": plan_snapshot.get("output", {}),
        "lease_epoch": lease_epoch,
    }


@app.get("/internal/v1/workers/jobs/{job_id}/input/asset", dependencies=[Depends(_ensure_worker_token)])
def worker_job_asset(job_id: str, worker_id: str, lease_epoch: int):
    security.check_worker_id(worker_id)
    with connect() as db:
        require_active_lease(db, job_id, worker_id, lease_epoch)
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "任务不存在")
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (job["asset_id"],)).fetchone()
    if not asset:
        raise HTTPException(409, "任务输入素材不存在")
    path = resolve_asset_path(asset)
    return FileResponse(path, media_type=asset["mime"], filename=asset["name"])


@app.post("/internal/v1/workers/jobs/{job_id}/artifact", dependencies=[Depends(_ensure_worker_token)])
async def worker_job_artifact(
    job_id: str,
    worker_id: str = Form(...),
    lease_epoch: int = Form(...),
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """远程 Worker 回传产物：文件名白名单 + 大小上限 + 只允许写进该任务的目录。"""
    security.check_worker_id(worker_id)
    if kind not in ARTIFACT_UPLOADS:
        raise HTTPException(422, f"不支持的产物类型: {kind}")
    expected_name, limit = ARTIFACT_UPLOADS[kind]
    with connect() as db:
        require_active_lease(db, job_id, worker_id, lease_epoch)
    data = await file.read()
    if not data:
        raise HTTPException(422, "产物为空")
    if len(data) > limit:
        raise HTTPException(413, f"产物超过上限（{limit // (1024 * 1024)} MB）")
    run_dir = (RUNS / job_id).resolve()
    target = (run_dir / expected_name).resolve()
    if run_dir not in target.parents:
        raise HTTPException(403, "产物路径不在授权目录内")
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "job_id": job_id,
        "kind": kind,
        "filename": expected_name,
        "bytes": len(data),
        "sha256": digest,
        "storage_reference": _storage_reference(job_id, str(target)),
    }


@app.post("/internal/v1/workers/claim", dependencies=[Depends(_ensure_worker_token)])
def worker_claim(request: WorkerClaimRequest) -> dict:
    security.check_worker_id(request.worker_id)
    return claim_job(request.worker_id, request.job_id)


@app.post("/internal/v1/workers/reconcile", dependencies=[Depends(_ensure_worker_token)])
def worker_reconcile() -> dict:
    return reconcile_stale_jobs()


@app.post("/internal/v1/workers/jobs/{job_id}/heartbeat", dependencies=[Depends(_ensure_worker_token)])
def worker_heartbeat(job_id: str, request: WorkerLeaseRequest) -> dict:
    security.check_worker_id(request.worker_id)
    return heartbeat_job(job_id, request.worker_id, request.lease_epoch)


@app.post("/internal/v1/workers/jobs/{job_id}/complete", dependencies=[Depends(_ensure_worker_token)])
def worker_complete(job_id: str, request: WorkerCompleteRequest) -> dict:
    security.check_worker_id(request.worker_id)
    return complete_leased_job(job_id, request)


@app.post("/internal/v1/workers/jobs/{job_id}/fail", dependencies=[Depends(_ensure_worker_token)])
def worker_fail(job_id: str, request: WorkerFailRequest) -> dict:
    security.check_worker_id(request.worker_id)
    return fail_leased_job(job_id, request)


@app.get("/api/v1/jobs/{job_id}/events")
def job_events(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    try:
        after_sequence = int(last_event_id) if last_event_id else 0
    except ValueError:
        after_sequence = 0
    with connect() as db:
        rows = db.execute(
            """
            SELECT *
            FROM job_events
            WHERE job_id = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (job_id, after_sequence),
        ).fetchall()

    def stream():
        for row in rows:
            payload = {
                "event_id": row["sequence"],
                "job_id": row["job_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "state": row["status"],
                "stage": row["stage"],
                "progress": row["progress"],
                "occurred_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            yield f"id: {row['sequence']}\nevent: {row['event_type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/v1/jobs/{job_id}/video")
def job_video(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
):
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    job = get_job(job_id)
    if job["status"] != "SUCCEEDED" or not job["output_path"]:
        raise HTTPException(409, "视频尚未准备完成")
    return FileResponse(
        _resolve_job_artifact_path(job_id, job["output_path"]),
        media_type="video/mp4",
        filename=f"product-preview-{job_id}.mp4",
    )


@app.get("/api/v1/jobs/{job_id}/manifest")
def job_manifest(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
):
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    job = get_job(job_id)
    if job["status"] != "SUCCEEDED" or not job["manifest_path"]:
        raise HTTPException(409, "清单尚未准备完成")
    return FileResponse(
        _resolve_job_artifact_path(job_id, job["manifest_path"]),
        media_type="application/json",
        filename=f"metadata-{job_id}.json",
    )


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="productdirector-api")
    subparsers = parser.add_subparsers(dest="command")
    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--worker-id", default=f"worker-{os.getpid()}")
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.command != "worker":
        parser.print_help()
        return 2
    while True:
        result = run_worker_once(args.worker_id)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.once:
            return 0
        time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
