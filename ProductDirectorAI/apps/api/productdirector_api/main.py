from __future__ import annotations

import argparse
import contextvars
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import socket
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
from pydantic import BaseModel, Field, ValidationError, model_validator
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageStat
from . import director, director_plan, security, storage
from .director_plan import CameraPath, ProductPose, SceneSpec, Vector3
from .providers import comfyui
from . import scene_generation
from .strict_background import (
    BackgroundProducerError,
    MODE_CONTROLLED_IMPORT,
    MODE_INDEPENDENT_WORKFLOW,
    default_controlled_source_root,
    load_background_evidence,
    run_controlled_background_workflow,
    verify_background_evidence_files,
    verify_evidence_binding,
    verify_workflow_contract,
    write_background_evidence,
)
from . import strict_qa
from . import audio_post
from . import batch as batch_rules
from . import interaction_geometry
from . import interaction_validation
from . import automation
from . import webhooks
from . import publishing
from . import publishing_connectors
from . import roles
from . import ledger
from . import localization
from . import platform_profiles
from . import publish_package
from . import reference_analysis


ROOT = Path(__file__).resolve().parents[3]
VAR = ROOT / "var"
UPLOADS = VAR / "uploads"
RUNS = VAR / "runs"
DB_PATH = VAR / "productdirector.db"
BLENDER_SCRIPT = ROOT / "blender" / "scripts" / "render_product.py"
FIDELITY_PASS_VALIDATOR = ROOT / "blender" / "scripts" / "validate_fidelity_passes.py"
STRICT_COMPOSITE = ROOT / "scripts" / "strict_composite.py"
STRICT_SOURCE_VALIDATOR = ROOT / "scripts" / "validate_strict_sources.py"
STRICT_VERIFIED_INPUT_TRUST = "VERIFIED_RENDER_SOURCE"
CONTROLLED_BLENDER_PRODUCT_TRUST = "CONTROLLED_BLENDER_PRODUCT"
CONTROLLED_RENDER_EVIDENCE_FILE = "controlled_render_evidence.json"
STRICT_COLOR_CONTRACT = {
    "product_color_space": "display-srgb",
    "background_color_space": "display-srgb",
    "layer_color_space": "display-srgb",
    "working_space": "display-linear",
    "output_color_space": "display-srgb",
    "blender_view_transform": "AgX",
}

DEFAULT_OWNER_ID = "owner-default"
DEFAULT_WORKSPACE_ID = "workspace-default"
DEFAULT_PROJECT_ID = "project-default"
LEASE_SECONDS = 60
VERIFICATION_PASSED = "VERIFICATION_PASSED"
TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "QA_REJECTED", VERIFICATION_PASSED}
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
              product_review_id TEXT,
              product_review_sha256 TEXT,
              fidelity_policy_id TEXT,
              fidelity_policy_version INTEGER,
              fidelity_policy_sha256 TEXT,
              fidelity_snapshot_json TEXT,
              strict_source_manifest_json TEXT,
              strict_source_manifest_sha256 TEXT,
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
            CREATE TABLE IF NOT EXISTS product_reviews (
              id TEXT PRIMARY KEY,
              product_version_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              reviewer TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(product_version_id) REFERENCES product_versions(id)
            );
            CREATE TABLE IF NOT EXISTS qa_threshold_sets (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              product_version_id TEXT,
              threshold_set_version TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(owner_id, project_id, product_version_id, threshold_set_version)
            );
            CREATE TABLE IF NOT EXISTS qa_reports (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              job_id TEXT NOT NULL,
              threshold_set_id TEXT NOT NULL,
              threshold_set_version TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              status TEXT NOT NULL,
              manifest_sha256 TEXT,
              binding_sha256 TEXT,
              decision TEXT,
              decision_notes TEXT,
              decision_manifest_sha256 TEXT,
              decided_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interaction_anchors (
              id TEXT PRIMARY KEY,
              product_version_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              name TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'DRAFT',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              approved_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (product_version_id, revision)
            );
            CREATE TABLE IF NOT EXISTS anchor_sets (
              id TEXT PRIMARY KEY,
              product_version_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (product_version_id, revision)
            );
            CREATE TABLE IF NOT EXISTS characters (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'DRAFT',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interaction_plans (
              id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL,
              product_version_id TEXT NOT NULL,
              anchor_set_id TEXT NOT NULL,
              character_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (plan_id, version)
            );
            CREATE TABLE IF NOT EXISTS reference_assets (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              uploaded_asset_id TEXT,
              source_url TEXT,
              status TEXT NOT NULL DEFAULT 'INGEST',
              error TEXT,
              source_hash TEXT,
              proxy_hash TEXT,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reference_analyses (
              id TEXT PRIMARY KEY,
              reference_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'DRAFT',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              edited_from_revision INTEGER,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (reference_id, revision)
            );
            CREATE TABLE IF NOT EXISTS reference_mappings (
              id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL,
              analysis_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS platform_profiles (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              profile_key TEXT NOT NULL,
              name TEXT NOT NULL,
              platform TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (owner_id, project_id, profile_key)
            );
            CREATE TABLE IF NOT EXISTS platform_profile_versions (
              id TEXT PRIMARY KEY,
              profile_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (profile_id, version),
              FOREIGN KEY(profile_id) REFERENCES platform_profiles(id)
            );
            CREATE TABLE IF NOT EXISTS postproduction_presets (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              preset_key TEXT NOT NULL,
              name TEXT NOT NULL,
              kind TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (owner_id, project_id, preset_key)
            );
            CREATE TABLE IF NOT EXISTS postproduction_preset_versions (
              id TEXT PRIMARY KEY,
              preset_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (preset_id, version),
              FOREIGN KEY(preset_id) REFERENCES postproduction_presets(id)
            );
            CREATE TABLE IF NOT EXISTS localizations (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              locale TEXT NOT NULL,
              profile_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS localization_revisions (
              id TEXT PRIMARY KEY,
              localization_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'DRAFT',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              edited_from_revision INTEGER,
              created_at TEXT NOT NULL,
              UNIQUE (localization_id, revision),
              FOREIGN KEY(localization_id) REFERENCES localizations(id)
            );
            CREATE TABLE IF NOT EXISTS audio_previews (
              id TEXT PRIMARY KEY,
              localization_id TEXT,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              locale TEXT NOT NULL,
              engine TEXT NOT NULL,
              voice TEXT NOT NULL,
              text TEXT NOT NULL,
              path TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS voiceovers (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              asset_id TEXT NOT NULL,
              locale TEXT NOT NULL,
              license_ref TEXT NOT NULL DEFAULT '',
              path TEXT NOT NULL DEFAULT '',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subtitle_tracks (
              id TEXT PRIMARY KEY,
              localization_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              locale TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(localization_id) REFERENCES localizations(id)
            );
            CREATE TABLE IF NOT EXISTS music_assets (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              license_ref TEXT NOT NULL,
              commercial_use_allowed INTEGER NOT NULL DEFAULT 0,
              path TEXT NOT NULL,
              duration_s REAL NOT NULL DEFAULT 0,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audio_mixes (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              voiceover_id TEXT,
              music_asset_id TEXT,
              path TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS duration_adaptations (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              policy TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_sources (
              id TEXT PRIMARY KEY,
              run_id TEXT,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              path TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS output_renditions (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              profile_id TEXT,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batches (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'DRAFT',
              revision INTEGER NOT NULL DEFAULT 1,
              paused INTEGER NOT NULL DEFAULT 0,
              cancelled INTEGER NOT NULL DEFAULT 0,
              idempotency_key TEXT,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_items (
              id TEXT PRIMARY KEY,
              batch_id TEXT NOT NULL,
              item_index INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'PENDING',
              run_id TEXT,
              job_id TEXT,
              cache_key TEXT NOT NULL,
              error TEXT,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (batch_id, item_index),
              FOREIGN KEY(batch_id) REFERENCES batches(id)
            );
            CREATE TABLE IF NOT EXISTS automation_keys (
              id TEXT PRIMARY KEY,
              key_id TEXT NOT NULL,
              prefix TEXT NOT NULL,
              name TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              workspace_id TEXT,
              project_id TEXT,
              scopes TEXT NOT NULL,
              secret_hash TEXT NOT NULL,
              fingerprint TEXT NOT NULL,
              ip_allowlist TEXT NOT NULL DEFAULT '',
              rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
              budget_limit_amount REAL,
              budget_period TEXT NOT NULL DEFAULT 'month',
              expires_at REAL,
              revoked_at TEXT,
              revoked_reason TEXT NOT NULL DEFAULT '',
              last_used_at TEXT,
              call_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE (key_id)
            );
            CREATE TABLE IF NOT EXISTS automation_rate_windows (
              id TEXT PRIMARY KEY,
              scope_kind TEXT NOT NULL,
              scope_ref TEXT NOT NULL,
              window_start INTEGER NOT NULL,
              window_seconds INTEGER NOT NULL DEFAULT 60,
              count INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              UNIQUE (scope_kind, scope_ref, window_start)
            );
            CREATE TABLE IF NOT EXISTS idempotency_records (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              actor TEXT NOT NULL,
              scope TEXT NOT NULL,
              idem_key TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              status_code INTEGER NOT NULL,
              response TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (owner_id, actor, scope, idem_key)
            );
            CREATE TABLE IF NOT EXISTS webhook_endpoints (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              event_types TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              backfill_history INTEGER NOT NULL DEFAULT 0,
              paused_at TEXT,
              paused_reason TEXT NOT NULL DEFAULT '',
              secret_current TEXT NOT NULL,
              secret_previous TEXT,
              secret_previous_expires_at REAL,
              revision INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox_events (
              id TEXT PRIMARY KEY,
              event_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              aggregate_type TEXT NOT NULL,
              aggregate_id TEXT NOT NULL,
              aggregate_revision INTEGER NOT NULL DEFAULT 0,
              schema_version TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (event_id)
            );
            CREATE TABLE IF NOT EXISTS delivery_attempts (
              id TEXT PRIMARY KEY,
              endpoint_id TEXT NOT NULL,
              event_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              status TEXT NOT NULL,
              response_status INTEGER,
              response_excerpt TEXT NOT NULL DEFAULT '',
              signature TEXT NOT NULL DEFAULT '',
              error TEXT,
              next_retry_at TEXT,
              delivered_at TEXT,
              created_at TEXT NOT NULL,
              UNIQUE (endpoint_id, event_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS dead_letter_events (
              id TEXT PRIMARY KEY,
              endpoint_id TEXT NOT NULL,
              event_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              attempts INTEGER NOT NULL,
              last_error TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '',
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              moved_at TEXT NOT NULL,
              replayed_at TEXT,
              UNIQUE (endpoint_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS members (
              id TEXT PRIMARY KEY,
              member_id TEXT NOT NULL,
              owner_id TEXT NOT NULL,
              name TEXT NOT NULL,
              role TEXT NOT NULL,
              token_hash TEXT NOT NULL,
              token_hint TEXT NOT NULL DEFAULT '',
              revoked_at TEXT,
              revoked_reason TEXT NOT NULL DEFAULT '',
              last_used_at TEXT,
              call_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (member_id)
            );
            CREATE TABLE IF NOT EXISTS connected_accounts (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              account_ref TEXT NOT NULL,
              display_name TEXT NOT NULL DEFAULT '',
              credential_ref TEXT NOT NULL DEFAULT '',
              scopes TEXT NOT NULL DEFAULT '',
              capabilities TEXT NOT NULL DEFAULT '',
              authorization_status TEXT NOT NULL DEFAULT 'CONNECTED',
              capabilities_checked_at TEXT,
              connected_at TEXT NOT NULL,
              disconnected_at TEXT,
              disconnect_reason TEXT NOT NULL DEFAULT '',
              revision INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              state TEXT NOT NULL,
              code_verifier TEXT NOT NULL,
              return_path TEXT NOT NULL DEFAULT '/',
              created_at TEXT NOT NULL,
              consumed_at TEXT,
              UNIQUE (state)
            );
            CREATE TABLE IF NOT EXISTS publish_preflights (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publish_approvals (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              preflight_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              account_id TEXT,
              package_id TEXT NOT NULL,
              snapshot_hash TEXT NOT NULL,
              scope TEXT NOT NULL DEFAULT 'single_publish',
              binding TEXT NOT NULL,
              actor TEXT NOT NULL,
              expires_at REAL,
              revoked_at TEXT,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publish_jobs (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              account_id TEXT NOT NULL,
              package_id TEXT NOT NULL,
              package_version_id TEXT NOT NULL,
              publish_intent_id TEXT NOT NULL,
              approval_id TEXT NOT NULL,
              dedupe_key TEXT NOT NULL,
              state TEXT NOT NULL,
              external_publish_id TEXT,
              permalink_url TEXT,
              last_error TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (dedupe_key)
            );
            CREATE TABLE IF NOT EXISTS budgets (              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              currency TEXT NOT NULL,
              limit_amount REAL,
              revision INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_events (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              operation_id TEXT NOT NULL,
              billing_item TEXT NOT NULL,
              event_id TEXT NOT NULL,
              dedupe_key TEXT NOT NULL,
              unit TEXT NOT NULL,
              quantity REAL NOT NULL,
              run_id TEXT,
              batch_id TEXT,
              capability TEXT,
              internal_estimate INTEGER NOT NULL DEFAULT 0,
              amount REAL,
              amount_source TEXT,
              currency TEXT,
              occurred_at TEXT NOT NULL,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (dedupe_key)
            );
            CREATE TABLE IF NOT EXISTS cost_ledger (
              id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              budget_id TEXT,
              kind TEXT NOT NULL,
              amount REAL NOT NULL,
              currency TEXT NOT NULL,
              run_id TEXT,
              batch_id TEXT,
              usage_event_id TEXT,
              note TEXT NOT NULL DEFAULT '',
              settled_at TEXT,
              settled_entry_id TEXT,
              actor_key_id TEXT,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );            CREATE TABLE IF NOT EXISTS publish_packages (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              batch_id TEXT,
              owner_id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              profile_id TEXT,
              locale TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'DRAFT',
              content_hash TEXT NOT NULL DEFAULT '',
              directory TEXT NOT NULL,
              zip_path TEXT NOT NULL DEFAULT '',
              zip_sha256 TEXT NOT NULL DEFAULT '',
              approved_at TEXT,
              payload TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
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
        for column in ("product_review_id", "product_review_sha256", "fidelity_policy_id", "fidelity_policy_sha256", "fidelity_snapshot_json", "strict_source_manifest_json", "strict_source_manifest_sha256", "controlled_render_evidence_json", "controlled_render_evidence_sha256"):
            ensure_column(db, "runs", column, "TEXT")
        ensure_column(db, "runs", "fidelity_policy_version", "INTEGER")
        # V6-06：预留对账标记，防止同一条预留被重复结算（重复结算会双计成本）。
        ensure_column(db, "cost_ledger", "settled_at", "TEXT")
        ensure_column(db, "cost_ledger", "settled_entry_id", "TEXT")
        # V6-07：账本行记录产生它的 Automation Key（Key 周期预算按此汇总，不额外记账）
        ensure_column(db, "cost_ledger", "actor_key_id", "TEXT")
        # V6-09：端点默认不回填历史事件（旧安装补齐该列）
        ensure_column(db, "webhook_endpoints", "backfill_history", "INTEGER NOT NULL DEFAULT 0")
        # V6-16：模型外观覆盖分析结果（上传时解析一次，素材卡直接展示，避免"看起来能渲染就以为外观已核验"）
        ensure_column(db, "assets", "media_info", "TEXT")
        for column in ("verified_dimensions", "view_coverage", "unverified_regions", "logo_regions", "camera_visibility_constraints"):
            ensure_column(db, "product_versions", column, "TEXT")
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
        default_threshold = dict(strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET)
        default_threshold_text = json.dumps(
            default_threshold, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        db.execute(
            "INSERT OR IGNORE INTO qa_threshold_sets(id, owner_id, project_id, product_version_id, threshold_set_version, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (
                default_threshold["threshold_set_id"],
                DEFAULT_OWNER_ID,
                DEFAULT_PROJECT_ID,
                default_threshold["threshold_set_version"],
                default_threshold_text,
                hashlib.sha256(default_threshold_text.encode("utf-8")).hexdigest(),
                now,
            ),
        )
        # V6-01：六个首批导出 Profile 与两个后期模板作为产品预设种子（幂等）。
        for seed in platform_profiles.SEED_PROFILES:
            spec = platform_profiles.spec_from_payload(seed)
            profile_key = spec.identity.profile_id
            db.execute(
                "INSERT OR IGNORE INTO platform_profiles(id, owner_id, project_id, profile_key, name, platform, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_key, DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID, profile_key,
                 spec.identity.name, spec.identity.platform, now, now),
            )
            payload_text = platform_profiles.profile_payload_json(spec)
            db.execute(
                "INSERT OR IGNORE INTO platform_profile_versions(id, profile_id, version, payload, payload_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"{profile_key}-v1", profile_key, 1, payload_text,
                 hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
            )
        for seed in platform_profiles.SEED_PRESETS:
            spec = platform_profiles.PostproductionPresetSpec.model_validate(seed["spec"])
            preset_key = seed["preset_key"]
            db.execute(
                "INSERT OR IGNORE INTO postproduction_presets(id, owner_id, project_id, preset_key, name, kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (preset_key, DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID, preset_key, spec.name, "postproduction", now, now),
            )
            payload_text = platform_profiles.preset_payload_json(spec)
            db.execute(
                "INSERT OR IGNORE INTO postproduction_preset_versions(id, preset_id, version, payload, payload_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"{preset_key}-v1", preset_key, 1, payload_text,
                 hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
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
    # 本镜头明确要求的独立分层；策略 allowed_operations 只表达“允许”，不强制每镜头生成。
    required_strict_layers: list[Literal["shadow_layer", "reflection_layer", "occlusion_layer"]] = Field(default_factory=list, max_length=3)
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
    def total_frames(self) -> int:
        return self.fps * self.duration_seconds


class ReenactmentOutputSpec(OutputSpec):
    """V5 参考重演输出：时长按目标 fps 量化为显式整帧帧数，fractional 时长只作记录。
    独立子类，不改变 V1 ImagePreviewSpec 合同（OutputSpec 字段与 V1 Schema 严格同步）。"""

    duration_seconds: float = Field(gt=0, le=120)
    frame_count: int = Field(ge=1, le=100000)

    @property
    def total_frames(self) -> int:
        return self.frame_count


def output_spec_for_plan(plan_snapshot: dict) -> OutputSpec:
    """按冻结计划选择输出合同：**显式 frame_count 的计划（V5 重演 / V6 生产计划）**
    使用 `ReenactmentOutputSpec`（整帧帧数 + 分数时长）；V1 导演计划沿用 5–8 秒合同。"""
    output = plan_snapshot.get("output") or {}
    if isinstance(output.get("frame_count"), int) and (
        plan_snapshot.get("from_reference") or plan_snapshot.get("production_plan")
    ):
        return ReenactmentOutputSpec.model_validate(output)
    return OutputSpec.model_validate(output)


def validate_production_plan_snapshot(plan_snapshot: dict, profile_spec=None) -> None:
    """V6 生产计划校验：2–8 镜头、单镜头 ≥24 帧、总帧数 = output.frame_count、
    总时长落在 Profile 允许范围内（V1 的"三镜头 5–8 秒"合同不适用于 V6 生产计划）。"""
    shots = plan_snapshot.get("shots") or []
    output = plan_snapshot.get("output") or {}
    if not 2 <= len(shots) <= 8:
        raise HTTPException(409, f"V6 生产计划必须包含 2–8 个镜头（当前 {len(shots)}）")
    total = 0
    for index, shot in enumerate(shots, start=1):
        duration = int(shot.get("duration_frames") or 0)
        if duration < 24:
            raise HTTPException(409, f"第 {index} 个镜头时长 {duration} 帧少于 24 帧下限")
        if not shot.get("camera") or not shot.get("focal_length_mm"):
            raise HTTPException(409, f"第 {index} 个镜头缺少相机或焦距")
        total += duration
    declared = output.get("frame_count")
    if declared is not None and int(declared) != total:
        raise HTTPException(409, f"output.frame_count={declared} 与镜头总帧数 {total} 不一致")
    fps = int(output.get("fps") or DEFAULT_FPS)
    seconds = total / fps if fps else 0.0
    if profile_spec is not None:
        low = profile_spec.video.duration_min_seconds
        high = profile_spec.video.duration_max_seconds
        if not (low <= seconds <= high):
            raise HTTPException(409, f"总时长 {seconds:.2f}s 不在 Profile 允许范围 {low}–{high}s 内")


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
    # V6-16（独立复核 BUG-03）：分辨率/输出规格必须能被真正保存，否则界面改了启动时又回到旧值。
    # 传入时必须与 duration_seconds、shots 总帧数一致，否则 422。
    output: OutputSpec | None = None
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID

    @model_validator(mode="after")
    def validate_total_duration(self):
        expected = DEFAULT_FPS * self.duration_seconds
        if sum(shot.duration_frames for shot in self.shots) != expected:
            raise ValueError(f"三个镜头的总帧数必须恰好为 {expected} 帧（{self.duration_seconds} 秒 × 24fps）")
        if self.output is not None and self.output.duration_seconds != self.duration_seconds:
            raise ValueError("output.duration_seconds 与 duration_seconds 必须一致")
        return self


class RunRequest(BaseModel):
    plan_id: str
    idempotency_key: str | None = None
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID
    # V3-01：Strict Run 必须引用真实冻结的审核与策略快照，不能读可变 latest。
    # 默认 false 保持 V1/V2 既有行为；显式打开或传入任一绑定字段时强制三件套齐备。
    require_fidelity_snapshot: bool = False
    plan_contract_id: str | None = None
    product_review_id: str | None = None
    product_review_sha256: str | None = None
    fidelity_policy_id: str | None = None
    fidelity_policy_sha256: str | None = None
    background_workflow: StrictBackgroundWorkflowInput | None = None
    background_source_mode: Literal["INDEPENDENT_BACKGROUND_WORKFLOW", "CONTROLLED_IMPORT"] | None = None

    @model_validator(mode="after")
    def validate_fidelity_binding(self):
        binding_fields = (self.plan_contract_id, self.product_review_id, self.fidelity_policy_id)
        if self.require_fidelity_snapshot or any(field is not None for field in binding_fields):
            missing = []
            if self.plan_contract_id is None:
                missing.append("plan_contract_id")
            if self.product_review_id is None:
                missing.append("product_review_id")
            if self.fidelity_policy_id is None:
                missing.append("fidelity_policy_id")
            if missing:
                raise ValueError("保真 Run 必须同时冻结 plan_contract_id、product_review_id、fidelity_policy_id；缺少: " + ", ".join(missing))
        if self.product_review_sha256 is not None and self.product_review_id is None:
            raise ValueError("product_review_sha256 必须与 product_review_id 同时提供")
        if self.fidelity_policy_sha256 is not None and self.fidelity_policy_id is None:
            raise ValueError("fidelity_policy_sha256 必须与 fidelity_policy_id 同时提供")
        if self.background_workflow is not None and not self.require_fidelity_snapshot and all(
            field is None for field in binding_fields
        ):
            raise ValueError("background_workflow 只能在 Strict Run 中绑定")
        if (self.background_workflow is None) != (self.background_source_mode is None):
            raise ValueError("background_workflow 与 background_source_mode 必须同时提供")
        return self



class StrictRenderEvidence(BaseModel):
    """本批可复验的本地 Blender 产物证据；只记录来源，不升级 Strict 资格。"""
    producer: str = Field(min_length=1, max_length=80)
    producer_version: str = Field(min_length=1, max_length=40)
    pass_layout: Literal["single-multilayer", "per-channel"]


class StrictBackgroundWorkflowInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    workflow_hash: str = Field(min_length=64, max_length=64)
    protection_map: list[dict] = Field(default_factory=list)
    output_ref: str = Field(default="strict/background")


class StrictSourceManifestRequest(BaseModel):
    render_evidence: StrictRenderEvidence
    background_workflow: StrictBackgroundWorkflowInput

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
STRICT_LAYER_OPERATIONS = ("shadow_layer", "reflection_layer", "occlusion_layer")
STRICT_OPERATION_TO_LAYER = {
    "shadow_layer": "shadow",
    "reflection_layer": "reflection",
    "occlusion_layer": "occlusion",
}


def _qa_default_threshold_payload() -> dict:
    return dict(strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET)


def _qa_default_threshold_set_id() -> str:
    return hashlib.sha256(
        json.dumps(_qa_default_threshold_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]


def _qa_threshold_payload_sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_qa_threshold_payload(payload: dict) -> dict:
    allowed = set(strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET)
    unknown = [key for key in payload if key not in allowed]
    if unknown:
        raise HTTPException(422, f"未知 QA 阈值字段: {', '.join(unknown)}")
    merged = dict(strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET)
    merged.update(payload)
    baseline = strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET
    ratio_fields = {
        "contour_iou_min",
        "mask_alpha_iou_min",
        "min_mask_coverage",
        "min_mask_binary_ratio",
        "logo_mask_min_coverage",
        "verified_dimension_max_relative_error",
    }
    mae_fields = {"color_core_mae_max", "edge_mae_max", "logo_core_mae_max"}
    max_looser_allowed = {
        "color_core_mae_max": 1.0 / 255.0,
        "edge_mae_max": 0.02,
        "logo_core_mae_max": 1.0 / 255.0,
        "verified_dimension_max_relative_error": 0.01,
    }
    min_looser_allowed = {
        "contour_iou_min": 0.995,
        "mask_alpha_iou_min": 0.995,
        "min_mask_coverage": 0.01,
        "min_mask_binary_ratio": 0.98,
        "logo_mask_min_coverage": 0.99,
    }
    for key in allowed - {"schema_version", "threshold_set_id", "threshold_set_version"}:
        try:
            value = float(merged[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"QA 阈值 {key} 必须为数值") from exc
        if not math.isfinite(value) or value < 0:
            raise HTTPException(422, f"QA 阈值 {key} 必须为非负有限数值")
        if key in ratio_fields and value > 1.0:
            raise HTTPException(422, f"QA 阈值 {key} 必须在 [0,1] 范围内")
        if key in mae_fields and value > 1.0:
            raise HTTPException(422, f"QA 阈值 {key} 超出合理 MAE 范围 [0,1]")
        if key in max_looser_allowed and value > max_looser_allowed[key] + 1e-12:
            raise HTTPException(
                422,
                f"QA 阈值 {key} 不得比默认基线更宽松（基线 {max_looser_allowed[key]}）",
            )
        if key in min_looser_allowed and value < min_looser_allowed[key] - 1e-12:
            raise HTTPException(
                422,
                f"QA 阈值 {key} 不得比默认基线更宽松（基线 {min_looser_allowed[key]}）",
            )
        merged[key] = value
    merged["threshold_set_version"] = str(payload.get("threshold_set_version") or "2026.09.1")
    merged["threshold_set_id"] = str(payload.get("threshold_set_id") or _qa_default_threshold_set_id())
    return merged



class ApprovedBackgroundWorkflow(BaseModel):
    """批准用于 Strict 非产品区域输入的背景来源工作流；不允许在产品保护区域内生成。"""
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    workflow_hash: str = Field(min_length=64, max_length=64)
    protection_map: list[dict] = Field(default_factory=list, max_length=64)

class FidelityPolicyRequest(BaseModel):
    """V3-03：保真策略版本。STRICT 只允许确定性操作。"""

    mode: Literal["STRICT", "CONTROLLED", "CREATIVE"]
    protected_regions: list[ProtectedRegion] = Field(default_factory=list, max_length=64)
    allowed_operations: list[str] = Field(default_factory=list, max_length=16)
    background_workflows: list[ApprovedBackgroundWorkflow] = Field(default_factory=list, max_length=32)
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


class ProductReviewRequest(BaseModel):
    """V3-04：冻结一次产品版本审核，并把核实结果绑定到版本上。"""

    decision: Literal["APPROVED", "REJECTED"]
    verified_dimensions: dict[str, float] = Field(default_factory=dict)
    view_coverage: list[str] = Field(default_factory=list, max_length=24)
    unverified_regions: list[str] = Field(default_factory=list, max_length=24)
    logo_regions: list[ProtectedRegion] = Field(default_factory=list, max_length=32)
    camera_visibility_constraints: list[str] = Field(default_factory=list, max_length=24)
    evidence_asset_ids: list[str] = Field(default_factory=list, max_length=24)
    source_kind: Literal["multi_view", "single_image", "cad"] = "cad"
    notes: str = Field(default="", max_length=2000)
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID

    @model_validator(mode="after")
    def enforce_single_image_rule(self):
        # 单图重建：未观测面必须显式声明未核实，并给出相机可见性限制；
        # 不允许用生成图把未核实区域变成"已核实事实"。
        if self.source_kind == "single_image" and self.decision == "APPROVED":
            if not self.unverified_regions:
                raise ValueError("单图来源的版本必须列出未核实区域（unverified_regions）")
            if not self.camera_visibility_constraints:
                raise ValueError("单图来源的版本必须给出相机可见性限制")
        return self


class QaThresholdSetRequest(BaseModel):
    threshold_set_version: str = Field(min_length=1, max_length=80)
    thresholds: dict = Field(default_factory=dict)
    product_version_id: str | None = None
    project_id: str = DEFAULT_PROJECT_ID
    owner_id: str = DEFAULT_OWNER_ID


class QaRunRequest(BaseModel):
    threshold_set_id: str | None = None
    threshold_set_version: str | None = None


class QaDecisionRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    manifest_hash: str = Field(min_length=64, max_length=64)
    notes: str = Field(default="", max_length=2000)


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




def resolve_run_owner_scope(run_id: str, owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> None:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        row = db.execute(
            "SELECT r.owner_id, pv.project_id FROM runs r "
            "JOIN plan_contracts pc ON pc.id = r.plan_contract_id "
            "JOIN product_versions pv ON pv.id = pc.product_version_id "
            "WHERE r.id = ?", (run_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Run 不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问 Run")

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


def fidelity_run_request_hash(plan_payload_sha256: str, request: RunRequest) -> str:
    """把保真绑定字段并入 Run 幂等 hash，避免不同冻结引用共享同一 idempotency_key。"""
    binding_fields = (
        request.plan_contract_id,
        request.product_review_id,
        request.fidelity_policy_id,
    )
    if not request.require_fidelity_snapshot and all(field is None for field in binding_fields):
        return plan_payload_sha256
    material = {
        "plan_payload_sha256": plan_payload_sha256,
        "plan_contract_id": request.plan_contract_id,
        "product_review_id": request.product_review_id,
        "product_review_sha256": request.product_review_sha256,
        "fidelity_policy_id": request.fidelity_policy_id,
        "fidelity_policy_sha256": request.fidelity_policy_sha256,
        "background_workflow": request.background_workflow.model_dump() if request.background_workflow else None,
        "background_source_mode": request.background_source_mode,
    }
    return plan_contract_snapshot_hash(canonical_payload(material))


def get_plan_contract_for_owner(
    db: sqlite3.Connection,
    plan_id: str,
    contract_id: str | None,
    owner_id: str,
    project_id: str,
) -> sqlite3.Row:
    if not contract_id:
        raise HTTPException(409, "Strict Run 缺少冻结合同引用")
    row = db.execute(
        """
        SELECT pc.*, pv.owner_id, pv.project_id
        FROM plan_contracts pc
        JOIN product_versions pv ON pv.id = pc.product_version_id
        WHERE pc.id = ? AND pc.plan_id = ?
        """,
        (contract_id, plan_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "冻结合同不存在或不属于该计划")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问冻结合同")
    return row


def freeze_fidelity_binding(
    db: sqlite3.Connection,
    contract: sqlite3.Row,
    product_review_id: str | None,
    product_review_sha256: str | None,
    fidelity_policy_id: str | None,
    fidelity_policy_sha256: str | None,
    owner_id: str,
) -> dict:
    """校验并冻结 Run 引用的真实审核/策略记录，绝不回退到可变 latest。"""
    if not product_review_id or not fidelity_policy_id:
        raise HTTPException(409, "Strict Run 缺少审核或策略冻结引用")
    product_version_id = contract["product_version_id"]
    review = db.execute(
        "SELECT * FROM product_reviews WHERE id = ? AND product_version_id = ?",
        (product_review_id, product_version_id),
    ).fetchone()
    if not review:
        raise HTTPException(404, "保真审核不存在或不属于该产品版本")
    if review["reviewer"] != owner_id:
        raise HTTPException(403, "越权引用他人审核记录")
    if review["decision"] != "APPROVED":
        raise HTTPException(409, "保真审核未通过，不能启动 Strict Run")
    if product_review_sha256 is not None and product_review_sha256.lower() != review["payload_sha256"].lower():
        raise HTTPException(409, "产品审核 hash 与冻结记录不符")
    policy = db.execute(
        "SELECT * FROM fidelity_policies WHERE id = ? AND product_version_id = ?",
        (fidelity_policy_id, product_version_id),
    ).fetchone()
    if not policy:
        raise HTTPException(404, "保真策略不存在或不属于该产品版本")
    if policy["mode"] != "STRICT":
        raise HTTPException(409, "Strict Run 仅接受 STRICT 保真策略")
    if fidelity_policy_sha256 is not None and fidelity_policy_sha256.lower() != policy["payload_sha256"].lower():
        raise HTTPException(409, "保真策略 hash 与冻结记录不符")
    return {
        "product_version_id": product_version_id,
        "plan_contract_id": contract["id"],
        "plan_contract_version": contract["version"],
        "plan_contract_sha256": contract["payload_sha256"],
        "product_review_id": review["id"],
        "product_review_sha256": review["payload_sha256"],
        "fidelity_policy_id": policy["id"],
        "fidelity_policy_version": policy["version"],
        "fidelity_policy_sha256": policy["payload_sha256"],
        "fidelity_mode": policy["mode"],
    }

def append_job_event(db: sqlite3.Connection, job_id: str, event_type: str, payload: dict | None = None) -> None:
    """追加任务事件。

    并发写入（执行器 + 租约心跳 + 批次调度）会同时计算 MAX(sequence)+1，在 PostgreSQL 的
    `(job_id, sequence)` 唯一约束下会撞键（真实故障：渲染到 75% 因 duplicate key 失败）。
    这里对唯一键冲突做有界重试并重新计算 sequence，保证事件不丢、任务不因事件写入失败而中断。
    """
    job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return
    event_payload = payload or {}
    created = utc_now()
    for _ in range(6):
        latest = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM job_events WHERE job_id = ?", (job_id,)
        ).fetchone()
        sequence = int(latest["sequence"]) + 1
        try:
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
                    created,
                ),
            )
        except Exception as exc:  # 唯一键冲突：重新计算 sequence 后重试（其他错误原样抛出）
            message = str(exc)
            if "job_events" not in message or "sequence" not in message:
                raise
            continue
        return


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
        strict_row = db.execute(
            "SELECT fidelity_snapshot_json FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if strict_row and strict_row["fidelity_snapshot_json"]:
            raise HTTPException(409, "Strict Run 必须由 Strict runtime closure 完成，不能通过 legacy complete 置为 SUCCEEDED")
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
        # QA 降级是唯一允许的终态→终态状态迁移：较新的 QA 运行失败必须把任务标回
        # QA_REJECTED（发布门已由审批失效兜底，状态本身也不能继续显示成功）。
        # 取消胜出的语义仍然优先（取消的任务不能被 QA 改标）。
        qa_downgrade = (
            values.get("status") == "QA_REJECTED"
            and current["status"] in TERMINAL_JOB_STATUSES
            and current["status"] != "CANCELLED"
        )
        if current["status"] in TERMINAL_JOB_STATUSES and not qa_downgrade:
            # 终态是最终事实：迟到的心跳/进度/完成回报不能把任务改回进行中。
            values = {
                key: value
                for key, value in values.items()
                if key not in {"status", "stage", "progress", "cancel_requested"}
            }
        if values.get("status") in {"SUCCEEDED", VERIFICATION_PASSED} and (
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


def _job_has_frozen_snapshot(job_id: str) -> bool:
    with connect() as db:
        row = db.execute(
            "SELECT fidelity_snapshot_json FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return bool(row and row["fidelity_snapshot_json"])


def _job_manifest_payload(job_id: str, manifest_path: str | None) -> dict | None:
    if not manifest_path:
        return None
    try:
        path = _resolve_job_artifact_path(job_id, manifest_path)
        return json.loads(path.read_text(encoding="utf-8"))
    except (HTTPException, OSError, json.JSONDecodeError):
        return None


def _verify_manifest_bound_files(job_id: str, manifest: dict, output_spec: OutputSpec) -> list[str]:
    """批准前重算当前 Manifest 引用的 Strict 输入/合成/成片文件 hash。"""
    failures: list[str] = []
    strict_root = RUNS / job_id / "strict"

    def verify_entries(entries: list[dict], root: Path, label: str) -> None:
        for entry in entries:
            relative = entry.get("path")
            expected = str(entry.get("sha256", "")).lower()
            if not relative:
                failures.append(f"{label} 缺少文件路径")
                continue
            path = (root / relative).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{label} 文件路径越界: {relative}")
                continue
            if not path.exists() or not path.is_file():
                failures.append(f"{label} 文件缺失: {relative}")
                continue
            if _file_sha256(path).lower() != expected:
                failures.append(f"{label} 文件已被替换: {relative}")

    input_manifest = manifest.get("input_manifest")
    if not isinstance(input_manifest, dict) or not input_manifest.get("files"):
        failures.append("Strict manifest 缺少逐文件 input_manifest")
    else:
        verify_entries(input_manifest.get("files", []), strict_root, "input_manifest")
    source_manifest = manifest.get("source_manifest")
    source_manifest_required = manifest.get("input_trust") == "REGISTERED_LOCAL_SAMPLE"
    if source_manifest_required and not isinstance(source_manifest, dict):
        failures.append("Strict manifest 缺少受控来源逐文件 source_manifest")
    elif isinstance(source_manifest, dict):
        for layer in source_manifest.get("layers", {}).values():
            if isinstance(layer, dict):
                verify_entries(layer.get("files", []), strict_root, "source_manifest")
    background_evidence = manifest.get("background_evidence")
    if isinstance(background_evidence, dict):
        failures.extend(verify_background_evidence_files(strict_root, background_evidence, output_spec))
    if not manifest.get("output_sha256"):
        failures.append("Strict manifest 缺少成片文件 hash")
    else:
        output = RUNS / job_id / "strict_preview.mp4"
        if not output.exists() or _file_sha256(output).lower() != str(manifest["output_sha256"]).lower():
            failures.append("Strict 成片文件已变化或缺失")
    if not manifest.get("composite_output_sha256"):
        failures.append("Strict manifest 缺少合成帧产物 hash")
    else:
        composite_root = strict_root / "composite_out"
        if _strict_tree_sha256(composite_root) != str(manifest["composite_output_sha256"]).lower():
            failures.append("Strict 合成帧产物已变化")
    return failures


def job_release_eligibility(job: dict) -> dict:
    """可发布/可继承 Strict PASS 的显式资格门。

    V1/V2 legacy 成功任务继续兼容；Strict Run 只有明确 fidelity_complete=true、
    非预置采样来源且状态为 SUCCEEDED，才允许下游聚合/发布视为完整 Strict 成果。
    """
    status = job.get("status")
    strict_mode = _job_has_frozen_snapshot(job["id"])
    manifest = _job_manifest_payload(job["id"], job.get("manifest_path"))
    fidelity_complete = None
    input_trust = None
    manifest_strict_mode = False
    if isinstance(manifest, dict):
        fidelity_complete = manifest.get("fidelity_complete")
        input_trust = manifest.get("input_trust")
        manifest_strict_mode = manifest.get("strict_mode") is True
    result: dict = {
        "eligible": False,
        "strict_mode": manifest_strict_mode or strict_mode,
        "fidelity_complete": fidelity_complete,
        "input_trust": input_trust,
        "verification_only": False,
        "reason": "",
    }
    if status == VERIFICATION_PASSED:
        result["verification_only"] = True
        result["reason"] = ("场景视频已生成，产品细节与动作尚待视觉审核；不是严格保真成片"
                            if manifest and manifest.get("scene_generation") else
                            "Strict 验证小样仅用于非商业验证，不可发布或继承 Strict PASS")
        return result
    if status != "SUCCEEDED":
        result["reason"] = f"任务状态为 {status}，尚未达到可发布完成状态"
        return result
    if not strict_mode:
        if not job.get("output_path"):
            result["reason"] = "任务缺少可发布产物"
            return result
        result["eligible"] = True
        result["reason"] = "V1/V2 基础预演成果"
        return result
    if not isinstance(manifest, dict) or manifest.get("strict_mode") is not True:
        result["reason"] = "Strict 成果缺少可信 manifest，不能发布"
        return result
    if input_trust != STRICT_VERIFIED_INPUT_TRUST:
        result["verification_only"] = True
        result["reason"] = "Strict 输入来源未达到可信闭环，input_trust 必须为 " + STRICT_VERIFIED_INPUT_TRUST
        return result
    if fidelity_complete is not True:
        result["reason"] = "Strict 成果 fidelity_complete 不为 true，不能发布"
        return result
    if not job.get("output_path"):
        result["reason"] = "Strict 成果缺少可发布产物"
        return result
    if not _job_has_valid_qa_approval(job["id"]):
        result["reason"] = "Strict 成果缺少有效 QA 人工批准或批准绑定已失效"
        return result
    result["eligible"] = True
    result["reason"] = "Strict 成果已通过完整来源与保真闭环"
    return result


def create_run_record(
    plan_row: sqlite3.Row,
    owner_id: str,
    project_id: str,
    idempotency_key: str | None,
    request_hash: str,
    *,
    plan_contract_id: str | None = None,
    product_review_id: str | None = None,
    product_review_sha256: str | None = None,
    fidelity_policy_id: str | None = None,
    fidelity_policy_sha256: str | None = None,
    require_fidelity_snapshot: bool = False,
    background_workflow: StrictBackgroundWorkflowInput | None = None,
    background_source_mode: str | None = None,
    required_strict_layers: list[str] | None = None,
) -> tuple[str, str, bool, dict | None]:
    now = utc_now()
    run_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    with connect() as db:
        if idempotency_key:
            existing = db.execute(
                "SELECT id, job_id, request_hash, status FROM runs WHERE idempotency_key = ? AND plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (idempotency_key, plan_row["id"]),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(409, "同一 idempotency_key 的计划内容冲突")
                return existing["id"], existing["job_id"], False, None

        fidelity_snapshot = None
        strict_binding = require_fidelity_snapshot or any(
            field is not None for field in (plan_contract_id, product_review_id, fidelity_policy_id)
        )
        if strict_binding:
            contract = get_plan_contract_for_owner(db, plan_row["id"], plan_contract_id, owner_id, project_id)
            latest = get_default_contract(plan_row["id"], db, owner_id, project_id)
            if not latest:
                raise HTTPException(409, "计划冻结合同缺失")
            if contract["id"] != latest["id"]:
                raise HTTPException(409, "计划已更新，旧保真审批已失效，请引用最新冻结合同")
            fidelity_snapshot = freeze_fidelity_binding(
                db, contract, product_review_id, product_review_sha256,
                fidelity_policy_id, fidelity_policy_sha256, owner_id,
            )
            required_layers = list(dict.fromkeys(required_strict_layers or []))
            unknown = [item for item in required_layers if item not in STRICT_LAYER_OPERATIONS]
            if unknown:
                raise HTTPException(409, "未知的 Strict 必需层: " + ", ".join(unknown))
            policy_row = db.execute(
                "SELECT payload FROM fidelity_policies WHERE id = ?", (fidelity_policy_id,),
            ).fetchone()
            policy_payload = json.loads(policy_row["payload"]) if policy_row else {}
            allowed_operations = set(policy_payload.get("allowed_operations", []))
            not_allowed = [item for item in required_layers if item not in allowed_operations]
            if not_allowed:
                raise HTTPException(409, "Strict 必需层未获保真策略允许: " + ", ".join(not_allowed))
            if background_workflow is not None:
                if background_source_mode not in {MODE_INDEPENDENT_WORKFLOW, MODE_CONTROLLED_IMPORT}:
                    raise HTTPException(409, "background_source_mode 必须是独立背景工作流或受控导入")
                workflow_payload = background_workflow.model_dump()
                workflow_payload["output_ref"] = "strict/background"
                if not _approved_background_workflow(policy_payload, workflow_payload):
                    raise HTTPException(409, "背景工作流未出现在已冻结 STRICT 保真策略的批准列表")
                fidelity_snapshot["background_workflow"] = workflow_payload
                fidelity_snapshot["background_source_mode"] = background_source_mode
            fidelity_snapshot["required_strict_layers"] = required_layers
        else:
            if background_workflow is not None:
                raise HTTPException(409, "background_workflow 只能在 Strict Run 中绑定")
            contract = ensure_plan_contract(db, plan_row, owner_id, project_id)

        asset = db.execute("SELECT * FROM assets WHERE id = ?", (plan_row["product_asset_id"],)).fetchone()
        if not asset:
            raise HTTPException(409, "计划绑定的产品素材不存在")
        snapshot_json = json.dumps(fidelity_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if fidelity_snapshot else None
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, NULL, NULL, NULL, 0, ?, ?)",
            (job_id, plan_row["id"], asset["id"], asset["kind"], now, now),
        )
        db.execute(
            "INSERT INTO runs (id, plan_id, plan_contract_id, owner_id, request_hash, idempotency_key, status, stage, progress, job_id, attempt_count, created_at, updated_at, product_review_id, product_review_sha256, fidelity_policy_id, fidelity_policy_version, fidelity_policy_sha256, fidelity_snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                fidelity_snapshot["product_review_id"] if fidelity_snapshot else None,
                fidelity_snapshot["product_review_sha256"] if fidelity_snapshot else None,
                fidelity_snapshot["fidelity_policy_id"] if fidelity_snapshot else None,
                fidelity_snapshot["fidelity_policy_version"] if fidelity_snapshot else None,
                fidelity_snapshot["fidelity_policy_sha256"] if fidelity_snapshot else None,
                snapshot_json,
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
    return run_id, job_id, True, fidelity_snapshot


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
    for frame in qa_sample_frames(plan_snapshot, output_spec.total_frames):
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
        "-frames:v", str(output_spec.total_frames),
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
        "--frames", str(output_spec.total_frames),
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
            rendered = min(output_spec.total_frames, rendered + 1)
            if rendered % 4 == 0:
                update_job(job_id, progress=12 + int(rendered / output_spec.total_frames * 70))
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
def load_strict_run_snapshot(db: sqlite3.Connection, job_id: str) -> dict | None:
    row = db.execute(
        "SELECT plan_id, owner_id, fidelity_snapshot_json FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if not row or not row["fidelity_snapshot_json"]:
        return None
    try:
        snapshot = json.loads(row["fidelity_snapshot_json"])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Strict Run 快照损坏: {exc}") from exc
    snapshot["plan_id"] = row["plan_id"]
    snapshot["owner_id"] = row["owner_id"]
    return snapshot


def _read_json_report(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_strict_input_manifest(job_id: str, strict_root: Path, snapshot: dict) -> tuple[dict, str]:
    """为预置 Strict 输入建立逐文件身份合同；只证明文件当前内容，不证明上游来源。"""
    files: list[dict] = []
    for folder_name in ("passes", "product", "mask", "background", "shadow", "reflection", "occlusion"):
        folder = strict_root / folder_name
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name == "input_manifest.json":
                continue
            files.append({
                "path": path.relative_to(strict_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    manifest = {
        "job_id": job_id,
        "plan_id": snapshot["plan_id"],
        "plan_contract_id": snapshot["plan_contract_id"],
        "product_version_id": snapshot["product_version_id"],
        "files": files,
    }
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return manifest, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _latest_run_id_for_job(db: sqlite3.Connection, job_id: str) -> str | None:
    row = db.execute(
        "SELECT id FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return row["id"] if row else None


def load_qa_threshold_set(
    db: sqlite3.Connection,
    owner_id: str,
    project_id: str,
    threshold_set_id: str | None,
    threshold_set_version: str | None,
    product_version_id: str | None = None,
) -> dict:
    """加载冻结阈值集；未显式指定时使用项目默认阈值，不允许从请求临时放宽。"""
    if threshold_set_id is None and threshold_set_version is None:
        threshold_set_id = strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET["threshold_set_id"]
        threshold_set_version = strict_qa.DEFAULT_STRICT_QA_THRESHOLD_SET["threshold_set_version"]
    conditions = ["owner_id = ?", "project_id = ?"]
    values: list = [owner_id, project_id]
    if threshold_set_id is not None:
        conditions.append("id = ?")
        values.append(threshold_set_id)
    if threshold_set_version is not None:
        conditions.append("threshold_set_version = ?")
        values.append(threshold_set_version)
    if product_version_id is not None:
        conditions.append("(product_version_id IS NULL OR product_version_id = ?)")
        values.append(product_version_id)
    row = db.execute(
        f"SELECT * FROM qa_threshold_sets WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT 1",
        values,
    ).fetchone()
    if not row:
        raise HTTPException(404, "冻结 QA 阈值集不存在或不属于当前项目")
    payload = json.loads(row["payload"])
    return {
        "id": row["id"],
        "threshold_set_version": row["threshold_set_version"],
        "payload": payload,
        "payload_sha256": row["payload_sha256"],
    }


def _qa_run_context(db: sqlite3.Connection, run_id: str) -> dict:
    run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "Run 不存在")
    if not run["fidelity_snapshot_json"]:
        raise HTTPException(409, "非 Strict Run 不能执行 Strict QA")
    snapshot = json.loads(run["fidelity_snapshot_json"])
    job_id = run["job_id"]
    plan_row = db.execute("SELECT payload FROM plans WHERE id = ?", (run["plan_id"],)).fetchone()
    if not plan_row:
        raise HTTPException(404, "计划不存在")
    plan_payload = json.loads(plan_row["payload"])
    output_spec = output_spec_for_plan(plan_payload)
    policy_row = db.execute(
        "SELECT payload FROM fidelity_policies WHERE id = ?", (snapshot["fidelity_policy_id"],),
    ).fetchone()
    review_row = db.execute(
        "SELECT payload FROM product_reviews WHERE id = ?", (snapshot["product_review_id"],),
    ).fetchone()
    if not policy_row or not review_row:
        raise HTTPException(409, "冻结保真策略或产品审核不存在")
    asset_row = db.execute(
        "SELECT * FROM assets WHERE id = (SELECT asset_id FROM jobs WHERE id = ?)",
        (job_id,),
    ).fetchone()
    asset_path = None
    if asset_row:
        asset = row_to_dict(asset_row)
        asset_path = Path(resolve_asset_path(asset))
    return {
        "run": row_to_dict(run),
        "run_id": run_id,
        "job_id": job_id,
        "snapshot": snapshot,
        "plan_payload": plan_payload,
        "output_spec": output_spec,
        "policy_payload": json.loads(policy_row["payload"]),
        "review_payload": json.loads(review_row["payload"]),
        "asset_path": asset_path,
        "controlled_evidence": load_controlled_render_evidence(db, job_id),
    }


def _qa_binding_sha256(
    db: sqlite3.Connection,
    run_id: str,
    manifest_sha256: str,
    threshold_payload_sha256: str,
    qa_report_payload_sha256: str,
) -> str:
    run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "Run 不存在")
    contract = db.execute(
        "SELECT payload_sha256 FROM plan_contracts WHERE id = ?", (run["plan_contract_id"],),
    ).fetchone()
    product_version = db.execute(
        "SELECT snapshot_sha256 FROM product_versions WHERE id = ?",
        (json.loads(run["fidelity_snapshot_json"])["product_version_id"],),
    ).fetchone()
    review = db.execute(
        "SELECT payload_sha256 FROM product_reviews WHERE id = ?",
        (run["product_review_id"],),
    ).fetchone()
    policy = db.execute(
        "SELECT payload_sha256 FROM fidelity_policies WHERE id = ?",
        (run["fidelity_policy_id"],),
    ).fetchone()
    job = db.execute(
        "SELECT asset_id FROM jobs WHERE id = ?", (run["job_id"],),
    ).fetchone()
    asset = None
    if job and job["asset_id"]:
        asset = db.execute(
            "SELECT sha256 FROM assets WHERE id = ?", (job["asset_id"],),
        ).fetchone()
    parts = {
        "run_id": run_id,
        "plan_contract_sha256": contract["payload_sha256"] if contract else None,
        "product_version_sha256": product_version["snapshot_sha256"] if product_version else None,
        "product_review_sha256": review["payload_sha256"] if review else None,
        "fidelity_policy_sha256": policy["payload_sha256"] if policy else None,
        "asset_sha256": asset["sha256"] if asset else None,
        "strict_source_manifest_sha256": run["strict_source_manifest_sha256"],
        "controlled_render_evidence_sha256": run["controlled_render_evidence_sha256"],
        "qa_threshold_sha256": threshold_payload_sha256,
        "qa_report_sha256": qa_report_payload_sha256,
        "manifest_sha256": manifest_sha256,
    }
    return hashlib.sha256(_canonical_json_text(parts).encode("utf-8")).hexdigest()


def _qa_report_decision_validity(db: sqlite3.Connection, row) -> dict:
    """读取时重新验证 APPROVED 是否仍绑定到当前 Manifest/输入/产物/合同。

    旧 decision 字段只代表历史人工决定；文件、阈值、审核、策略、计划或资产变化后，
    下游不得继续读取为有效批准。
    """
    result = {
        "decision": row["decision"] if row else None,
        "decision_valid": False,
        "effective_decision": row["decision"] if row else None,
        "reason": "",
    }
    if not row or row["decision"] != "APPROVED":
        result["reason"] = "QA 报告尚无有效人工批准"
        return result
    try:
        latest = db.execute(
            "SELECT id FROM qa_reports WHERE job_id = ? ORDER BY created_at DESC, updated_at DESC LIMIT 1",
            (row["job_id"],),
        ).fetchone()
        if not latest or latest["id"] != row["id"]:
            result["effective_decision"] = "REVOKED"
            result["reason"] = "已有更新的 QA 报告，旧批准失效"
            return result
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)).fetchone()
        if not job:
            result["effective_decision"] = "REVOKED"
            result["reason"] = "QA 报告绑定的 Job 不存在"
            return result
        manifest = _job_manifest_payload(row["job_id"], job["manifest_path"])
        if not isinstance(manifest, dict):
            result["effective_decision"] = "REVOKED"
            result["reason"] = "当前 Job 缺少可信 manifest 文件"
            return result
        current_manifest_sha256 = hashlib.sha256(
            _canonical_json_text(manifest).encode("utf-8")
        ).hexdigest()
        if not row["decision_manifest_sha256"] or not row["manifest_sha256"]:
            result["effective_decision"] = "REVOKED"
            result["reason"] = "QA 批准缺少精确 manifest_hash 绑定"
            return result
        if (
            current_manifest_sha256.lower() != row["manifest_sha256"].lower()
            or current_manifest_sha256.lower() != row["decision_manifest_sha256"].lower()
        ):
            result["effective_decision"] = "REVOKED"
            result["reason"] = "Manifest 已变化，旧 QA 批准失效"
            return result
        manifest_qa_report = manifest.get("qa_report")
        if (
            not isinstance(manifest_qa_report, dict)
            or hashlib.sha256(_canonical_json_text(manifest_qa_report).encode("utf-8")).hexdigest()
            != row["payload_sha256"]
        ):
            result["effective_decision"] = "REVOKED"
            result["reason"] = "Manifest 中的 QA 报告与已冻结报告不一致"
            return result
        context = _qa_run_context(db, row["run_id"])
        bound_failures = _verify_manifest_bound_files(
            row["job_id"], manifest, context["output_spec"]
        )
        if bound_failures:
            result["effective_decision"] = "REVOKED"
            result["reason"] = "Strict 输入/产物已变化: " + "；".join(bound_failures[:8])
            return result
        if row["status"] != "PASS":
            result["effective_decision"] = "REVOKED"
            result["reason"] = "QA 报告状态不是 PASS"
            return result
        threshold = db.execute(
            "SELECT payload_sha256 FROM qa_threshold_sets WHERE id = ?",
            (row["threshold_set_id"],),
        ).fetchone()
        if not threshold:
            result["effective_decision"] = "REVOKED"
            result["reason"] = "QA 阈值集不存在"
            return result
        current_binding = _qa_binding_sha256(
            db,
            row["run_id"],
            current_manifest_sha256,
            threshold["payload_sha256"],
            row["payload_sha256"],
        )
        if current_binding != row["binding_sha256"]:
            result["effective_decision"] = "REVOKED"
            result["reason"] = "QA 绑定输入已变化，旧批准失效"
            return result
        asset_row = db.execute(
            "SELECT * FROM assets WHERE id = (SELECT asset_id FROM jobs WHERE id = ?)",
            (row["job_id"],),
        ).fetchone()
        if asset_row:
            asset = row_to_dict(asset_row)
            asset_path = Path(resolve_asset_path(asset))
            if not asset_path.is_file() or _file_sha256(asset_path).lower() != str(asset["sha256"]).lower():
                result["effective_decision"] = "REVOKED"
                result["reason"] = "产品资产文件已变化"
                return result
        result["decision_valid"] = True
        result["effective_decision"] = "APPROVED"
        result["reason"] = ""
        return result
    except HTTPException as exc:
        result["effective_decision"] = "REVOKED"
        result["reason"] = f"{exc.status_code}: {exc.detail}"
        return result
    except Exception as exc:
        result["effective_decision"] = "REVOKED"
        result["reason"] = "QA 批准有效性复核失败: " + str(exc)[:300]
        return result


def _job_has_valid_qa_approval(job_id: str) -> bool:
    """发布门不得读取旧 decision 字段，必须按当前绑定重算有效性。"""
    with connect() as db:
        row = db.execute(
            """
            SELECT * FROM qa_reports
            WHERE job_id = ?
            ORDER BY created_at DESC, updated_at DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if not row or row["decision"] != "APPROVED":
            return False
        return _qa_report_decision_validity(db, row)["decision_valid"]


def _persist_qa_report(
    db: sqlite3.Connection,
    run_id: str,
    job_id: str,
    qa_report: dict,
    threshold_set: dict,
    manifest_sha256: str,
) -> str:
    payload_text = _canonical_json_text(qa_report)
    payload_sha256 = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    binding = _qa_binding_sha256(
        db, run_id, manifest_sha256, threshold_set["payload_sha256"], payload_sha256
    )
    report_id = str(uuid.uuid4())
    now = utc_now()
    db.execute(
        """
        INSERT INTO qa_reports(
          id, run_id, job_id, threshold_set_id, threshold_set_version,
          payload, payload_sha256, status, manifest_sha256, binding_sha256,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id, run_id, job_id, threshold_set["id"], threshold_set["threshold_set_version"],
            payload_text, payload_sha256, qa_report["status"], manifest_sha256, binding,
            now, now,
        ),
    )
    return report_id


def _collect_strict_layer_files(strict_root: Path) -> dict[str, dict]:
    """逐文件读取 Strict 层并计算 sha256；只描述目录当前内容。"""
    layers: dict[str, dict] = {}
    for layer_name in ("passes", "product", "mask", "background", "shadow", "reflection", "occlusion"):
        folder = strict_root / layer_name
        if not folder.exists():
            continue
        files = []
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name in {"input_manifest.json", "source_manifest.json"}:
                continue
            files.append({
                "path": path.relative_to(strict_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        layers[layer_name] = {"root": layer_name, "files": files}
    return layers


def _required_strict_layers_from_plan(plan_payload: dict) -> list[str]:
    """从冻结分镜合同汇总本次明确要求的独立层；缺失字段按空处理以兼容旧计划。"""
    required: list[str] = []
    for shot in plan_payload.get("shots", []):
        for layer in shot.get("required_strict_layers", []):
            if layer in STRICT_LAYER_OPERATIONS and layer not in required:
                required.append(layer)
    return required


_FRAME_INDEX_RE = re.compile(r"(?:^|[^0-9])(\d+)(?:$|[^0-9])")


def _layer_file_frame_set(layer: dict) -> set[int]:
    frames: set[int] = set()
    for entry in layer.get("files", []):
        stem = Path(entry.get("path", "")).stem
        match = _FRAME_INDEX_RE.search(stem)
        if match:
            frames.add(int(match.group(1)))
    return frames


def _optional_layer_frame_file_failures(layer: dict, layer_name: str) -> list[str]:
    """可选层必须具有可识别且唯一的帧号；逐文件格式/有限值校验由合成器完成。"""
    failures: list[str] = []
    seen: dict[int, str] = {}
    for entry in layer.get("files", []):
        relative = entry.get("path", "")
        stem = Path(relative).stem
        match = _FRAME_INDEX_RE.search(stem)
        if not match:
            failures.append(f"{layer_name} 层包含不可识别帧名：{relative}")
            continue
        frame = int(match.group(1))
        if frame in seen:
            failures.append(f"{layer_name} 层存在重复帧 {frame}: {seen[frame]}, {relative}")
        seen[frame] = relative
    return failures


def _required_layer_frame_contracts(plan_payload: dict, frame_count: int) -> tuple[dict[str, set[int]], list[str]]:
    """按冻结分镜逐段计算必需层必须覆盖的帧集合；分镜总帧与计划不符即失败。"""
    contracts: dict[str, set[int]] = {}
    failures: list[str] = []
    cursor = 1
    shots = plan_payload.get("shots", [])
    for index, shot in enumerate(shots):
        duration = shot.get("duration_frames")
        if not isinstance(duration, int) or duration <= 0:
            failures.append(f"分镜 {index + 1} duration_frames 无效")
            continue
        end = cursor + duration - 1
        for layer in shot.get("required_strict_layers", []):
            if layer in STRICT_LAYER_OPERATIONS:
                layer_name = STRICT_OPERATION_TO_LAYER[layer]
                contracts.setdefault(layer_name, set()).update(range(cursor, end + 1))
        cursor = end + 1
    if cursor - 1 != frame_count:
        failures.append(f"分镜帧范围合计 {cursor - 1} 与冻结计划 {frame_count} 不一致")
    return contracts, failures


def _strict_layer_contract_failures(
    layers: dict, policy_payload: dict, required_layers: list[str] | None = None,
    required_layer_frames: dict[str, set[int]] | None = None,
    expected_frames: set[int] | None = None,
) -> list[str]:
    """按冻结 STRICT 策略核对背景与独立层合同；allowed_operations 是允许列表，
    只有本镜头/本次合同明确要求时缺失才失败，且要求层必须逐帧覆盖对应分镜区间。"""
    failures: list[str] = []
    if not layers.get("background") or not layers["background"].get("files"):
        failures.append("Strict 输入来源缺少 background 层合同")
    allowed_operations = set(policy_payload.get("allowed_operations", []))
    required = set(required_layers or ())
    for operation, layer_name in STRICT_OPERATION_TO_LAYER.items():
        if layer_name in (required_layer_frames or {}):
            required.add(operation)
    for operation, layer_name in (("shadow_layer", "shadow"), ("reflection_layer", "reflection"), ("occlusion_layer", "occlusion")):
        layer = layers.get(layer_name) or {}
        layer_present = bool(layer.get("files"))
        present_frames = _layer_file_frame_set(layer) if layer_present else set()
        if operation in required and not layer_present:
            failures.append(f"本镜头/本次合同要求 {operation}，但 Strict 输入来源缺少 {layer_name} 层合同")
        if operation not in allowed_operations and layer_present:
            failures.append(f"Strict 输入来源包含未获策略允许的 {layer_name} 层")
        if layer_present:
            failures.extend(_optional_layer_frame_file_failures(layer, layer_name))
            if expected_frames is not None:
                out_of_range = sorted(present_frames - expected_frames)
                if out_of_range:
                    failures.append(f"{layer_name} 层含越出冻结全片帧集合的帧：{out_of_range[:12]}")
        required_frames = (required_layer_frames or {}).get(layer_name)
        if required_frames:
            missing = sorted(required_frames - present_frames)
            if missing:
                failures.append(f"本镜头要求的 {layer_name} 层缺少帧：{missing[:12]}")
    return failures


def load_strict_source_manifest(db: sqlite3.Connection, job_id: str) -> dict | None:
    row = db.execute(
        "SELECT strict_source_manifest_json, strict_source_manifest_sha256 FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if not row or not row["strict_source_manifest_json"]:
        return None
    try:
        manifest = json.loads(row["strict_source_manifest_json"])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Strict 输入来源快照损坏: {exc}") from exc
    actual = hashlib.sha256(_canonical_json_text(manifest).encode("utf-8")).hexdigest()
    if actual != row["strict_source_manifest_sha256"]:
        raise RuntimeError("Strict 输入来源快照哈希不匹配")
    return manifest


def _approved_background_workflow(policy_payload: dict, workflow: dict) -> bool:
    """背景工作流必须按 name/version/workflow_hash/输入保护区映射完整匹配已冻结策略。"""
    for item in policy_payload.get("background_workflows", []):
        if item.get("name") != workflow.get("name") or item.get("version") != workflow.get("version"):
            continue
        expected = str(item.get("workflow_hash", "")).lower()
        actual = str(workflow.get("workflow_hash", "")).lower()
        if not expected or not actual or expected != actual:
            continue
        expected_map = _canonical_json_text({"protection_map": item.get("protection_map", [])})
        actual_map = _canonical_json_text({"protection_map": workflow.get("protection_map", [])})
        if expected_map == actual_map:
            return True
    return False


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_tree_sha256(root: Path) -> str:
    """按相对路径 + sha256 计算 Strict 目录树的不可变摘要。"""
    entries: list[dict] = []
    if not root.exists():
        return hashlib.sha256(b"").hexdigest()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_sha256(path),
        })
    return hashlib.sha256(_canonical_json_text({"files": entries}).encode("utf-8")).hexdigest()


def _decode_process_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def probe_blender_version(executable: str | None) -> str | None:
    """读取锁定 Blender 可执行文件的真实版本；失败返回 None，不猜测。"""
    if not executable:
        return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = _decode_process_text(result.stdout) + _decode_process_text(result.stderr)
    first_line = text.strip().splitlines()
    return first_line[0].strip() if first_line else None


def build_controlled_blender_command(
    asset: dict, strict_root: Path, plan_path: Path, output_spec: OutputSpec
) -> list[str]:
    """构造由 Worker 掌控的 Blender 多通道渲染命令；不接受用户自报 producer。"""
    return [
        BLENDER,
        "--background",
        "--python", str(BLENDER_SCRIPT),
        "--",
        "--input", asset["path"],
        "--output", str(strict_root),
        "--width", str(output_spec.width),
        "--height", str(output_spec.height),
        "--frames", str(output_spec.total_frames),
        "--plan", str(plan_path),
        "--passes",
    ]


def run_controlled_blender_command(command: list[str]):
    """执行受控 Blender 命令；独立成函数便于 Worker/测试替换进程实现。"""
    return subprocess.run(command, capture_output=True)


def _parse_blender_pass_channels(stdout: str) -> list[str]:
    for line in (stdout or "").splitlines():
        if line.startswith("DIRECTOR_PASS_CHANNELS "):
            raw = line.split(" ", 1)[1].strip()
            return [item.strip() for item in raw.split(",") if item.strip()]
    return []


def _controlled_render_contract_failures(
    db: sqlite3.Connection, asset: dict, strict_snapshot: dict, plan_snapshot: dict
) -> list[str]:
    """受控渲染启动前，把资产/版本/计划合同逐项核对为结构化失败。"""
    failures: list[str] = []
    contract = db.execute(
        """
        SELECT pc.*, pv.product_asset_id, pv.owner_id, pv.project_id, pv.snapshot_sha256
        FROM plan_contracts pc
        JOIN product_versions pv ON pv.id = pc.product_version_id
        WHERE pc.id = ? AND pc.plan_id = ?
        """,
        (strict_snapshot["plan_contract_id"], strict_snapshot["plan_id"]),
    ).fetchone()
    if not contract:
        return ["受控渲染冻结合同不存在或不属于该计划"]
    if contract["owner_id"] != strict_snapshot["owner_id"]:
        return ["受控渲染越权访问冻结合同"]
    latest = get_default_contract(
        strict_snapshot["plan_id"], db, strict_snapshot["owner_id"], contract["project_id"]
    )
    if not latest or latest["id"] != contract["id"]:
        failures.append("计划已更新，旧保真审批已失效，请引用最新冻结合同")
    try:
        frozen = freeze_fidelity_binding(
            db, contract, strict_snapshot["product_review_id"], strict_snapshot["product_review_sha256"],
            strict_snapshot["fidelity_policy_id"], strict_snapshot["fidelity_policy_sha256"], strict_snapshot["owner_id"],
        )
    except HTTPException as exc:
        return [f"{exc.status_code}: {exc.detail}"]
    if frozen["product_review_sha256"] != strict_snapshot["product_review_sha256"]:
        failures.append("产品审核 hash 与 Run 快照不一致")
    if frozen["fidelity_policy_sha256"] != strict_snapshot["fidelity_policy_sha256"]:
        failures.append("保真策略 hash 与 Run 快照不一致")
    if frozen["product_version_id"] != strict_snapshot["product_version_id"]:
        failures.append("冻结产品版本与 Run 快照不一致")
    if contract["product_asset_id"] != asset["id"]:
        failures.append("冻结合同产品资产与 Job 资产不一致")
    expected_asset_sha256 = str(asset.get("sha256", "")).lower()
    actual_asset_sha256 = _file_sha256(Path(asset["path"]))
    if expected_asset_sha256 != actual_asset_sha256:
        failures.append("产品资产文件 hash 与冻结资产不一致")
    plan_snapshot_sha256 = plan_contract_payload_hash(plan_snapshot)[1]
    if contract["payload_sha256"] != plan_snapshot_sha256:
        failures.append("DirectorPlan 快照 hash 与冻结合同不一致")
    return failures


def _run_controlled_pass_validation(pass_root: Path, output_spec: OutputSpec) -> tuple[dict | None, str | None]:
    report_path = pass_root / "passes_report.json"
    cmd = [
        sys.executable, str(FIDELITY_PASS_VALIDATOR),
        "--passes", str(pass_root), "--frames", str(output_spec.total_frames),
        "--start-frame", "1", "--json", str(report_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    report = _read_json_report(report_path)
    if report is None:
        detail = (_decode_process_text(result.stderr) + _decode_process_text(result.stdout)).strip()[-2000:]
        return None, f"五通道校验执行失败: {detail}"
    return report, None


def load_controlled_render_evidence(db: sqlite3.Connection, job_id: str) -> dict | None:
    row = db.execute(
        "SELECT controlled_render_evidence_json, controlled_render_evidence_sha256 FROM runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if not row or not row["controlled_render_evidence_json"]:
        return None
    try:
        evidence = json.loads(row["controlled_render_evidence_json"])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"受控渲染证据损坏: {exc}") from exc
    actual = hashlib.sha256(_canonical_json_text(evidence).encode("utf-8")).hexdigest()
    if actual != row["controlled_render_evidence_sha256"]:
        raise RuntimeError("受控渲染证据哈希不匹配")
    return evidence


def verify_controlled_render_evidence(strict_root: Path, evidence: dict, asset_path: Path | None = None) -> list[str]:
    """用当前磁盘文件复核已冻结受控渲染证据；篡改、缺失或多出文件均失败。"""
    failures: list[str] = []
    expected: dict[str, str] = {}
    for item in evidence.get("generated_files", []):
        expected[item["path"]] = item.get("sha256", "")
    current: dict[str, str] = {}
    for path in sorted(strict_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"controlled_render_evidence.json", "passes_report.json"}:
            continue
        relative = path.relative_to(strict_root).as_posix()
        current[relative] = _file_sha256(path)
    missing = sorted(set(expected) - set(current))
    extra = sorted(set(current) - set(expected))
    if missing:
        failures.append(f"受控渲染产物缺少文件：{missing[:8]}")
    if extra:
        failures.append(f"受控渲染产物多出未登记文件：{extra[:8]}")
    for relative, expected_hash in expected.items():
        if relative in current and current[relative].lower() != expected_hash.lower():
            failures.append(f"受控渲染产物已被替换：{relative}")
    if asset_path is not None and evidence.get("asset_sha256"):
        if _file_sha256(asset_path).lower() != str(evidence.get("asset_sha256", "")).lower():
            failures.append("受控渲染证据中的产品资产 hash 与当前文件不一致")
    return failures


def _matching_approved_background_workflow(policy_payload: dict, workflow: dict) -> dict | None:
    """返回与冻结 Run 请求完全一致的策略批准项；无匹配返回 None。"""
    for item in policy_payload.get("background_workflows", []):
        if verify_workflow_contract(workflow, item):
            continue
        return item
    return None


def run_controlled_background_producer(
    job_id: str,
    worker_id: str,
    lease_epoch: int,
    run_dir: Path,
    output_spec: OutputSpec,
    snapshot: dict,
    policy_payload: dict,
) -> dict:
    """同一持租约 Worker 内的系统受控背景 Producer；不读取客户端 producer 字段。"""
    requested_workflow = snapshot.get("background_workflow")
    if not isinstance(requested_workflow, dict):
        return {"passed": True, "skipped": True}
    approved_workflow = _matching_approved_background_workflow(policy_payload, requested_workflow)
    if approved_workflow is None:
        return {"passed": False, "failure": "冻结 Run 背景工作流未出现在已批准 STRICT 策略列表"}

    strict_root = run_dir / "strict"
    source_mode = snapshot.get("background_source_mode")
    if source_mode == MODE_INDEPENDENT_WORKFLOW:
        workflow_file_value = os.getenv("PRODUCTDIRECTOR_BACKGROUND_WORKFLOW_FILE", "").strip()
        if not workflow_file_value:
            return {"passed": False, "failure": "冻结 Run 选择独立背景工作流但现场 workflow 文件缺失；禁止自动回退受控导入"}
        mode = MODE_INDEPENDENT_WORKFLOW
        workflow_file = Path(workflow_file_value)
        source_dir = None
        mask_dir = None
        controlled_root = None
    elif source_mode == MODE_CONTROLLED_IMPORT:
        mode = MODE_CONTROLLED_IMPORT
        workflow_file = None
        controlled_root = default_controlled_source_root()
        source_dir = controlled_root / job_id
        mask_dir = strict_root / "mask"
    else:
        return {"passed": False, "failure": "冻结 Run background_source_mode 缺失或不受支持"}

    def lease_check(job_id: str, worker_id: str, lease_epoch: int) -> None:
        with connect() as db:
            require_active_lease(db, job_id, worker_id, lease_epoch)

    try:
        evidence = run_controlled_background_workflow(
            job_id,
            worker_id,
            lease_epoch,
            snapshot,
            approved_workflow,
            strict_root,
            output_spec,
            requested_workflow=requested_workflow,
            mode=mode,
            source_dir=source_dir,
            mask_dir=mask_dir,
            workflow_file=workflow_file,
            controlled_root=controlled_root,
            lease_check=lease_check,
        )
    except BackgroundProducerError as exc:
        return {"passed": False, "failure": f"受控背景 Producer 失败: {exc}"}

    evidence_path = write_background_evidence(strict_root, evidence)
    binding_failures = verify_evidence_binding(
        evidence, job_id, snapshot, approved_workflow.get("workflow_hash", "")
    )
    file_failures = verify_background_evidence_files(strict_root, evidence, output_spec)
    failures = binding_failures + file_failures
    if failures:
        return {"passed": False, "failure": "背景证据运行后复核未通过: " + "；".join(failures[:12])}
    return {"passed": True, "skipped": False, "evidence": evidence, "evidence_path": evidence_path}


def _has_preseeded_strict_inputs(run_dir: Path) -> bool:
    pass_root = run_dir / "strict" / "passes"
    return pass_root.exists() and any(path.is_file() for path in pass_root.rglob("*"))


def _resolve_strict_source_path(strict_root: Path, relative: str) -> Path:
    root = strict_root.resolve()
    path = (strict_root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(403, "Strict 来源路径不在授权目录内") from None
    return path


def _verify_strict_source_manifest(
    strict_root: Path, manifest: dict, job_id: str, snapshot: dict,
    output_spec: OutputSpec, policy_payload: dict, plan_payload: dict,
) -> list[str]:
    failures: list[str] = []
    if manifest.get("job_id") != job_id:
        failures.append("Strict 输入来源不属于当前 Job")
    for field in ("plan_id", "plan_contract_id", "product_version_id", "owner_id"):
        if manifest.get(field) != snapshot.get(field):
            failures.append(f"Strict 输入来源 {field} 与 Run 快照不一致")
    frames = manifest.get("frames") or {}
    if frames.get("start_frame") != 1:
        failures.append("Strict 输入来源 start_frame 必须为 1")
    if frames.get("frame_count") != output_spec.total_frames:
        failures.append("Strict 输入来源 frame_count 与冻结计划不一致")
    if manifest.get("background_frame_offset", 0) != 0:
        failures.append("Strict 输入来源 background_frame_offset 必须为 0")
    background = manifest.get("background_workflow") or {}
    if not _approved_background_workflow(policy_payload, background):
        failures.append("背景工作流未出现在已冻结 STRICT 保真策略的批准列表")
    layers = manifest.get("layers") or {}
    required_layer_frames, plan_contract_failures = _required_layer_frame_contracts(
        plan_payload, output_spec.total_frames
    )
    failures.extend(plan_contract_failures)
    failures.extend(_strict_layer_contract_failures(
        layers, policy_payload, snapshot.get("required_strict_layers"), required_layer_frames,
        set(range(1, output_spec.total_frames + 1)),
    ))
    current_layers = _collect_strict_layer_files(strict_root)
    expected_files: dict[str, str] = {}
    for layer in (manifest.get("layers") or {}).values():
        for file_entry in layer.get("files", []):
            expected_files[file_entry["path"]] = file_entry.get("sha256", "")
    current_files: dict[str, str] = {}
    for layer in current_layers.values():
        for file_entry in layer.get("files", []):
            current_files[file_entry["path"]] = file_entry.get("sha256", "")
    missing = sorted(set(expected_files) - set(current_files))
    extra = sorted(set(current_files) - set(expected_files))
    if missing:
        failures.append(f"Strict 输入来源缺少文件：{missing[:8]}")
    if extra:
        failures.append(f"Strict 输入来源多出未注册文件：{extra[:8]}")
    for relative, expected_hash in expected_files.items():
        actual_hash = current_files.get(relative)
        if actual_hash is None:
            continue
        if actual_hash.lower() != expected_hash.lower():
            failures.append(f"Strict 输入文件已被替换：{relative}")
    return failures


def run_controlled_blender_passes(
    job_id: str, worker_id: str, lease_epoch: int, asset: dict,
    run_dir: Path, plan_path: Path, plan_snapshot: dict, output_spec: OutputSpec,
    strict_snapshot: dict,
) -> dict:
    """Worker 掌控的真实 Blender 产品五通道受控渲染：发起、退出码、Pass 语义与逐文件 hash 证据。"""
    with connect() as db:
        require_active_lease(db, job_id, worker_id, lease_epoch)
    with connect() as db:
        failures = _controlled_render_contract_failures(db, asset, strict_snapshot, plan_snapshot)
    if failures:
        return {"passed": False, "failure": "受控渲染合同复核未通过: " + "；".join(failures[:12])}
    if not BLENDER:
        return {"passed": False, "failure": "Blender 未安装或未找到，不能启动受控产品渲染"}

    strict_root = run_dir / "strict"
    pass_root = strict_root / "passes"
    pass_root.mkdir(parents=True, exist_ok=True)
    command = build_controlled_blender_command(asset, strict_root, plan_path, output_spec)
    blender_version = probe_blender_version(BLENDER)
    update_job(job_id, status="RUNNING", stage="RENDER", progress=10)
    try:
        completed = run_controlled_blender_command(command)
    except Exception as exc:
        return {"passed": False, "failure": f"Blender 渲染进程启动失败: {exc}"}
    stdout_text = _decode_process_text(completed.stdout)
    stderr_text = _decode_process_text(completed.stderr)
    if completed.returncode != 0:
        detail = (stderr_text or stdout_text).strip()[-2000:]
        return {"passed": False, "failure": f"Blender 渲染进程失败(exit={completed.returncode}): {detail}"}

    update_job(job_id, stage="VERIFY", progress=80)
    passes_report, passes_error = _run_controlled_pass_validation(pass_root, output_spec)
    if passes_report is None:
        return {"passed": False, "failure": passes_error or "五通道校验失败"}
    if not passes_report.get("passed"):
        detail = passes_report.get("failures", ["五通道校验未通过"])
        return {"passed": False, "failure": "五通道校验未通过: " + "；".join(detail[:12])}

    parsed_channels = _parse_blender_pass_channels(stdout_text)
    generated_files: list[dict] = []
    for path in sorted(strict_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {CONTROLLED_RENDER_EVIDENCE_FILE, "passes_report.json"}:
            continue
        generated_files.append({
            "path": path.relative_to(strict_root).as_posix(),
            "sha256": _file_sha256(path),
        })
    evidence = {
        "schema_version": "1.0",
        "producer_control": "CONTROLLED_WORKER",
        "worker_id": worker_id,
        "lease_epoch": lease_epoch,
        "blender_executable": str(BLENDER),
        "blender_version": blender_version,
        "render_script": str(BLENDER_SCRIPT),
        "render_script_sha256": _file_sha256(BLENDER_SCRIPT),
        "asset_id": asset["id"],
        "asset_sha256": _file_sha256(Path(asset["path"])),
        "product_version_id": strict_snapshot["product_version_id"],
        "plan_contract_id": strict_snapshot["plan_contract_id"],
        "plan_snapshot_sha256": plan_contract_payload_hash(plan_snapshot)[1],
        "command": command,
        "exit_code": completed.returncode,
        "frames_requested": output_spec.total_frames,
        "pass_semantics": parsed_channels,
        "pass_layout": passes_report.get("layout"),
        "generated_files": generated_files,
    }
    evidence_text = _canonical_json_text(evidence)
    evidence_sha256 = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
    evidence_path = strict_root / CONTROLLED_RENDER_EVIDENCE_FILE
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    verification_failures = verify_controlled_render_evidence(strict_root, evidence, Path(asset["path"]))
    if verification_failures:
        return {"passed": False, "failure": "受控渲染证据复核未通过: " + "；".join(verification_failures[:12])}

    manifest = {
        "schema_version": "1.0",
        "job_id": job_id,
        "plan_id": strict_snapshot["plan_id"],
        "strict_mode": True,
        "fidelity_snapshot": strict_snapshot,
        "controlled_render_evidence": evidence,
        "controlled_render_evidence_sha256": evidence_sha256,
        "passes_report": passes_report,
        "fidelity_complete": False,
        "input_trust": CONTROLLED_BLENDER_PRODUCT_TRUST,
        "width": output_spec.width,
        "height": output_spec.height,
        "fps": output_spec.fps,
        "frame_count": output_spec.total_frames,
        "duration_seconds": output_spec.duration_seconds,
        "created_at": utc_now(),
    }
    manifest_path = run_dir / "metadata.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with connect() as db:
        begin_immediate(db)
        require_active_lease(db, job_id, worker_id, lease_epoch)
        now = utc_now()
        db.execute(
            "UPDATE runs SET controlled_render_evidence_json = ?, controlled_render_evidence_sha256 = ?, updated_at = ? WHERE job_id = ?",
            (evidence_text, evidence_sha256, now, job_id),
        )
        append_job_event(db, job_id, "strict.render.controlled", {
            "worker_id": worker_id,
            "lease_epoch": lease_epoch,
            "blender_version": blender_version,
            "evidence_sha256": evidence_sha256,
        })
    return {"passed": True, "output": manifest_path, "manifest_path": manifest_path, "manifest": manifest}


def _persist_controlled_verification_qa(
    job_id: str,
    run_dir: Path,
    plan_snapshot: dict,
    output_spec: OutputSpec,
    strict_snapshot: dict,
    policy_payload: dict,
    controlled_evidence: dict | None,
    asset_path: str | None,
    manifest_path: Path,
) -> str:
    """受控渲染验证小样的 QA 报告：真实 QA 引擎只测渲染保真（产品 vs Beauty 参考、
    轮廓 IoU、Logo 区域）；未合成/无成片编码的检查如实 NOT_VERIFIED，发布门继续拒绝。
    保证每条 Strict Run 都有 QA 记录，不为"验证小样"静默跳过 QA。"""
    strict_root = run_dir / "strict"
    with connect() as db:
        review_row = db.execute(
            "SELECT payload FROM product_reviews WHERE id = ?", (strict_snapshot["product_review_id"],),
        ).fetchone()
        pv_row = db.execute(
            "SELECT project_id FROM product_versions WHERE id = ?", (strict_snapshot["product_version_id"],),
        ).fetchone()
        threshold_set = load_qa_threshold_set(
            db, strict_snapshot["owner_id"], (pv_row["project_id"] if pv_row else DEFAULT_PROJECT_ID), None, None
        )
        run_id = _latest_run_id_for_job(db, job_id)
        evidence_row = db.execute(
            "SELECT controlled_render_evidence_json FROM runs WHERE id = ?", (run_id,),
        ).fetchone()
    if not review_row:
        return ""
    # 受控渲染刚写完的证据必须重新读取：execute_job 局部变量是渲染前的旧值。
    if controlled_evidence is None and evidence_row and evidence_row["controlled_render_evidence_json"]:
        try:
            controlled_evidence = json.loads(evidence_row["controlled_render_evidence_json"])
        except json.JSONDecodeError:
            controlled_evidence = None
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    manifest_sha256 = hashlib.sha256(_canonical_json_text(manifest_payload).encode("utf-8")).hexdigest()
    qa_report = strict_qa.run_strict_qa(
        strict_root,
        plan_snapshot,
        output_spec,
        strict_snapshot,
        policy_payload,
        json.loads(review_row["payload"]),
        threshold_set["payload"],
        controlled_evidence=controlled_evidence,
        media_report=None,
        media_file=None,
        asset_path=Path(asset_path) if asset_path else None,
    )
    (strict_root / "qa_report.json").write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
    with connect() as db:
        return _persist_qa_report(db, run_id, job_id, qa_report, threshold_set, manifest_sha256)


def _run_strict_source_validator(strict_root: Path, output_spec: OutputSpec) -> tuple[dict | None, str | None]:
    report_path = strict_root / "source_report.json"
    cmd = [
        sys.executable, str(STRICT_SOURCE_VALIDATOR),
        "--passes", str(strict_root / "passes"),
        "--product", str(strict_root / "product"),
        "--mask", str(strict_root / "mask"),
        "--background", str(strict_root / "background"),
        "--frames", str(output_spec.total_frames), "--start-frame", "1",
        "--background-frame-offset", "0", "--json", str(report_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    report = _read_json_report(report_path)
    if report is None:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()[-2000:]
        return None, f"Strict 来源一致性校验执行失败: {detail}"
    return report, None

def reference_recreation_citation(db, plan_payload: dict, plan_id: str) -> dict | None:
    """V5-06 原视频引用追踪：冻结计划携带 from_reference 时，把来源引用与冻结映射
    快照写入 Run manifest，供重演产物溯源。参考视频内容/字幕/OCR/链接文本是外部
    数据；品牌/水印/音乐/人物不默认复制到成片。"""
    from_reference = plan_payload.get("from_reference") or {}
    analysis_id = from_reference.get("analysis_id")
    reference_id = from_reference.get("reference_id")
    if not analysis_id or not reference_id:
        return None
    analysis = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (analysis_id,)).fetchone()
    reference = db.execute("SELECT * FROM reference_assets WHERE id = ?", (reference_id,)).fetchone()
    mapping = db.execute(
        "SELECT * FROM reference_mappings WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    if not analysis or not reference or not mapping:
        return None
    reference_payload = json.loads(reference["payload"]) if reference["payload"] else {}
    mapping_payload = json.loads(mapping["payload"]) if mapping["payload"] else {}
    return {
        "reference_id": reference_id,
        "reference_source_sha256": (reference_payload.get("source") or {}).get("sha256"),
        "reference_proxy_sha256": (reference_payload.get("proxy") or {}).get("sha256"),
        "analysis_id": analysis_id,
        "analysis_revision": analysis["revision"],
        "analysis_payload_sha256": analysis["payload_sha256"],
        "mapping_id": mapping["id"],
        "mapping_payload_sha256": mapping["payload_sha256"],
        "mapping": mapping_payload,
        "note": "参考内容为外部数据；品牌/水印/音乐/人物不默认复制到成片",
    }


def _validate_from_reference_plan_snapshot(db, plan_snapshot: dict, plan_id: str) -> None:
    """V5-06 重演计划任务前置校验：镜头结构必须与冻结 ReferenceMapping 一致，
    保留时长（量化整帧）逐镜头核对，总帧数与 output.frame_count 一致。"""
    mapping_row = db.execute(
        "SELECT * FROM reference_mappings WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    if not mapping_row:
        raise HTTPException(409, "参考重演计划缺少冻结映射，无法启动任务")
    mapping = json.loads(mapping_row["payload"])
    expected_shots = mapping.get("shots") or []
    shots = plan_snapshot.get("shots") or []
    output = plan_snapshot.get("output") or {}
    if len(shots) != len(expected_shots):
        raise HTTPException(409, "计划镜头数与参考映射不一致")
    for index, (shot, mapped) in enumerate(zip(shots, expected_shots), start=1):
        if str(shot.get("id")) != str(mapped.get("target_shot_id")):
            raise HTTPException(409, f"第 {index} 个镜头与参考映射不匹配")
        if int(shot.get("duration_frames") or 0) != int(mapped.get("duration_target_frames") or -1):
            raise HTTPException(409, f"第 {index} 个镜头时长与映射保留时长不一致")
    total_frames = sum(int(shot["duration_frames"]) for shot in shots)
    if int(output.get("frame_count") or total_frames) != total_frames:
        raise HTTPException(409, "计划 output.frame_count 与镜头总帧数不一致")


def run_strict_runtime_closure(job_id: str, run_dir: Path, plan_path: Path, output_spec: OutputSpec) -> dict:
    """Strict Run 的 RENDER→COMPOSITE→QA 运行时闭环：真实读取并复核冻结引用，
    运行通道校验器与 Strict 合成器，任一步失败都不能产出 SUCCEEDED。"""
    with connect() as db:
        snapshot = load_strict_run_snapshot(db, job_id)
    if snapshot is None:
        return {"passed": False, "failure": "Strict Run 缺少冻结快照"}

    # 1) 复核冻结的 plan contract / review / policy 仍然有效且 hash 未漂移。
    try:
        with connect() as db:
            contract = db.execute(
                """
                SELECT pc.*, pv.owner_id, pv.project_id
                FROM plan_contracts pc
                JOIN product_versions pv ON pv.id = pc.product_version_id
                WHERE pc.id = ? AND pc.plan_id = ?
                """,
                (snapshot["plan_contract_id"], snapshot["plan_id"]),
            ).fetchone()
            if not contract:
                raise HTTPException(404, "冻结合同不存在或不属于该计划")
            if contract["owner_id"] != snapshot["owner_id"]:
                raise HTTPException(403, "越权访问冻结合同")
            latest = get_default_contract(snapshot["plan_id"], db, snapshot["owner_id"], contract["project_id"])
            if not latest or contract["id"] != latest["id"]:
                raise HTTPException(409, "计划已更新，旧保真审批已失效，请引用最新冻结合同")
            frozen = freeze_fidelity_binding(
                db, contract, snapshot["product_review_id"], snapshot["product_review_sha256"],
                snapshot["fidelity_policy_id"], snapshot["fidelity_policy_sha256"], snapshot["owner_id"],
            )
            if frozen["product_review_sha256"] != snapshot["product_review_sha256"]:
                raise HTTPException(409, "产品审核 hash 与 Run 快照不一致")
            if frozen["fidelity_policy_sha256"] != snapshot["fidelity_policy_sha256"]:
                raise HTTPException(409, "保真策略 hash 与 Run 快照不一致")
    except HTTPException as exc:
        return {"passed": False, "failure": f"{exc.status_code}: {exc.detail}"}

    strict_root = run_dir / "strict"
    pass_root = strict_root / "passes"
    if not pass_root.exists():
        return {"passed": False, "failure": "Strict Run 缺少五通道产物目录 strict/passes"}
    try:
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "failure": f"Strict Run 缺少有效的 DirectorPlan 快照: {exc}"}

    source_manifest = None
    source_report = None
    policy_payload: dict = {}
    with connect() as db:
        source_manifest = load_strict_source_manifest(db, job_id)
        policy_row = db.execute(
            "SELECT payload FROM fidelity_policies WHERE id = ?", (snapshot["fidelity_policy_id"],),
        ).fetchone()
        if policy_row:
            try:
                policy_payload = json.loads(policy_row["payload"])
            except json.JSONDecodeError:
                policy_payload = {}
    background_evidence = load_background_evidence(strict_root)
    if snapshot.get("background_workflow"):
        if not background_evidence:
            return {"passed": False, "failure": "Strict Run 绑定了 background_workflow 但缺少背景证据"}
        approved_workflow = _matching_approved_background_workflow(
            policy_payload, snapshot.get("background_workflow")
        )
        if approved_workflow is None:
            return {"passed": False, "failure": "冻结 Run 背景工作流未出现在已批准 STRICT 策略列表"}
        evidence_failures = verify_evidence_binding(
            background_evidence, job_id, snapshot, approved_workflow.get("workflow_hash", "")
        )
        evidence_failures += verify_background_evidence_files(strict_root, background_evidence, output_spec)
        if evidence_failures:
            return {"passed": False, "failure": "背景证据运行后复核未通过: " + "；".join(evidence_failures[:12])}
    if source_manifest is not None:
        verification_failures = _verify_strict_source_manifest(
            strict_root, source_manifest, job_id, snapshot, output_spec, policy_payload, plan_payload
        )
        if verification_failures:
            return {"passed": False, "failure": "Strict 输入来源冻结复核未通过: " + "；".join(verification_failures[:12])}
        source_report, source_error = _run_strict_source_validator(strict_root, output_spec)
        if source_report is None:
            return {"passed": False, "failure": source_error or "Strict 来源一致性校验失败"}
        if not source_report.get("passed"):
            failures = source_report.get("failures", ["Strict 来源一致性校验未通过"])
            return {"passed": False, "failure": "Strict 来源一致性校验未通过: " + "；".join(failures[:12])}
        input_manifest = source_manifest
    else:
        input_manifest, _ = build_strict_input_manifest(job_id, strict_root, snapshot)
        required_layer_frames, plan_contract_failures = _required_layer_frame_contracts(
            plan_payload, output_spec.total_frames
        )
        layer_contract_failures = _strict_layer_contract_failures(
            _collect_strict_layer_files(strict_root), policy_payload,
            snapshot.get("required_strict_layers"), required_layer_frames,
            set(range(1, output_spec.total_frames + 1)),
        )
        if plan_contract_failures or layer_contract_failures:
            return {"passed": False, "failure": "Strict 图层合同与冻结策略不一致: " + "；".join((plan_contract_failures + layer_contract_failures)[:12])}
    input_manifest_sha256 = hashlib.sha256(_canonical_json_text(input_manifest).encode("utf-8")).hexdigest()
    (strict_root / "input_manifest.json").write_text(
        json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2) 五通道真实校验（OpenEXR/Pillow，校验帧数、有限值、深度与法线）。
    passes_report_path = strict_root / "passes_report.json"
    pass_cmd = [
        sys.executable, str(FIDELITY_PASS_VALIDATOR), "--passes", str(pass_root),
        "--frames", str(output_spec.total_frames), "--start-frame", "1", "--json", str(passes_report_path),
    ]
    pass_result = subprocess.run(pass_cmd, capture_output=True)
    passes_report = _read_json_report(passes_report_path)
    if passes_report is None:
        detail = (pass_result.stderr or pass_result.stdout or b"").decode("utf-8", "replace").strip()[-2000:]
        return {"passed": False, "failure": f"五通道校验执行失败: {detail}"}
    if not passes_report.get("passed"):
        failures = passes_report.get("failures", ["五通道校验未通过"])
        return {"passed": False, "failure": "五通道校验未通过: " + "；".join(failures[:12])}

    # 3) 真实 Strict 合成与帧完整性 QA。
    product_dir = strict_root / "product"
    mask_dir = strict_root / "mask"
    background_dir = strict_root / "background"
    composite_out = strict_root / "composite_out"
    for label, folder in (("产品层", product_dir), ("遮罩", mask_dir), ("背景", background_dir)):
        if not folder.exists():
            return {"passed": False, "failure": f"Strict Run 缺少{label}目录 strict/{folder.name}"}
    strict_plan_path = strict_root / "strict_plan.json"
    required_layer_frames, plan_contract_failures = _required_layer_frame_contracts(
        plan_payload, output_spec.total_frames
    )
    strict_plan_path.write_text(
        json.dumps({
            "frame_count": output_spec.total_frames,
            "start_frame": 1,
            "background_frame_offset": 0,
            "required_layers": {name: sorted(frames) for name, frames in required_layer_frames.items()},
            "color_contract": STRICT_COLOR_CONTRACT,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    composite_cmd = [
        sys.executable, str(STRICT_COMPOSITE), "--product", str(product_dir), "--mask", str(mask_dir),
        "--background", str(background_dir), "--out", str(composite_out), "--dilate", "0",
        "--plan", str(strict_plan_path),
        "--product-color-space", STRICT_COLOR_CONTRACT["product_color_space"],
        "--background-color-space", STRICT_COLOR_CONTRACT["background_color_space"],
        "--layer-color-space", STRICT_COLOR_CONTRACT["layer_color_space"],
    ]
    for layer_name in ("shadow", "reflection", "occlusion"):
        layer_dir = strict_root / layer_name
        if layer_dir.exists():
            composite_cmd += [f"--{layer_name}", str(layer_dir)]
    composite_result = subprocess.run(composite_cmd, capture_output=True)
    composite_report = _read_json_report(composite_out / "composite_report.json")
    if composite_report is None:
        try:
            composite_report = json.loads((composite_result.stdout or b"").decode("utf-8", "replace").strip())
        except (ValueError, json.JSONDecodeError):
            composite_report = None
    if composite_report is None:
        detail = (composite_result.stderr or composite_result.stdout or b"").decode("utf-8", "replace").strip()[-2000:]
        return {"passed": False, "failure": f"Strict 合成执行失败: {detail}"}
    if not composite_report.get("passed"):
        failures = composite_report.get("failures", ["Strict 合成未通过"])
        return {"passed": False, "failure": "Strict 合成 QA 未通过: " + "；".join(failures[:12])}

    if not FFMPEG:
        return {"passed": False, "failure": "FFmpeg 未安装或未找到"}
    output = run_dir / "strict_preview.mp4"
    encode = subprocess.run(
        [FFMPEG, "-y", "-framerate", "24", "-start_number", "1", "-i", str(composite_out / "composite_%04d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)],
        capture_output=True,
    )
    if encode.returncode != 0 or not output.exists():
        return {"passed": False, "failure": "Strict 合成帧编码失败: " + (encode.stderr or b"").decode("utf-8", "replace")[-2000:]}

    plan_snapshot = json.loads(plan_path.read_text(encoding="utf-8"))
    media_report = media_quality_report(output, plan_snapshot, output_spec, run_dir / "qa_strict")
    media_report["computed_by"] = "productdirector.media_quality_report"
    media_report["source_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    if not media_report["passed"]:
        return {"passed": False, "failure": "Strict 编码成片媒体质量未通过: " + "；".join(media_report["failures"][:12])}
    with connect() as db:
        run_id = _latest_run_id_for_job(db, job_id)
        review_row = db.execute(
            "SELECT payload FROM product_reviews WHERE id = ?", (snapshot["product_review_id"],),
        ).fetchone()
        product_version = db.execute(
            "SELECT project_id FROM product_versions WHERE id = ?", (snapshot["product_version_id"],),
        ).fetchone()
        if not review_row or not product_version:
            return {"passed": False, "failure": "Strict QA 缺少冻结产品审核或产品版本"}
        threshold_set = load_qa_threshold_set(
            db, snapshot["owner_id"], product_version["project_id"], None, None
        )
        asset_row = db.execute(
            "SELECT * FROM assets WHERE id = (SELECT asset_id FROM jobs WHERE id = ?)", (job_id,),
        ).fetchone()
        controlled_evidence = load_controlled_render_evidence(db, job_id)
        reference_recreation = reference_recreation_citation(db, plan_snapshot, snapshot["plan_id"])
    asset_path = None
    if asset_row:
        asset = row_to_dict(asset_row)
        asset_path = Path(resolve_asset_path(asset))
    review_payload = json.loads(review_row["payload"])
    qa_report = strict_qa.run_strict_qa(
        strict_root,
        plan_snapshot,
        output_spec,
        snapshot,
        policy_payload,
        review_payload,
        threshold_set["payload"],
        controlled_evidence=controlled_evidence,
        media_report=media_report,
        media_file=output,
        asset_path=asset_path,
    )
    (strict_root / "qa_report.json").write_text(
        json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if qa_report["fatal_failures"]:
        return {
            "passed": False,
            "failure": "Strict QA 未通过: " + "；".join(qa_report["fatal_failures"][:12]),
        }
    manifest = {
        "schema_version": "1.0",
        "job_id": job_id,
        "plan_id": snapshot["plan_id"],
        "strict_mode": True,
        "fidelity_snapshot": snapshot,
        "reference_recreation": reference_recreation,
        "input_manifest": input_manifest,
        "input_manifest_sha256": input_manifest_sha256,
        "source_manifest": source_manifest,
        "source_report": source_report,
        "fidelity_complete": False,
        "input_trust": (
            "REGISTERED_LOCAL_SAMPLE"
            if source_manifest is not None
            else "CONTROLLED_IMPORT_VERIFICATION_SAMPLE"
            if background_evidence and background_evidence.get("source_provenance") == "CONTROLLED_IMPORT"
            else "PRESEEDED_LOCAL_SAMPLE"
        ),
        "passes_report": passes_report,
        "composite_report": composite_report,
        "composite_output_sha256": _strict_tree_sha256(composite_out),
        "output_sha256": _file_sha256(output),
        "qa_report": qa_report,
        "qa_threshold_set": {
            "id": threshold_set["id"],
            "version": threshold_set["threshold_set_version"],
            "payload_sha256": threshold_set["payload_sha256"],
        },
        "background_evidence": background_evidence,
        "width": output_spec.width,
        "height": output_spec.height,
        "fps": output_spec.fps,
        "frame_count": output_spec.total_frames,
        "duration_seconds": output_spec.duration_seconds,
        "director_plan": plan_snapshot,
        "created_at": utc_now(),
    }
    manifest_path = run_dir / "metadata.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_sha256 = hashlib.sha256(_canonical_json_text(manifest).encode("utf-8")).hexdigest()
    with connect() as db:
        _persist_qa_report(db, run_id, job_id, qa_report, threshold_set, manifest_sha256)
    return {"passed": True, "output": output, "manifest_path": manifest_path, "manifest": manifest}

def resolve_scene_reference(db, asset, owner_id: str) -> dict:
    source, crop = asset, None
    if asset["kind"] == "model":
        record = db.execute("SELECT request_payload FROM provider_jobs WHERE artifact_asset_id = ? "
                            "AND operation = 'RECONSTRUCT_3D' ORDER BY created_at DESC LIMIT 1", (asset["id"],)).fetchone()
        provenance = json.loads(record["request_payload"]) if record else {}
        source = db.execute("SELECT * FROM assets WHERE id = ?", (provenance.get("asset_id", ""),)).fetchone()
        crop = provenance.get("crop")
        if source and provenance.get("asset_sha256") != source["sha256"]:
            raise HTTPException(409, "重建来源图片已变化，请重新选择原始彩色产品图")
    if not source or source["kind"] != "image" or source["owner_id"] != owner_id:
        raise HTTPException(422, "场景生成需要原始彩色产品图；此模型无可追踪来源，请在产品库选择产品图片")
    return {"asset_id": source["id"], "sha256": source["sha256"], "crop": crop}


def render_scene_job(job_id: str, snapshot: dict, run_dir: Path) -> dict:
    reference = snapshot["scene_generation"]["reference"]
    with connect() as db:
        source = db.execute("SELECT * FROM assets WHERE id = ?", (reference["asset_id"],)).fetchone()
        product = db.execute("SELECT * FROM assets WHERE id = ?", (snapshot["product_asset_id"],)).fetchone()
    if not source or not product or source["owner_id"] != product["owner_id"]:
        raise RuntimeError("场景参考图不存在或归属不一致")
    def check_active():
        lease = CURRENT_LEASE.get()
        with connect() as db:
            require_active_lease(db, job_id, lease.worker_id, lease.lease_epoch)
        if get_job(job_id)["cancel_requested"]:
            raise RuntimeError("场景生成已取消；不全局中断其他用户的H3任务")
    def run_command(command):
        check_active()
        with process_lock:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes[job_id] = process
        try:
            _, err = process.communicate()
        finally:
            with process_lock: processes.pop(job_id, None)
        check_active()
        if process.returncode:
            raise RuntimeError(err.decode("utf-8", errors="replace")[-1800:])
    return scene_generation.generate_scenes(snapshot, resolve_asset_path(row_to_dict(source)).read_bytes(), run_dir,
        ffmpeg=FFMPEG, run_command=run_command, check_active=check_active,
        progress=lambda stage, value: update_job(job_id, stage=stage, progress=value))


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
        output_spec = output_spec_for_plan(plan_snapshot)
        with connect() as db:
            strict_snapshot = load_strict_run_snapshot(db, job_id)
        if strict_snapshot is not None:
            with connect() as db:
                source_manifest = load_strict_source_manifest(db, job_id)
                controlled_evidence = load_controlled_render_evidence(db, job_id)
                policy_row = db.execute(
                    "SELECT payload FROM fidelity_policies WHERE id = ?",
                    (strict_snapshot["fidelity_policy_id"],),
                ).fetchone()
            policy_payload = json.loads(policy_row["payload"]) if policy_row else {}
            if source_manifest is None and controlled_evidence is None and not _has_preseeded_strict_inputs(run_dir):
                controlled_result = run_controlled_blender_passes(
                    job_id, worker_id, lease_epoch, asset, run_dir, plan_path,
                    plan_snapshot, output_spec, strict_snapshot,
                )
                if not controlled_result["passed"]:
                    update_job(job_id, status="QA_REJECTED", stage="QA_REJECTED", error=controlled_result["failure"], progress=100)
                    release_worker_lease(job_id, worker_id, lease_epoch, "worker.failed", {"error": controlled_result["failure"]})
                    return
                background_result = run_controlled_background_producer(
                    job_id, worker_id, lease_epoch, run_dir, output_spec,
                    strict_snapshot, policy_payload,
                )
                if not background_result.get("skipped") and not background_result["passed"]:
                    update_job(job_id, status="QA_REJECTED", stage="QA_REJECTED", error=background_result["failure"], progress=100)
                    release_worker_lease(job_id, worker_id, lease_epoch, "worker.failed", {"error": background_result["failure"]})
                    return
                if strict_snapshot.get("background_workflow"):
                    strict_result = run_strict_runtime_closure(job_id, run_dir, plan_path, output_spec)
                    if not strict_result["passed"]:
                        update_job(job_id, status="QA_REJECTED", stage="QA_REJECTED", error=strict_result["failure"], progress=100)
                        release_worker_lease(job_id, worker_id, lease_epoch, "worker.failed", {"error": strict_result["failure"]})
                        return
                    update_job(job_id, status=VERIFICATION_PASSED, stage="VERIFIED_SAMPLE", progress=100,
                               output_path=_storage_reference(job_id, str(strict_result["output"])),
                               manifest_path=_storage_reference(job_id, str(strict_result["manifest_path"])),
                               error=None)
                    release_worker_lease(job_id, worker_id, lease_epoch, "worker.completed")
                    return
                # 未绑定背景工作流：受控渲染验证小样。仍运行真实 QA 引擎并落库，
                # 合成/编码检查如实 NOT_VERIFIED；发布门继续拒绝。
                _persist_controlled_verification_qa(
                    job_id, run_dir, plan_snapshot, output_spec, strict_snapshot, policy_payload,
                    controlled_evidence, asset["path"],
                    Path(controlled_result["manifest_path"]),
                )
                update_job(
                    job_id, status=VERIFICATION_PASSED, stage="VERIFIED_SAMPLE", progress=100,
                    output_path=_storage_reference(job_id, str(controlled_result["manifest_path"])),
                    manifest_path=_storage_reference(job_id, str(controlled_result["manifest_path"])),
                    error=None,
                )
                release_worker_lease(job_id, worker_id, lease_epoch, "worker.completed")
                return
            strict_result = run_strict_runtime_closure(job_id, run_dir, plan_path, output_spec)
            if not strict_result["passed"]:
                update_job(job_id, status="QA_REJECTED", stage="QA_REJECTED", error=strict_result["failure"], progress=100)
                release_worker_lease(job_id, worker_id, lease_epoch, "worker.failed", {"error": strict_result["failure"]})
                return
            update_job(job_id, status=VERIFICATION_PASSED, stage="VERIFIED_SAMPLE", progress=100,
                       output_path=_storage_reference(job_id, str(strict_result["output"])),
                       manifest_path=_storage_reference(job_id, str(strict_result["manifest_path"])),
                       error=None)
            release_worker_lease(job_id, worker_id, lease_epoch, "worker.completed")
            return
        output = run_dir / "preview.mp4"
        scene_result = None
        if plan_snapshot.get("scene_generation"):
            scene_result = render_scene_job(job_id, plan_snapshot, run_dir)
        elif asset["kind"] == "model":
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
        frame_count = int(stream.get("nb_frames", output_spec.total_frames))
        if stream.get("width") != output_spec.width or stream.get("height") != output_spec.height:
            raise RuntimeError(f"视频技术检查失败: {stream.get('width')}x{stream.get('height')}, {duration:.3f}s")
        if abs(framerate - output_spec.fps) > 0.05 or frame_count != output_spec.total_frames:
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
            # V6-16（独立复核 BUG-05）：如实报告保真三元组，不再固定写 STRICT。
            # 基础预演（无 Strict 快照）的执行模式是 CONTROLLED、验证状态 NOT_VERIFIED；
            # product_version_id 取冻结合同里的真实版本，缺失时标 UNKNOWN（不再用占位 UUID）。
            with connect() as db:
                contract_row = get_default_contract(job["plan_id"], db)
            strict_bound = strict_snapshot is not None
            executed_mode = "GENERATIVE" if scene_result else ("STRICT" if strict_bound else "CONTROLLED")
            verification_status = ("VERIFIED" if strict_bound and qa_report.get("passed") else
                                   ("NOT_VERIFIED" if not strict_bound else "UNKNOWN"))
            director_plan_3d = None if scene_result else director_plan.to_target_document(
                plan_snapshot,
                executed_mode=executed_mode,
                verification_status=verification_status,
                product_version_id=(contract_row["product_version_id"] if contract_row else None),
            )
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
            "preview_kind": "H3_SCENES" if scene_result else ("BLENDER_3D" if asset["kind"] == "model" else "IMAGE_2D"),
            "scene_generation": scene_result,
            "width": output_spec.width,
            "height": output_spec.height,
            "fps": output_spec.fps,
            "frame_count": output_spec.total_frames,
            "duration_seconds": output_spec.duration_seconds,
            "ffprobe": {"duration": duration, "video_stream": stream},
            "qa": qa_report,
            "director_plan": plan_snapshot,
            # 目标 3D 合同视图：把运行时快照映射成 contracts/director-plan.v1.1.schema.json 的形状
            "director_plan_3d": director_plan_3d,
            "director_plan_3d_error": director_plan_3d_error,
            # V6-16：保真三元组放在 Manifest 顶层，避免只看 director_plan_3d 时被误读
            "fidelity": {
                "requested_mode": plan_snapshot.get("fidelity_mode"),
                "executed_mode": executed_mode if director_plan_3d or scene_result else None,
                "verification_status": verification_status if director_plan_3d or scene_result else "UNKNOWN",
                "strict_snapshot_bound": strict_bound,
                "product_version_id": contract_row["product_version_id"] if contract_row else None,
                "product_version_binding": "VERSIONED" if contract_row else "UNKNOWN",
                "note": ("基础预演不构成 Strict 验收：executed_mode 与 verification_status 才是实际状态"
                         if not strict_bound else "Strict 链路：模式与验证状态来自真实快照与 QA"),
            },
            "director_plan_snapshot_sha256": hashlib.sha256(plan_snapshot_bytes).hexdigest(),
            "blender": Path(BLENDER).name if BLENDER else None,
            "ffmpeg": Path(FFMPEG).name if FFMPEG else None,
            "created_at": utc_now(),
        }
        manifest_path = run_dir / "metadata.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        update_job(
            job_id,
            status=VERIFICATION_PASSED if scene_result else "SUCCEEDED",
            stage="SCENES_REVIEW" if scene_result else "ARTIFACT",
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
    started = time.monotonic()
    try:
        execute_claimed_job(job_id, "local-background", int(claim["lease_epoch"]))
    finally:
        _record_render_usage(job_id, started)


def _record_render_usage(job_id: str, started: float) -> None:
    """把真实渲染耗时登记为自管 GPU 用量（V6-06）：失败/QA_REJECTED 的任务同样计入消耗。"""
    elapsed = max(0.0, time.monotonic() - started)
    try:
        status = get_job(job_id)["status"]
    except HTTPException:
        status = "UNKNOWN"
    record_internal_usage(
        provider="productdirector.blender", unit="gpu_second", quantity=round(elapsed, 3),
        operation_id=job_id, capability="render", run_id=job_id,
        note=f"渲染任务 {job_id} 墙钟时长折算的 GPU 秒（内部估算，状态 {status}）",
    )


def _advance_batch_for_job(job_id: str) -> None:
    """作业完成后推进它所属的批次：否则批次只按 max_concurrent 调度一次就永久停在 PENDING。

    真实缺陷证据：V6-09 负载批次 100 项只调度了 2 项，其余 98 项在项完成后无人推进。
    """
    try:
        with connect() as db:
            item = db.execute("SELECT batch_id FROM batch_items WHERE job_id = ?", (job_id,)).fetchone()
        if item:
            advance_batch(item["batch_id"], execute_inline=False)
    except Exception as exc:  # 推进失败不影响作业本身的结果
        print(f"[batch] 推进批次失败 job={job_id}: {exc}", file=sys.stderr)


def run_worker_once(worker_id: str) -> dict:
    reconcile_stale_jobs()
    claim = claim_job(worker_id)
    if not claim.get("claimed"):
        return {"claimed": False}
    started = time.monotonic()
    try:
        execute_claimed_job(claim["job_id"], worker_id, int(claim["lease_epoch"]))
    finally:
        _record_render_usage(claim["job_id"], started)
        _advance_batch_for_job(claim["job_id"])
    return {"claimed": True, "job_id": claim["job_id"], "lease_epoch": claim["lease_epoch"], "status": get_job(claim["job_id"])["status"]}


def current_actor_label() -> str:
    """当前调用方标签：会话为 `session`，Automation 为 `key:<key_id>`（不写密钥）。"""
    identity = security.current_automation_key.get()
    if identity:
        return f"key:{identity['key_id']}"
    return "session"


def _rate_window_bump(db: sqlite3.Connection, scope_kind: str, scope_ref: str, window: int) -> int:
    """窗口计数 +1（唯一键冲突时重试重算），返回当前窗口计数。"""
    for _ in range(6):
        row = db.execute(
            "SELECT count FROM automation_rate_windows WHERE scope_kind = ? AND scope_ref = ? AND window_start = ?",
            (scope_kind, scope_ref, window),
        ).fetchone()
        now = utc_now()
        if row is None:
            try:
                db.execute(
                    "INSERT INTO automation_rate_windows(id, scope_kind, scope_ref, window_start, window_seconds, "
                    "count, updated_at) VALUES (?, ?, ?, ?, 60, 1, ?)",
                    (str(uuid.uuid4()), scope_kind, scope_ref, window, now),
                )
                return 1
            except sqlite3.IntegrityError:
                continue
        count = int(row["count"]) + 1
        db.execute(
            "UPDATE automation_rate_windows SET count = ?, updated_at = ? WHERE scope_kind = ? AND scope_ref = ? "
            "AND window_start = ?",
            (count, now, scope_kind, scope_ref, window),
        )
        return count
    raise HTTPException(503, "限流计数器写入失败，请重试")


def _key_period_spend(db: sqlite3.Connection, key_id: str, period: str, now_epoch: float) -> float:
    """该 Key 在当前周期的已发生金额（settled + accrued，预留不计入）。"""
    seconds = {"minute": 60, "hour": 3600, "day": 86400, "month": 30 * 86400}.get(period or "month", 30 * 86400)
    cutoff = _iso_from_epoch(now_epoch - seconds)
    rows = db.execute(
        "SELECT amount FROM cost_ledger WHERE actor_key_id = ? AND kind IN ('settled', 'accrued') AND created_at >= ?",
        (key_id, cutoff),
    ).fetchall()
    return round(sum(float(row["amount"]) for row in rows), 6)


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _epoch_from_iso(value: str | None) -> float:
    """解析库里已有的时间字符串（兼容带/不带 Z 与带时区偏移的写法）。"""
    if not value:
        return 0.0
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def enforce_member_access(request: Request, identity: dict) -> None:
    """V6-16：成员角色权限（服务端强制；未登记的写操作默认拒绝）。"""
    try:
        roles.check_permission(identity["role"], request.method, request.url.path)
    except roles.RoleError as exc:
        raise HTTPException(exc.status_code, exc.as_detail()) from exc


def enforce_automation_access(request: Request, identity: dict) -> None:
    """V6-07：scope、Key 管理边界、双层限流、Key 周期预算。"""
    path = request.url.path.rstrip("/") or "/api/v1"
    method = "GET" if request.method == "HEAD" else request.method
    if automation.owner_only(path):
        raise HTTPException(403, {
            "code": "owner_session_required",
            "detail": "Automation Key 不能管理 Key：创建/撤销只允许 Owner 会话（浏览器登录）",
        })
    required = automation.required_scope(method, path)
    missing = automation.missing_scopes(identity["scopes"], required)
    if missing:
        raise HTTPException(403, {
            "code": "insufficient_scope",
            "detail": f"该 Key 缺少 scope：{', '.join(missing)}",
            "required_scopes": missing,
            "granted_scopes": identity["scopes"],
            "route": f"{method} {path}",
        })
    now = time.time()
    window = automation.window_start(now)
    key_limit = max(1, int(identity["rate_limit_per_minute"]))
    workspace_limit = automation.WORKSPACE_RATE_LIMIT_PER_MINUTE
    with connect() as db:
        begin_immediate(db)
        key_count = _rate_window_bump(db, "key", identity["key_id"], window)
        workspace_ref = identity["workspace_id"] or identity["owner_id"] or "workspace-default"
        workspace_count = _rate_window_bump(db, "workspace", workspace_ref, window)
    verdict = automation.rate_limit_verdict(key_count, key_limit, window, now)
    workspace_verdict = automation.rate_limit_verdict(workspace_count, workspace_limit, window, now)
    effective = key_limit if verdict["remaining"] <= workspace_verdict["remaining"] else workspace_limit
    remaining = min(verdict["remaining"], workspace_verdict["remaining"])
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": str(effective),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(verdict["reset_in"]),
        "X-RateLimit-Scope": "key+workspace",
    }
    blocked = (not verdict["allowed"] and "key") or (not workspace_verdict["allowed"] and "workspace") or None
    if blocked:
        request.state.rate_limit_headers["Retry-After"] = str(max(verdict["reset_in"], workspace_verdict["reset_in"]))
        raise HTTPException(429, {
            "code": "rate_limited",
            "detail": (f"超过每分钟限额（{'Key' if blocked == 'key' else '工作区'} {effective} 次/分钟）："
                       "这是本产品初始设置，可在部署压测后调整"),
            "limit_scope": blocked,
            "limits": {"per_key": key_limit, "per_workspace": workspace_limit},
            "counts": {"key": key_count, "workspace": workspace_count},
            "retry_after_seconds": max(verdict["reset_in"], workspace_verdict["reset_in"]),
        })
    if identity.get("budget_limit_amount") is not None and method in {"POST", "PATCH", "PUT", "DELETE"}:
        with connect() as db:
            spend = _key_period_spend(db, identity["key_id"], identity["budget_period"], now)
        if spend >= float(identity["budget_limit_amount"]):
            emit_event_standalone(
                event_type="budget.blocked", aggregate_type="automation_key", aggregate_id=identity["key_id"],
                aggregate_revision=0,
                payload={"key_id": identity["key_id"], "key_name": identity["name"], "block_code": "key_budget_exhausted",
                         "spend": spend, "limit": identity["budget_limit_amount"],
                         "period": identity["budget_period"], "status": "BLOCKED"},
            )
            raise HTTPException(409, {
                "code": "key_budget_exhausted",
                "detail": (f"该 Key 在 {identity['budget_period']} 周期内已发生 {spend}，"
                           f"达到上限 {identity['budget_limit_amount']}：后续写调用被阻断"),
                "spend": spend, "limit": identity["budget_limit_amount"], "period": identity["budget_period"],
            })
    with connect() as db:
        db.execute("UPDATE automation_keys SET last_used_at = ?, call_count = call_count + 1 WHERE key_id = ?",
                   (utc_now(), identity["key_id"]))


def _idempotency_gate(request: Request, scope: str, payload: dict) -> "IdempotencyGate":
    return IdempotencyGate(request, scope, payload)


class IdempotencyGate:
    """按 (owner, 调用方, 接口, 键) 隔离的幂等记录；同键不同体 → 409。"""

    def __init__(self, request: Request, scope: str, payload: dict):
        self.request = request
        self.scope = scope
        self.actor = current_actor_label()
        self.owner_id = security.current_owner.get() or DEFAULT_OWNER_ID
        self.key = (request.headers.get("idempotency-key") or "").strip() or None
        self.request_hash = automation.request_fingerprint(payload)
        self.required = security.current_automation_key.get() is not None and automation.idempotency_required(
            request.method, request.url.path.rstrip("/") or request.url.path
        )

    def replay(self) -> dict | None:
        if not self.key:
            if self.required:
                raise HTTPException(428, {
                    "code": "idempotency_key_required",
                    "detail": "Automation 调用创建任务/发布类接口必须带 Idempotency-Key 头（同键重放返回首次结果）",
                    "header": "Idempotency-Key",
                })
            return None
        with connect() as db:
            row = db.execute(
                "SELECT * FROM idempotency_records WHERE owner_id = ? AND actor = ? AND scope = ? AND idem_key = ?",
                (self.owner_id, self.actor, self.scope, self.key),
            ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != self.request_hash:
            raise HTTPException(409, {
                "code": "idempotency_conflict",
                "detail": "同一个 Idempotency-Key 被用于不同的请求内容：请换新键，避免误复用旧结果",
                "scope": self.scope,
            })
        return json.loads(row["response"])

    def store(self, response_body: dict, status_code: int) -> None:
        if not self.key:
            return
        with connect() as db:
            try:
                db.execute(
                    "INSERT INTO idempotency_records(id, owner_id, actor, scope, idem_key, request_hash, "
                    "status_code, response, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), self.owner_id, self.actor, self.scope, self.key, self.request_hash,
                     status_code, _canonical_json_text(response_body), utc_now()),
                )
            except sqlite3.IntegrityError:
                pass  # 并发同键：先写入者为准，重放方会读到它


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
    key_context = None
    member_context = None
    try:
        if request.method != "OPTIONS" and not request.url.path.startswith("/internal/"):
            if not (request.url.path == "/api/v1/session" and request.method == "POST"):
                owner = security.authenticate(request, connect, ALLOWED_ORIGINS)
                context = security.current_owner.set(owner)
                member_identity = getattr(request.state, "member", None)
                member_context = None
                if member_identity is not None and member_identity.get("role") != "owner":
                    member_context = security.current_member.set(member_identity)
                    enforce_member_access(request, member_identity)
                key_identity = getattr(request.state, "automation_key", None)
                if key_identity is not None:
                    key_context = security.current_automation_key.set(key_identity)
                    enforce_automation_access(request, key_identity)
        response = await call_next(request)
        headers = getattr(request.state, "rate_limit_headers", None)
        if headers:
            for name, value in headers.items():
                response.headers[name] = value
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=getattr(request.state, "rate_limit_headers", None) or {"Cache-Control": "no-store"})
    finally:
        if key_context is not None:
            security.current_automation_key.reset(key_context)
        if member_context is not None:
            security.current_member.reset(member_context)
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
    from PIL import Image as PILImage
    with PILImage.open(path) as image:
        if x+width > image.width or y+height > image.height:
            raise HTTPException(422, "3D重建裁切超出原图范围，请重新框选完整产品")
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
        "usage": record_internal_usage(
            provider="h3-comfyui", unit="video_generation_request", quantity=1.0,
            operation_id=provider_job_id, capability="video_generation",
            note=f"H3 视频生成请求 {provider_job_id}（自管 ComfyUI，内部估算）"),
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
            "INSERT INTO assets (id, name, kind, mime, size_bytes, sha256, path, created_at, owner_id, media_info) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, file.filename or stored.name, kind, mime, len(data), digest, stored.name, created,
             security.current_owner.get() or DEFAULT_OWNER_ID,
             json.dumps(glb_info, ensure_ascii=False) if glb_info else None),
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
    result = []
    for row in rows:
        item = {key: row[key] for key in row.keys() if key != "path"}
        raw = item.pop("media_info", None)
        try:
            item["media_info"] = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            item["media_info"] = None
        result.append(item)
    return result


@app.get("/api/v1/assets/{asset_id}/appearance")
def get_asset_appearance(asset_id: str) -> dict:
    """模型外观覆盖（V6-16，复核 BUG-04）：真实解析文件，报告贴图/顶点色/材质覆盖与风险等级。"""
    owner_id = security.current_owner.get() or DEFAULT_OWNER_ID
    with connect() as db:
        row = db.execute("SELECT * FROM assets WHERE id = ? AND owner_id = ?", (asset_id, owner_id)).fetchone()
    if not row:
        raise HTTPException(404, "素材不存在")
    asset = row_to_dict(row)
    if asset["kind"] != "model":
        return {"asset_id": asset_id, "kind": asset["kind"], "appearance_level": "NOT_APPLICABLE",
                "appearance_coverage": "IMAGE_SOURCE",
                "appearance_note": "图片素材的外观来自拍摄/渲染原图，不做 3D 材质分析",
                "checks": []}
    try:
        data = resolve_asset_path(asset).read_bytes()
    except HTTPException as exc:
        raise HTTPException(409, f"素材文件不可读：{exc.detail}") from exc
    info = inspect_glb(data)
    return {
        "asset_id": asset_id,
        "kind": "model",
        "sha256": asset["sha256"],
        "inspected_at": utc_now(),
        **info,
        "checks": [
            {"item": "贴图/纹理引用", "status": "有" if info["has_textures"] else "无"},
            {"item": "UV 坐标（TEXCOORD_0）", "status": "有" if info["has_uv"] else "无"},
            {"item": "顶点色（COLOR_0）", "status": "有" if info["has_vertex_color"] else "无"},
            {"item": "材质数", "status": str(info["materials"])},
            {"item": "不同材质颜色数", "status": str(info["distinct_material_colors"])},
        ],
        "next_steps": (["对照授权参考逐项核对形状与外观（面罩/镜片/软管/喇叭/底座）",
                        "修补可信材质与外观映射后再做正式广告",
                        "正式发布前必须有同角度静帧对照证据"]
                       if info["appearance_level"] != "VERIFIED_APPEARANCE" else
                       ["外观信息可用于高保真渲染；仍需同角度静帧对照确认"],
                       ),
    }


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
    return {"version": version, **analyze_glb_appearance(document)}


def analyze_glb_appearance(document: dict) -> dict:
    """外观覆盖分析（V6-16，独立复核 BUG-04）。

    诚实口径：**没有纹理不等于没有外观信息**——顶点色、多材质 PBR 参数也可以是有效外观。
    这里只报告"有哪些外观来源"，并给出一个可行动的风险等级；不做"无纹理一律拒绝"。
    """
    materials = document.get("materials") or []
    meshes = document.get("meshes") or []
    has_textures = bool(document.get("images") or document.get("textures"))
    base_colors = []
    for material in materials:
        factor = ((material.get("pbrMetallicRoughness") or {}).get("baseColorFactor")) or None
        if factor:
            base_colors.append([round(float(value), 4) for value in factor])
    textured_materials = 0
    for material in materials:
        pbr = material.get("pbrMetallicRoughness") or {}
        if pbr.get("baseColorTexture") or material.get("emissiveTexture") or material.get("normalTexture"):
            textured_materials += 1
    has_uv = False
    has_vertex_color = False
    for mesh in meshes:
        for primitive in mesh.get("primitives") or []:
            attributes = primitive.get("attributes") or {}
            if "TEXCOORD_0" in attributes:
                has_uv = True
            if "COLOR_0" in attributes:
                has_vertex_color = True
    distinct_colors = {tuple(color) for color in base_colors}
    if has_textures and textured_materials:
        coverage, level = "TEXTURED", "VERIFIED_APPEARANCE"
        note = "包含贴图与材质引用：外观信息可用于高保真渲染"
    elif has_textures:
        coverage, level = "TEXTURES_UNREFERENCED", "PARTIAL_APPEARANCE"
        note = "文件里有贴图但没有任何材质引用它：渲染不会用到这些贴图，需要检查导出设置"
    elif has_vertex_color:
        coverage, level = "VERTEX_COLOR", "VERIFIED_APPEARANCE"
        note = "使用顶点色表达外观：不依赖贴图也可以有真实外观"
    elif len(distinct_colors) > 1:
        coverage, level = "MULTI_MATERIAL_COLORS", "PARTIAL_APPEARANCE"
        note = "多材质纯色：能区分部件，但无贴图细节（如镜片渐变、面罩高光）"
    elif len(distinct_colors) == 1:
        coverage, level = "SINGLE_FLAT_MATERIAL", "APPEARANCE_UNVERIFIED"
        note = ("只有单一纯色材质且无贴图/顶点色/UV：几何预演可用，但**不能**据此声称外观保真；"
                "需要对照授权参考逐项核对，或提供带贴图/多材质的模型")
    else:
        coverage, level = "UNKNOWN", "APPEARANCE_UNVERIFIED"
        note = "未解析到材质信息：外观状态未知，需要人工核对"
    return {
        "meshes": len(meshes),
        "materials": len(materials),
        "images": len(document.get("images") or []),
        "has_textures": has_textures,
        "has_uv": has_uv,
        "has_vertex_color": has_vertex_color,
        "base_color_factors": base_colors[:8],
        "distinct_material_colors": len(distinct_colors),
        "appearance_coverage": coverage,
        "appearance_level": level,
        "appearance_note": note,
        "honest_boundary": ("外观等级只描述输入素材本身；渲染是否保真还需对照授权参考逐项核对"
                            "（面罩/镜片/软管/喇叭/底座等）"),
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


@app.get("/api/v1/plans")
def list_plans(owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
    """计划列表（V4 互动界面选择计划用；单 Owner 应用，按时间倒序）。"""
    with connect() as db:
        rows = db.execute("SELECT * FROM plans ORDER BY created_at DESC LIMIT 200").fetchall()
    return [
        {
            "id": row["id"],
            "product_asset_id": row["product_asset_id"],
            "intent": row["intent"],
            "approved": bool(row["approved"]),
            "created_at": row["created_at"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
        }
        for row in rows
    ]


@app.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str) -> dict:
    """计划详情（V5 双栏对照读取目标分镜）。"""
    with connect() as db:
        row = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not row:
        raise HTTPException(404, "计划不存在")
    return {
        "id": row["id"],
        "product_asset_id": row["product_asset_id"],
        "intent": row["intent"],
        "approved": bool(row["approved"]),
        "created_at": row["created_at"],
        "payload": json.loads(row["payload"]) if row["payload"] else {},
    }


@app.get("/api/v1/plans/{plan_id}/contracts")
def list_plan_contracts(
    plan_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    """计划合同版本列表（V3 审核界面创建 Strict Run 时需要冻结合同 id）。"""
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "计划不存在")
        rows = db.execute(
            "SELECT pc.* FROM plan_contracts pc "
            "JOIN product_versions pv ON pv.id = pc.product_version_id "
            "WHERE pc.plan_id = ? AND pv.owner_id = ? AND pv.project_id = ? "
            "ORDER BY pc.version DESC",
            (plan_id, owner_id, project_id),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "product_version_id": row["product_version_id"],
            "version": row["version"],
            "contract_status": row["contract_status"],
            "payload_sha256": row["payload_sha256"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


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


def product_review_public(row) -> dict:
    return {
        "id": row["id"],
        "product_version_id": row["product_version_id"],
        "decision": row["decision"],
        "source_kind": row["source_kind"],
        "reviewer": row["reviewer"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "review": json.loads(row["payload"]),
    }


@app.post("/api/v1/product-versions/{product_version_id}/reviews", status_code=201)
def create_product_review(product_version_id: str, request: ProductReviewRequest) -> dict:
    """冻结产品版本审核，并把核实结果绑定到该版本。"""
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    # 证据素材必须存在且属于同一 owner，避免引用他人的素材当证据。
    with connect() as db:
        for asset_id in request.evidence_asset_ids:
            asset = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if not asset:
                raise HTTPException(404, f"证据素材不存在: {asset_id}")
            if asset["owner_id"] != owner_id:
                raise HTTPException(403, f"证据素材不属于当前 Owner: {asset_id}")
    payload = {
        "decision": request.decision,
        "verified_dimensions": request.verified_dimensions,
        "view_coverage": list(request.view_coverage),
        "unverified_regions": list(request.unverified_regions),
        "logo_regions": [region.model_dump() for region in request.logo_regions],
        "camera_visibility_constraints": list(request.camera_visibility_constraints),
        "evidence_asset_ids": list(request.evidence_asset_ids),
        "source_kind": request.source_kind,
        "notes": request.notes,
    }
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    review_id = str(uuid.uuid4())
    with connect() as db:
        begin_immediate(db)
        db.execute(
            "INSERT INTO product_reviews (id, product_version_id, decision, source_kind, payload, payload_sha256, reviewer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (review_id, product_version_id, request.decision, request.source_kind,
             payload_text, digest, owner_id, utc_now()),
        )
        # 审核结果绑定到版本：核实信息随版本可查，供合成与 QA 使用。
        db.execute(
            "UPDATE product_versions SET verified_dimensions = ?, view_coverage = ?, unverified_regions = ?, "
            "logo_regions = ?, camera_visibility_constraints = ? WHERE id = ?",
            (
                json.dumps(request.verified_dimensions, ensure_ascii=False),
                json.dumps(list(request.view_coverage), ensure_ascii=False),
                json.dumps(list(request.unverified_regions), ensure_ascii=False),
                json.dumps([region.model_dump() for region in request.logo_regions], ensure_ascii=False),
                json.dumps(list(request.camera_visibility_constraints), ensure_ascii=False),
                product_version_id,
            ),
        )
    with connect() as db:
        row = db.execute("SELECT * FROM product_reviews WHERE id = ?", (review_id,)).fetchone()
    return product_review_public(row)


@app.get("/api/v1/product-versions/{product_version_id}/reviews")
def list_product_reviews(
    product_version_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM product_reviews WHERE product_version_id = ? ORDER BY created_at DESC",
            (product_version_id,),
        ).fetchall()
    return [product_review_public(row) for row in rows]


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
        "background_workflows": [item.model_dump() for item in request.background_workflows],
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


def qa_threshold_set_public(row) -> dict:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "project_id": row["project_id"],
        "product_version_id": row["product_version_id"],
        "threshold_set_version": row["threshold_set_version"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "thresholds": json.loads(row["payload"]),
    }


@app.post("/api/v1/qa-threshold-sets", status_code=201)
def create_qa_threshold_set(request: QaThresholdSetRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(request.owner_id, request.project_id)
    if request.product_version_id is not None:
        load_product_version_for_owner(request.product_version_id, owner_id, project_id)
    payload = _validate_qa_threshold_payload({
        **request.thresholds,
        "threshold_set_version": request.threshold_set_version,
        "threshold_set_id": hashlib.sha256(
            json.dumps(request.thresholds, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32],
    })
    payload_text = _canonical_json_text(payload)
    digest = _qa_threshold_payload_sha256(payload)
    with connect() as db:
        begin_immediate(db)
        existing = db.execute(
            "SELECT id FROM qa_threshold_sets WHERE owner_id = ? AND project_id = ? AND product_version_id IS ? AND threshold_set_version = ?",
            (owner_id, project_id, request.product_version_id, request.threshold_set_version),
        ).fetchone()
        if existing:
            raise HTTPException(409, "同名 QA 阈值集已冻结，请使用新版本号")
        db.execute(
            "INSERT INTO qa_threshold_sets(id, owner_id, project_id, product_version_id, threshold_set_version, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["threshold_set_id"], owner_id, project_id, request.product_version_id,
                request.threshold_set_version, payload_text, digest, utc_now(),
            ),
        )
        row = db.execute("SELECT * FROM qa_threshold_sets WHERE id = ?", (payload["threshold_set_id"],)).fetchone()
    return qa_threshold_set_public(row)


@app.get("/api/v1/qa-threshold-sets")
def list_qa_threshold_sets(
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
    product_version_id: str | None = None,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        if product_version_id is None:
            rows = db.execute(
                "SELECT * FROM qa_threshold_sets WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC",
                (owner_id, project_id),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM qa_threshold_sets WHERE owner_id = ? AND project_id = ? AND (product_version_id IS NULL OR product_version_id = ?) ORDER BY created_at DESC",
                (owner_id, project_id, product_version_id),
            ).fetchall()
    return [qa_threshold_set_public(row) for row in rows]


def qa_report_public(row, db=None) -> dict:
    payload = {
        "id": row["id"],
        "run_id": row["run_id"],
        "job_id": row["job_id"],
        "threshold_set_id": row["threshold_set_id"],
        "threshold_set_version": row["threshold_set_version"],
        "payload_sha256": row["payload_sha256"],
        "status": row["status"],
        "manifest_sha256": row["manifest_sha256"],
        "binding_sha256": row["binding_sha256"],
        "decision": row["decision"],
        "decision_notes": row["decision_notes"],
        "decision_manifest_sha256": row["decision_manifest_sha256"],
        "decided_at": row["decided_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "report": json.loads(row["payload"]),
    }
    if db is None:
        payload["decision_valid"] = None
        payload["effective_decision"] = row["decision"]
        payload["decision_invalid_reason"] = None
    else:
        validity = _qa_report_decision_validity(db, row)
        payload["decision_valid"] = validity["decision_valid"]
        payload["effective_decision"] = validity["effective_decision"]
        payload["decision_invalid_reason"] = validity["reason"]
    return payload


@app.post("/api/v1/runs/{run_id}/qa")
def run_qa_for_run(run_id: str, request: QaRunRequest | None = None) -> dict:
    request = request or QaRunRequest()
    resolve_run_owner_scope(run_id)
    with connect() as db:
        context = _qa_run_context(db, run_id)
        threshold_set = load_qa_threshold_set(
            db,
            context["run"]["owner_id"],
            context["run"].get("project_id") or DEFAULT_PROJECT_ID,
            request.threshold_set_id,
            request.threshold_set_version,
            context["snapshot"].get("product_version_id"),
        )
    run_dir = RUNS / context["job_id"]
    strict_root = run_dir / "strict"
    output = run_dir / "strict_preview.mp4"
    media_report = None
    if output.exists() and FFMPEG:
        media_report = media_quality_report(output, context["plan_payload"], context["output_spec"], run_dir / "qa_strict_api")
        media_report["computed_by"] = "productdirector.media_quality_report"
        media_report["source_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    qa_report = strict_qa.run_strict_qa(
        strict_root,
        context["plan_payload"],
        context["output_spec"],
        context["snapshot"],
        context["policy_payload"],
        context["review_payload"],
        threshold_set["payload"],
        controlled_evidence=context["controlled_evidence"],
        media_report=media_report,
        media_file=output if output.exists() else None,
        asset_path=context["asset_path"],
    )
    (strict_root / "qa_report.json").write_text(json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8")
    job = get_job(context["job_id"])
    manifest = _job_manifest_payload(context["job_id"], job.get("manifest_path"))
    if not isinstance(manifest, dict):
        raise HTTPException(409, "Run 尚未生成可信 manifest，不能绑定 QA 审批")
    manifest_sha256 = hashlib.sha256(_canonical_json_text(manifest).encode("utf-8")).hexdigest()
    with connect() as db:
        report_id = _persist_qa_report(db, run_id, context["job_id"], qa_report, threshold_set, manifest_sha256)
    if qa_report["fatal_failures"]:
        update_job(context["job_id"], status="QA_REJECTED", stage="QA_REJECTED", error="；".join(qa_report["fatal_failures"][:12]), progress=100)
    return {
        "run_id": run_id,
        "job_id": context["job_id"],
        "qa_report_id": report_id,
        "manifest_sha256": manifest_sha256,
        "status": qa_report["status"],
        "fatal_failures": qa_report["fatal_failures"],
        "not_verified": qa_report["not_verified"],
    }


@app.get("/api/v1/qa-reports/{report_id}")
def get_qa_report(report_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM qa_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "QA 报告不存在")
        resolve_run_owner_scope(row["run_id"])
        return qa_report_public(row, db)


@app.get("/api/v1/qa-reports")
def list_qa_reports(
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
    job_id: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    """QA 报告列表（可按 job/run 过滤）；供审核界面按任务定位质检记录。"""
    conditions: list[str] = []
    values: list[str] = []
    if job_id:
        conditions.append("job_id = ?")
        values.append(job_id)
    if run_id:
        conditions.append("run_id = ?")
        values.append(run_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM qa_reports{where} ORDER BY created_at DESC LIMIT 200",
            values,
        ).fetchall()
    return [qa_report_public(row) for row in rows]


STRICT_PREVIEW_CHANNELS: dict[str, tuple[str, str, str]] = {
    # channel -> (strict 子目录, 文件名模板, media type)；只放浏览器可预览的 PNG 通道。
    # EXR 通道（beauty/alpha/depth/normal）需要专门查看器，当前接口如实在 404 中说明。
    "product": ("product", "frame_{frame:04d}.png", "image/png"),
    "mask": ("mask", "frame_{frame:04d}.png", "image/png"),
    "background": ("background", "frame_{frame:04d}.png", "image/png"),
    "composite": ("composite_out", "composite_{frame:04d}.png", "image/png"),
    "passes_mask": ("passes/mask", "frame_{frame:04d}.png", "image/png"),
}


@app.get("/api/v1/runs/{run_id}/strict-artifacts/{channel}/{frame}")
def get_strict_artifact_frame(run_id: str, channel: str, frame: int) -> FileResponse:
    """V3-06 通道查看器的受鉴权帧预览：只放行白名单 PNG 通道与合法帧号。"""
    if channel not in STRICT_PREVIEW_CHANNELS:
        raise HTTPException(
            404,
            "通道不可预览或不存在；beauty/alpha/depth/normal 为 EXR 原始通道，需专用查看器",
        )
    if frame < 1 or frame > 100000:
        raise HTTPException(404, "帧号无效")
    resolve_run_owner_scope(run_id)
    with connect() as db:
        row = db.execute("SELECT job_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Run 不存在")
    folder, pattern, media_type = STRICT_PREVIEW_CHANNELS[channel]
    strict_root = (RUNS / row["job_id"] / "strict").resolve()
    target = (strict_root / folder / pattern.format(frame=frame)).resolve()
    if strict_root not in target.parents:
        raise HTTPException(403, "非法路径")
    if not target.is_file():
        raise HTTPException(404, f"该通道帧不存在：{channel} 帧 {frame}")
    return FileResponse(target, media_type=media_type)


@app.post("/api/v1/qa-reports/{report_id}/decisions")
def decide_qa_report(report_id: str, request: QaDecisionRequest) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM qa_reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, "QA 报告不存在")
    with connect() as db:
        latest = db.execute(
            "SELECT id FROM qa_reports WHERE job_id = ? ORDER BY created_at DESC, updated_at DESC LIMIT 1",
            (row["job_id"],),
        ).fetchone()
        if not latest or latest["id"] != report_id:
            raise HTTPException(409, "已有更新的 QA 报告，旧报告不能审批")
    resolve_run_owner_scope(row["run_id"])
    with connect() as db:
        context = _qa_run_context(db, row["run_id"])
    job = get_job(context["job_id"])
    manifest = _job_manifest_payload(context["job_id"], job.get("manifest_path"))
    if not isinstance(manifest, dict):
        raise HTTPException(409, "当前 Job 缺少可信 manifest 文件，不能批准")
    current_manifest_sha256 = hashlib.sha256(_canonical_json_text(manifest).encode("utf-8")).hexdigest()
    if request.manifest_hash.lower() != current_manifest_sha256:
        raise HTTPException(409, "manifest_hash 与 QA 报告绑定清单不一致")
    if request.manifest_hash.lower() != (row["manifest_sha256"] or "").lower():
        raise HTTPException(409, "manifest_hash 与 QA 报告当前清单不一致")
    manifest_qa_report = manifest.get("qa_report")
    if not isinstance(manifest_qa_report, dict):
        raise HTTPException(409, "Manifest 缺少 QA 报告，不能批准")
    if hashlib.sha256(_canonical_json_text(manifest_qa_report).encode("utf-8")).hexdigest() != row["payload_sha256"]:
        raise HTTPException(409, "Manifest 中的 QA 报告与已冻结报告不一致")
    bound_failures = _verify_manifest_bound_files(context["job_id"], manifest, context["output_spec"])
    if bound_failures:
        raise HTTPException(409, "Strict 输入/产物已变化: " + "；".join(bound_failures[:8]))
    if request.decision == "APPROVED":
        if row["status"] != "PASS":
            raise HTTPException(409, "QA 状态不是 PASS，不能批准为 Strict 成果")
    with connect() as db:
        begin_immediate(db)
        latest = db.execute("SELECT * FROM qa_reports WHERE id = ?", (report_id,)).fetchone()
        current_binding = _qa_binding_sha256(
            db,
            latest["run_id"],
            current_manifest_sha256,
            db.execute("SELECT payload_sha256 FROM qa_threshold_sets WHERE id = ?", (latest["threshold_set_id"],)).fetchone()["payload_sha256"],
            latest["payload_sha256"],
        )
        if current_binding != latest["binding_sha256"]:
            raise HTTPException(409, "QA 绑定输入已变化，旧批准/决策失效")
        now = utc_now()
        db.execute(
            "UPDATE qa_reports SET decision = ?, decision_notes = ?, decision_manifest_sha256 = ?, decided_at = ?, updated_at = ? WHERE id = ?",
            (request.decision, request.notes, request.manifest_hash.lower(), now, now, report_id),
        )
        updated = db.execute("SELECT * FROM qa_reports WHERE id = ?", (report_id,)).fetchone()
    return qa_report_public(updated, db)


# ---------------------------------------------------------------------------
# V5-01：参考视频上传、代理与时间戳映射、URL 获取约束
# ---------------------------------------------------------------------------

V5_MAX_REFERENCE_BYTES = 500 * 1024 * 1024
V5_URL_FETCH_TIMEOUT = 60
V5_PROXY_MAX_WIDTH = 640


def _url_host_is_public(hostname: str) -> bool:
    """URL 直连获取只允许公网主机（防 SSRF；登录/DRM/访问控制媒体一律不绕过）。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def _fetch_reference_url(url: str, workdir: Path) -> Path:
    """仅支持可直接公开取得的媒体 URL；任何失败都提示改用本地上传。"""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise RuntimeError("仅支持 http(s) 直连媒体地址；无法访问的链接请上传本地文件")
    if not _url_host_is_public(parts.hostname):
        raise RuntimeError("拒绝访问内网/本机地址；无法访问的链接请上传本地文件")
    request = urllib.request.Request(url, headers={"User-Agent": "ProductDirectorAI/1.0"})
    target = workdir / "source.mp4"
    with urllib.request.urlopen(request, timeout=V5_URL_FETCH_TIMEOUT) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("video/"):
            raise RuntimeError("链接不是可直接取得的视频媒体；请上传本地文件")
        length_header = response.headers.get("Content-Length")
        if length_header and int(length_header) > V5_MAX_REFERENCE_BYTES:
            raise RuntimeError("媒体超过大小上限")
        data = response.read(V5_MAX_REFERENCE_BYTES + 1)
        if len(data) > V5_MAX_REFERENCE_BYTES:
            raise RuntimeError("媒体超过大小上限")
        target.write_bytes(data)
    return target


def _ingest_reference_media(source_path: Path, workdir: Path) -> dict:
    """ffprobe 检测 + 生成分析代理 + 时间戳映射（source_to_proxy_map）。"""
    if not FFPROBE or not FFMPEG:
        raise RuntimeError("ffprobe/ffmpeg 未安装或未找到")
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,width,height:format=duration",
         "-of", "json", str(source_path)],
        capture_output=True,
    )
    if probe.returncode != 0:
        raise RuntimeError("无法解析媒体（损坏或不支持的格式）；请提供有效视频文件")
    try:
        info = json.loads(probe.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("无法解析媒体信息") from exc
    stream = (info.get("streams") or [{}])[0]
    fmt = info.get("format") or {}
    rate_text = stream.get("r_frame_rate") or ""
    numerator, _, denominator = rate_text.partition("/")
    try:
        fps = float(numerator) / float(denominator) if denominator else None
    except (ValueError, ZeroDivisionError):
        fps = None
    duration = float(fmt.get("duration") or 0.0)
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    proxy_path = workdir / "proxy.mp4"
    proxy_width = min(V5_PROXY_MAX_WIDTH, width) if width else V5_PROXY_MAX_WIDTH
    proxy_height = max(1, round(height * proxy_width / width)) if width else max(1, height)
    encode = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(source_path),
         "-vf", f"scale={proxy_width}:{proxy_height}", "-an",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(proxy_path)],
        capture_output=True,
    )
    if encode.returncode != 0 or not proxy_path.exists():
        raise RuntimeError("分析代理生成失败")
    proxy_probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration", "-of", "json", str(proxy_path)],
        capture_output=True,
    )
    proxy_duration = duration
    if proxy_probe.returncode == 0:
        try:
            proxy_duration = float(json.loads(proxy_probe.stdout.decode("utf-8", "replace")).get("format", {}).get("duration") or duration)
        except (json.JSONDecodeError, ValueError):
            proxy_duration = duration
    return {
        "source": {
            "width": width, "height": height, "fps": fps,
            "duration_s": round(duration, 4), "sha256": _file_sha256(source_path),
        },
        "proxy": {
            "width": proxy_width, "height": proxy_height, "fps": fps,
            "duration_s": round(proxy_duration, 4), "sha256": _file_sha256(proxy_path),
        },
        "timebase": {
            "fps": fps, "duration_s": round(duration, 4),
            "estimated_frames": int(round(duration * fps)) if fps else 0,
        },
        "source_to_proxy_map": {
            "scale_width": proxy_width / width if width else 1.0,
            "scale_height": proxy_height / height if height else 1.0,
            "fps_ratio": 1.0,
            "source_duration_s": round(duration, 4),
            "proxy_duration_s": round(proxy_duration, 4),
        },
    }


class ReferenceRequest(BaseModel):
    uploaded_asset_id: str | None = None
    source_url: str | None = None
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.uploaded_asset_id is None) == (self.source_url is None):
            raise ValueError("必须且只能提供 uploaded_asset_id 或 source_url 之一")
        return self


def _reference_public(row) -> dict:
    payload = json.loads(row["payload"]) if row["payload"] else {}
    return {
        "id": row["id"],
        "source_kind": row["source_kind"],
        "uploaded_asset_id": row["uploaded_asset_id"],
        "source_url": row["source_url"],
        "status": row["status"],
        "error": row["error"],
        "source_hash": row["source_hash"],
        "proxy_hash": row["proxy_hash"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "payload": payload,
    }


@app.post("/api/v1/references", status_code=201)
def create_reference(request: ReferenceRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    source_kind = "uploaded_asset" if request.uploaded_asset_id else "source_url"

    def resolve_source(workdir: Path) -> Path:
        if request.uploaded_asset_id:
            with connect() as db:
                asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.uploaded_asset_id,)).fetchone()
            if not asset:
                raise HTTPException(404, "素材不存在")
            if asset["owner_id"] != owner_id:
                raise HTTPException(403, "素材不属于当前 Owner")
            if asset["kind"] != "video":
                raise HTTPException(422, "参考素材必须是视频")
            return Path(resolve_asset_path(row_to_dict(asset)))
        return _fetch_reference_url(request.source_url or "", workdir)

    return _ingest_and_store_reference(
        owner_id, project_id, source_kind, resolve_source,
        uploaded_asset_id=request.uploaded_asset_id, source_url=request.source_url,
    )


@app.post("/api/v1/references/upload", status_code=201)
def upload_reference(file: UploadFile = File(...)) -> dict:
    """本地文件上传入口：V1 素材库只接受图片/GLB，参考视频走独立入口并落盘到参考工作区。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if not (file.content_type or "").lower().startswith("video/"):
        raise HTTPException(422, "参考文件必须是视频")

    def resolve_source(workdir: Path) -> Path:
        target = workdir / "source.mp4"
        total = 0
        with open(target, "wb") as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > V5_MAX_REFERENCE_BYTES:
                    raise RuntimeError("媒体超过大小上限")
                handle.write(chunk)
        if total == 0:
            raise RuntimeError("上传文件为空")
        return target

    return _ingest_and_store_reference(owner_id, project_id, "uploaded_file", resolve_source)


def _ingest_and_store_reference(
    owner_id: str, project_id: str, source_kind: str, resolve_source,
    uploaded_asset_id: str | None = None, source_url: str | None = None,
) -> dict:
    reference_id = str(uuid.uuid4())
    workdir = VAR / "references" / reference_id
    workdir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"source_kind": source_kind}
    status = "READY"
    error = None
    source_hash = None
    proxy_hash = None
    try:
        source_path = resolve_source(workdir)
        payload = _ingest_reference_media(source_path, workdir)
        source_hash = payload["source"]["sha256"]
        proxy_hash = payload["proxy"]["sha256"]
    except HTTPException:
        raise
    except Exception as exc:
        status = "BLOCKED" if source_kind == "source_url" else "FAILED"
        error = str(exc) + ("（无法取回链接时请上传本地文件）" if source_kind == "source_url" else "")
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO reference_assets(id, owner_id, project_id, source_kind, uploaded_asset_id, source_url, status, error, source_hash, proxy_hash, payload, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (reference_id, owner_id, project_id, source_kind, uploaded_asset_id,
             source_url, status, error, source_hash, proxy_hash,
             json.dumps(payload, ensure_ascii=False), now, now),
        )
        row = db.execute("SELECT * FROM reference_assets WHERE id = ?", (reference_id,)).fetchone()
    return _reference_public(row)


@app.get("/api/v1/references")
def list_references(owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM reference_assets WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC LIMIT 200",
            (owner_id, project_id),
        ).fetchall()
    return [_reference_public(row) for row in rows]


@app.get("/api/v1/references/{reference_id}")
def get_reference(reference_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM reference_assets WHERE id = ?", (reference_id,)).fetchone()
    if not row:
        raise HTTPException(404, "参考视频不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问参考视频")
    return _reference_public(row)


@app.get("/api/v1/references/{reference_id}/proxy")
def get_reference_proxy(reference_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM reference_assets WHERE id = ?", (reference_id,)).fetchone()
    if not row:
        raise HTTPException(404, "参考视频不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问参考视频")
    if row["status"] != "READY":
        raise HTTPException(409, "参考视频尚未完成摄取")
    proxy_path = VAR / "references" / reference_id / "proxy.mp4"
    if not proxy_path.exists():
        raise HTTPException(404, "代理文件不存在")
    return FileResponse(proxy_path, media_type="video/mp4", filename="reference_proxy.mp4")


# ---------------------------------------------------------------------------
# V5-02：本地切镜、编辑与冻结分析版本
# ---------------------------------------------------------------------------

class ReferenceAnalyzeRequest(BaseModel):
    scope: Literal["cuts"] = "cuts"
    sample_policy: Literal["uniform"] = "uniform"
    notes: str = Field(default="", max_length=2000)


class ReferenceAnalysisEditRequest(BaseModel):
    """仅草稿可编辑；提交后生成新修订并记录 edited_from。segments 为人工修订后的分段。"""
    segments: list[dict] = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)


def _reference_analysis_public(row) -> dict:
    return {
        "id": row["id"],
        "reference_id": row["reference_id"],
        "revision": row["revision"],
        "status": row["status"],
        "payload_sha256": row["payload_sha256"],
        "edited_from_revision": row["edited_from_revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "analysis": json.loads(row["payload"]),
    }


def _load_reference_owned(reference_id: str, owner_id: str, project_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM reference_assets WHERE id = ?", (reference_id,)).fetchone()
    if not row:
        raise HTTPException(404, "参考视频不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问参考视频")
    return row


@app.post("/api/v1/references/{reference_id}/analyze", status_code=201)
def analyze_reference(reference_id: str, request: ReferenceAnalyzeRequest | None = None) -> dict:
    """本地切镜分析（同步执行 ffmpeg scene 检测；结果如实带算法与阈值说明）。"""
    request = request or ReferenceAnalyzeRequest()
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    reference = _load_reference_owned(reference_id, owner_id, project_id)
    if reference["status"] != "READY":
        raise HTTPException(409, "参考视频尚未完成摄取")
    proxy_path = VAR / "references" / reference_id / "proxy.mp4"
    try:
        payload = reference_analysis.segment_reference(
            proxy_path, json.loads(reference["payload"]).get("timebase", {}).get("fps")
        )
        observations = reference_analysis.segment_observations(
            proxy_path, payload["segments"], VAR / "references" / reference_id
        )
        for segment in payload["segments"]:
            segment["observations"] = observations.get(segment["index"], {})
    except Exception as exc:
        raise HTTPException(422, f"切镜分析失败: {exc}") from exc
    payload["scope"] = request.scope
    payload["sample_policy"] = request.sample_policy
    payload["notes"] = request.notes
    payload_text = _canonical_json_text(payload)
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    analysis_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        begin_immediate(db)
        latest = db.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM reference_analyses WHERE reference_id = ?",
            (reference_id,),
        ).fetchone()
        revision = int(latest["revision"]) + 1
        db.execute(
            "INSERT INTO reference_analyses(id, reference_id, revision, status, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?)",
            (analysis_id, reference_id, revision, payload_text, digest, now, now),
        )
        row = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (analysis_id,)).fetchone()
    return _reference_analysis_public(row)


@app.get("/api/v1/references/{reference_id}/analyses")
def list_reference_analyses(reference_id: str) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    _load_reference_owned(reference_id, owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM reference_analyses WHERE reference_id = ? ORDER BY revision DESC",
            (reference_id,),
        ).fetchall()
    return [_reference_analysis_public(row) for row in rows]


@app.get("/api/v1/reference-analyses/{analysis_id}")
def get_reference_analysis(analysis_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not row:
        raise HTTPException(404, "分析不存在")
    _load_reference_owned(row["reference_id"], owner_id, project_id)
    return _reference_analysis_public(row)


@app.patch("/api/v1/reference-analyses/{analysis_id}")
def edit_reference_analysis(analysis_id: str, request: ReferenceAnalysisEditRequest) -> dict:
    """人工修订切镜：仅草稿可编辑；生成新修订并记录 edited_from（修订有来源）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not row:
        raise HTTPException(404, "分析不存在")
    _load_reference_owned(row["reference_id"], owner_id, project_id)
    if row["status"] != "DRAFT":
        raise HTTPException(409, "已批准的分析不可编辑；请基于新修订修改")
    base = json.loads(row["payload"])
    payload = {
        **base,
        "segments": request.segments,
        "edited": True,
        "edited_notes": request.notes,
    }
    payload_text = _canonical_json_text(payload)
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    new_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        begin_immediate(db)
        latest = db.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM reference_analyses WHERE reference_id = ?",
            (row["reference_id"],),
        ).fetchone()
        revision = int(latest["revision"]) + 1
        db.execute(
            "INSERT INTO reference_analyses(id, reference_id, revision, status, payload, payload_sha256, edited_from_revision, created_at, updated_at) "
            "VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?)",
            (new_id, row["reference_id"], revision, payload_text, digest, row["revision"], now, now),
        )
        created = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (new_id,)).fetchone()
    return _reference_analysis_public(created)


@app.post("/api/v1/reference-analyses/{analysis_id}/approve")
def approve_reference_analysis(analysis_id: str) -> dict:
    """冻结分析版本（幂等）；批准后不可编辑，修订有来源。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if not row:
        raise HTTPException(404, "分析不存在")
    _load_reference_owned(row["reference_id"], owner_id, project_id)
    if row["status"] == "APPROVED":
        return _reference_analysis_public(row)
    with connect() as db:
        db.execute(
            "UPDATE reference_analyses SET status = 'APPROVED', updated_at = ? WHERE id = ?",
            (utc_now(), analysis_id),
        )
        updated = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (analysis_id,)).fetchone()
    return _reference_analysis_public(updated)


# ---------------------------------------------------------------------------
# V5-04：ReferenceMapping 与从参考生成目标计划
# ---------------------------------------------------------------------------

V5_REUSE_DIMENSIONS = {"duration", "shot_size", "composition", "motion_direction", "action_rhythm", "transition"}
V5_MOTION_TO_CAMERA = {"static": "static", "moving": "side_track"}
# V5 适配规划的确定性构图：按镜头序号循环取景（V3-07 校准机位族），目标对准产品中心。
# 构图属于 changed_dimensions；取景高度 0.21m 按 CC0 相机口径标定，属启发式适配而非测量。
V5_COMPOSITION_VIEWPOINTS = [
    {"position_m": [0.0, -1.85, 0.21]},
    {"position_m": [-0.55, -1.7, 0.26]},
    {"position_m": [0.55, -1.7, 0.26]},
]


class FromReferenceRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=80)
    product_version_id: str = Field(min_length=1, max_length=80)
    selected_dimensions: list[str] = Field(min_length=1, max_length=8)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_dimensions(self):
        unknown = [item for item in self.selected_dimensions if item not in V5_REUSE_DIMENSIONS]
        if unknown:
            raise ValueError(f"未知复用维度: {unknown}（可用: {sorted(V5_REUSE_DIMENSIONS)}）")
        return self


@app.post("/api/v1/projects/{project_id}/plans/from-reference", status_code=201)
def create_plan_from_reference(project_id: str, request: FromReferenceRequest) -> dict:
    """把已批准的分析分段映射为本产品可执行 DirectorPlan（适配规划 + 能力校验）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, project_id)
    with connect() as db:
        analysis = db.execute("SELECT * FROM reference_analyses WHERE id = ?", (request.analysis_id,)).fetchone()
        if not analysis:
            raise HTTPException(404, "分析不存在")
        if analysis["status"] != "APPROVED":
            raise HTTPException(409, "必须使用已批准的分析版本")
        reference = db.execute(
            "SELECT * FROM reference_assets WHERE id = ?", (analysis["reference_id"],),
        ).fetchone()
        version = load_product_version_for_owner(request.product_version_id, owner_id, project_id)
    if reference["owner_id"] != owner_id:
        raise HTTPException(403, "参考视频不属于当前 Owner")
    analysis_payload = json.loads(analysis["payload"])
    segments = analysis_payload.get("segments", [])
    if not segments:
        raise HTTPException(409, "分析没有可用分段")
    asset_id = version["product_asset_id"]
    reference_payload = json.loads(reference["payload"]) if reference["payload"] else {}
    reference_duration_s = float(
        (reference_payload.get("timebase") or {}).get("duration_s") or 0.0
    )
    # 适配规划：源分段 → 可执行 Shot（保留时长按目标 fps 量化为整帧，误差记录在映射中）
    shots = []
    mapping_shots = []
    total_duration_error_frames = 0.0
    total_frames = 0
    for index, segment in enumerate(segments):
        start_s = float(segment["start_s"])
        end_s = segment["end_s"]
        if end_s is not None:
            source_duration_s = float(end_s) - start_s
        elif reference_duration_s > start_s:
            # 末尾开放分段（end_s=null 表示"到片尾"）用参考片实际时长求解，不静默假设 1 秒
            source_duration_s = reference_duration_s - start_s
        else:
            source_duration_s = 1.0
        # 重演计划不套用 V1 导演的 24 帧最小镜头约束；量化整帧即可（误差门 ≤1 帧/镜头）。
        duration_frames = max(1, int(round(source_duration_s * DEFAULT_FPS)))
        duration_error_frames = duration_frames - source_duration_s * DEFAULT_FPS
        total_duration_error_frames += duration_error_frames
        total_frames += duration_frames
        observations = segment.get("observations", {})
        motion = (observations.get("motion_type") or {}).get("value") or "static"
        camera = V5_MOTION_TO_CAMERA.get(motion, "static")
        transition = segment.get("transition_in")
        shots.append({
            "id": f"shot_{index + 1:02d}",
            "name": f"参考重演 {index + 1}",
            "camera": camera,
            "focal_length_mm": 35,
            "duration_frames": duration_frames,
            "camera_target_m": [0.0, 0.0, 0.21],
            "camera_path": {
                "type": "static",
                **V5_COMPOSITION_VIEWPOINTS[index % len(V5_COMPOSITION_VIEWPOINTS)],
            },
        })
        mapping_shots.append({
            "source_segment_index": segment["index"],
            "source_range_s": [round(start_s, 3), round(end_s, 3) if end_s is not None else None],
            "target_shot_id": f"shot_{index + 1:02d}",
            "target_frames": [1 + (total_frames - duration_frames), total_frames],
            "kept_dimensions": sorted(set(request.selected_dimensions) & {"duration", "motion_direction", "transition"}),
            "changed_dimensions": ["shot_size", "composition", "action_rhythm"],
            "unsupported_dimensions": [item for item in request.selected_dimensions if item in {"shot_size", "action_rhythm"}],
            "duration_source_s": round(source_duration_s, 3),
            "duration_target_frames": duration_frames,
            "duration_error_frames": round(duration_error_frames, 2),
            "motion_type": motion,
            "camera": camera,
        })
    plan_id = str(uuid.uuid4())
    created = utc_now()
    payload = {
        "schema_version": "1.0",
        "product_asset_id": asset_id,
        "intent": f"参考重演：{reference['id'][:8]}（{len(segments)} 段）",
        "output": {"width": 540, "height": 960, "fps": DEFAULT_FPS,
                   "duration_seconds": round(total_frames / DEFAULT_FPS, 2), "frame_count": total_frames},
        "fidelity_mode": "STRICT_REQUESTED",
        "shots": shots,
        "from_reference": {"analysis_id": analysis["id"], "reference_id": reference["id"]},
    }
    mapping_payload = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "analysis_id": analysis["id"],
        "analysis_revision": analysis["revision"],
        "selected_dimensions": request.selected_dimensions,
        "shots": mapping_shots,
        "total_frames": total_frames,
        "total_duration_error_frames": round(total_duration_error_frames, 2),
        "capability_notes": [
            "单目视频不恢复焦距/相机路径；运动类型为启发式推断，低置信度已在分析中标记",
            "shot_size/action_rhythm 无主体检测模型，未复用（unsupported）",
            "品牌/人物身份/音乐/逐字文案默认不复用",
        ],
        "notes": request.notes,
    }
    with connect() as db:
        begin_immediate(db)
        _, payload_sha256 = plan_contract_payload_hash(payload)
        product_version_id = create_product_version(asset_id, owner_id, project_id, payload_sha256, db)
        db.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, 0, ?)",
            (plan_id, asset_id, payload["intent"], json.dumps(payload, ensure_ascii=False), created),
        )
        contract = upsert_plan_contract(plan_id, product_version_id, payload, db=db)
        mapping_text = _canonical_json_text(mapping_payload)
        mapping_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO reference_mappings(id, plan_id, analysis_id, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mapping_id, plan_id, analysis["id"], mapping_text,
             hashlib.sha256(mapping_text.encode("utf-8")).hexdigest(), created),
        )
    return {
        "id": plan_id,
        "approved": False,
        "created_at": created,
        "contract_id": contract["contract_id"],
        "product_version_id": product_version_id,
        "schema_version": "1.0",
        "product_asset_id": asset_id,
        "intent": payload["intent"],
        "output": payload["output"],
        "shots": shots,
        "reference_mapping_id": mapping_id,
        "reference_mapping": mapping_payload,
    }


@app.get("/api/v1/plans/{plan_id}/reference-mapping")
def get_plan_reference_mapping(plan_id: str) -> dict:
    """来源与目标时间映射（冻结引用）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "计划不存在")
        row = db.execute(
            "SELECT * FROM reference_mappings WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "该计划没有参考映射")
    with connect() as db:
        analysis = db.execute("SELECT reference_id FROM reference_analyses WHERE id = ?", (row["analysis_id"],)).fetchone()
    if analysis:
        _load_reference_owned(analysis["reference_id"], owner_id, project_id)
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "analysis_id": row["analysis_id"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "mapping": json.loads(row["payload"]),
    }


# ---------------------------------------------------------------------------
# V4-01：交互锚点 / 人物 / 动作模板合同与版本化
# ---------------------------------------------------------------------------

V4_ANCHOR_ACTIONS = {"press_button", "single_punch_target", "approach", "celebrate", "two_person_turn"}
V4_MOTION_TEMPLATES = [
    {
        "id": "approach",
        "label": "走近产品",
        "skeleton_version": "pd-proxy-v1",
        "duration_frames": 48,
        "contact_events": [],
        "allowed_speed_range": [0.8, 1.2],
        "requires_anchor": False,
    },
    {
        "id": "press_button",
        "label": "按按钮",
        "skeleton_version": "pd-proxy-v1",
        "duration_frames": 72,
        "contact_events": [{"event": "contact", "frame_offset": 36}, {"event": "press", "frame_offset": 44}],
        "allowed_speed_range": [0.9, 1.1],
        "requires_anchor": True,
    },
    {
        "id": "single_punch_target",
        "label": "击打指定靶点",
        "skeleton_version": "pd-proxy-v1",
        "duration_frames": 96,
        "contact_events": [{"event": "impact", "frame_offset": 60}],
        "allowed_speed_range": [0.9, 1.15],
        "requires_anchor": True,
    },
    {
        "id": "celebrate",
        "label": "庆祝",
        "skeleton_version": "pd-proxy-v1",
        "duration_frames": 72,
        "contact_events": [],
        "allowed_speed_range": [0.85, 1.25],
        "requires_anchor": False,
    },
]
V4_MOTION_TEMPLATES_BY_ID = {item["id"]: item for item in V4_MOTION_TEMPLATES}


class AnchorRequest(BaseModel):
    """交互锚点合同：产品本地规范坐标（bbox 归一化 0..1）+ 单位法线 + 半径 + 允许动作。"""
    name: str = Field(min_length=1, max_length=80)
    position_m: list[float] = Field(min_length=3, max_length=3)
    normal: list[float] = Field(min_length=3, max_length=3)
    radius_m: float
    allowed_actions: list[str] = Field(min_length=1, max_length=8)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_contract(self):
        failures = interaction_geometry.anchor_payload_ok(self.model_dump())
        if failures:
            raise ValueError("；".join(failures))
        for action in self.allowed_actions:
            if action not in V4_ANCHOR_ACTIONS:
                raise ValueError(f"不允许的动作类型：{action}（可用: {sorted(V4_ANCHOR_ACTIONS)}）")
        return self


class AnchorSetApproveRequest(BaseModel):
    revision: int = Field(ge=1)


class CharacterRequest(BaseModel):
    """人物规格：合成 Proxy 或授权素材，真实素材必须带许可/同意记录。"""
    name: str = Field(min_length=1, max_length=80)
    source: Literal["synthesized_proxy", "licensed_asset"]
    height_range_m: list[float] = Field(min_length=2, max_length=2)
    asset_refs: list[str] = Field(default_factory=list, max_length=16)
    license_record: str = Field(default="", max_length=4000)
    consent_record: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_character(self):
        low, high = self.height_range_m
        if not (0.5 <= low <= high <= 2.5):
            raise ValueError("height_range_m 必须在 [0.5, 2.5] 内且递增")
        if self.source == "licensed_asset" and not (self.license_record and self.consent_record):
            raise ValueError("授权素材必须提供 license_record 与 consent_record")
        if self.source == "synthesized_proxy" and (self.license_record or self.consent_record):
            # 合成人物也可以记录来源说明，不禁止；但真实素材记录不得缺失（上一条已覆盖）。
            pass
        return self


def _anchor_public(row) -> dict:
    return {
        "id": row["id"],
        "product_version_id": row["product_version_id"],
        "revision": row["revision"],
        "name": row["name"],
        "status": row["status"],
        "payload_sha256": row["payload_sha256"],
        "approved_at": row["approved_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "anchor": json.loads(row["payload"]),
    }


def _canonical_anchor_set_payload(version_id: str, revision: int, anchor_rows: list[dict]) -> tuple[str, str]:
    payload = {
        "schema_version": "1.0",
        "product_version_id": version_id,
        "revision": revision,
        "anchors": [
            {
                "id": row["id"],
                "name": row["name"],
                "position_m": anchor_payload["position_m"],
                "normal": anchor_payload["normal"],
                "radius_m": anchor_payload["radius_m"],
                "allowed_actions": anchor_payload["allowed_actions"],
            }
            for row in anchor_rows
            for anchor_payload in [json.loads(row["payload"])]
        ],
    }
    text = _canonical_json_text(payload)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


@app.get("/api/v1/motion-templates")
def list_motion_templates() -> list[dict]:
    """V4 必需动作模板：approach / press_button / single_punch_target / celebrate。"""
    return V4_MOTION_TEMPLATES


@app.post("/api/v1/product-versions/{product_version_id}/anchors", status_code=201)
def create_anchor(product_version_id: str, request: AnchorRequest) -> dict:
    """创建交互锚点草稿。锚点绑定产品版本；产品版本升级后不自动沿用（需重新映射并确认）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    payload = request.model_dump()
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    with connect() as db:
        begin_immediate(db)
        latest = db.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM interaction_anchors WHERE product_version_id = ?",
            (product_version_id,),
        ).fetchone()
        revision = int(latest["revision"]) + 1
        anchor_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO interaction_anchors(id, product_version_id, revision, name, status, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)",
            (anchor_id, product_version_id, revision, request.name, payload_text, digest, now, now),
        )
        row = db.execute("SELECT * FROM interaction_anchors WHERE id = ?", (anchor_id,)).fetchone()
    return _anchor_public(row)


@app.get("/api/v1/product-versions/{product_version_id}/anchors")
def list_anchors(
    product_version_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM interaction_anchors WHERE product_version_id = ? ORDER BY revision DESC",
            (product_version_id,),
        ).fetchall()
    return [_anchor_public(row) for row in rows]


@app.patch("/api/v1/anchors/{anchor_id}")
def update_anchor(anchor_id: str, request: AnchorRequest) -> dict:
    """修改锚点 → 生成新修订（旧修订不可改写）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM interaction_anchors WHERE id = ?", (anchor_id,)).fetchone()
    if not row:
        raise HTTPException(404, "锚点不存在")
    load_product_version_for_owner(row["product_version_id"], owner_id, project_id)
    payload = request.model_dump()
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    with connect() as db:
        begin_immediate(db)
        latest = db.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM interaction_anchors WHERE product_version_id = ?",
            (row["product_version_id"],),
        ).fetchone()
        revision = int(latest["revision"]) + 1
        new_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO interaction_anchors(id, product_version_id, revision, name, status, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)",
            (new_id, row["product_version_id"], revision, request.name, payload_text, digest, now, now),
        )
        created = db.execute("SELECT * FROM interaction_anchors WHERE id = ?", (new_id,)).fetchone()
    return _anchor_public(created)


@app.post("/api/v1/product-versions/{product_version_id}/anchor-sets/approve")
def approve_anchor_set(product_version_id: str, request: AnchorSetApproveRequest) -> dict:
    """按修订冻结锚点集（幂等：同一修订重复批准返回同一集合，不新建）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    with connect() as db:
        existing = db.execute(
            "SELECT * FROM anchor_sets WHERE product_version_id = ? AND revision = ?",
            (product_version_id, request.revision),
        ).fetchone()
        if existing:
            return _anchor_set_public(existing)
        anchors = db.execute(
            "SELECT * FROM interaction_anchors WHERE product_version_id = ? AND revision = ?",
            (product_version_id, request.revision),
        ).fetchall()
        if not anchors:
            raise HTTPException(404, f"该产品版本没有修订 {request.revision} 的锚点")
        payload_text, digest = _canonical_anchor_set_payload(
            product_version_id, request.revision, [row_to_dict(row) for row in anchors]
        )
        begin_immediate(db)
        set_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO anchor_sets(id, product_version_id, revision, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (set_id, product_version_id, request.revision, payload_text, digest, now),
        )
        db.execute(
            "UPDATE interaction_anchors SET status = 'APPROVED', approved_at = ? WHERE product_version_id = ? AND revision = ?",
            (now, product_version_id, request.revision),
        )
        row = db.execute("SELECT * FROM anchor_sets WHERE id = ?", (set_id,)).fetchone()
    return _anchor_set_public(row)


def _anchor_set_public(row) -> dict:
    return {
        "id": row["id"],
        "product_version_id": row["product_version_id"],
        "revision": row["revision"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "set": json.loads(row["payload"]),
    }


@app.get("/api/v1/product-versions/{product_version_id}/anchor-sets")
def list_anchor_sets(
    product_version_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    load_product_version_for_owner(product_version_id, owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM anchor_sets WHERE product_version_id = ? ORDER BY revision DESC",
            (product_version_id,),
        ).fetchall()
    return [_anchor_set_public(row) for row in rows]


@app.get("/api/v1/anchor-sets/{anchor_set_id}")
def get_anchor_set(anchor_set_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM anchor_sets WHERE id = ?", (anchor_set_id,)).fetchone()
    if not row:
        raise HTTPException(404, "锚点集不存在")
    load_product_version_for_owner(row["product_version_id"], owner_id, project_id)
    return _anchor_set_public(row)


def _character_public(row) -> dict:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "project_id": row["project_id"],
        "name": row["name"],
        "status": row["status"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "character": json.loads(row["payload"]),
    }


@app.post("/api/v1/characters", status_code=201)
def create_character(request: CharacterRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    # 授权素材引用必须真实存在且属于同一 Owner（与产品审核证据同规则）
    with connect() as db:
        for asset_id in request.asset_refs:
            asset = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if not asset:
                raise HTTPException(404, f"素材引用不存在: {asset_id}")
            if asset["owner_id"] != owner_id:
                raise HTTPException(403, f"素材引用不属于当前 Owner: {asset_id}")
    payload = request.model_dump()
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    character_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO characters(id, owner_id, project_id, name, status, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)",
            (character_id, owner_id, project_id, request.name, payload_text, digest, now, now),
        )
        row = db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    return _character_public(row)


@app.get("/api/v1/characters/{character_id}/capability")
def character_capability(character_id: str) -> dict:
    """人物能力校验报告：来源、可用预演、生成路线配置状态与授权素材绑定情况（如实报告）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    if not row:
        raise HTTPException(404, "人物不存在")
    if row["owner_id"] != owner_id or row["project_id"] != project_id:
        raise HTTPException(403, "越权访问人物")
    payload = json.loads(row["payload"])
    licensed_assets = []
    with connect() as db:
        for asset_id in payload.get("asset_refs", []):
            asset = db.execute("SELECT id, name, kind FROM assets WHERE id = ?", (asset_id,)).fetchone()
            licensed_assets.append({
                "asset_id": asset_id,
                "status": "BOUND" if asset else "MISSING",
                "name": asset["name"] if asset else "",
                "kind": asset["kind"] if asset else "",
            })
    records_complete = True
    if payload.get("source") == "licensed_asset":
        records_complete = bool(payload.get("license_record") and payload.get("consent_record"))
    generation_route = "NOT_CONFIGURED"
    generation_detail = "未配置经批准的真人感人物生成工作流；接入前不得宣称可用"
    return {
        "schema_version": "1.0",
        "character_id": character_id,
        "person_layer_source": payload.get("source"),
        "proxy_previz": {"status": "AVAILABLE", "detail": "确定性人体 Proxy（V4-02/03）可用于尺度/路径/遮挡与接触预演"},
        "generation_route": {"status": generation_route, "detail": generation_detail},
        "licensed_assets": licensed_assets,
        "records_complete": records_complete,
        "usable_for_previz": True,
        "usable_for_final_person_layer": (
            payload.get("source") == "licensed_asset"
            and bool(payload.get("asset_refs"))
            and all(item["status"] == "BOUND" for item in licensed_assets)
            and records_complete
        ),
    }


@app.get("/api/v1/characters")
def list_characters(owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM characters WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC",
            (owner_id, project_id),
        ).fetchall()
    return [_character_public(row) for row in rows]


# ---------------------------------------------------------------------------
# V4-02/03：交互计划合同、校验与数据版预演
# ---------------------------------------------------------------------------

class InteractionRequest(BaseModel):
    character_id: str = Field(min_length=1, max_length=80)
    anchor_set_id: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=80)
    prepare_frame: int = Field(ge=1, le=100000)
    contact_frame: int = Field(ge=1, le=100000)
    end_frame: int = Field(ge=1, le=100000)
    speed_scale: float = Field(default=1.0, ge=0.5, le=1.5)
    occlusion_strategy: Literal["front_depth_priority", "generated_segmentation_estimate"] = "front_depth_priority"
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_frames(self):
        if not (self.prepare_frame < self.contact_frame <= self.end_frame):
            raise ValueError("帧范围需满足 prepare < contact ≤ end")
        if self.action not in V4_MOTION_TEMPLATES_BY_ID:
            raise ValueError(f"未知动作模板：{self.action}（可用: {sorted(V4_MOTION_TEMPLATES_BY_ID)}）")
        template = V4_MOTION_TEMPLATES_BY_ID[self.action]
        allowed_range = template["allowed_speed_range"]
        if not (allowed_range[0] <= self.speed_scale <= allowed_range[1]):
            raise ValueError(f"speed_scale 超出动作模板允许范围 {allowed_range}")
        return self


def _plan_frame_count(plan_row) -> int:
    try:
        payload = json.loads(plan_row["payload"])
        return sum(int(shot.get("duration_frames") or 0) for shot in payload.get("shots", []))
    except (json.JSONDecodeError, KeyError, TypeError):
        return 0


def _interaction_public(row) -> dict:
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "product_version_id": row["product_version_id"],
        "anchor_set_id": row["anchor_set_id"],
        "character_id": row["character_id"],
        "version": row["version"],
        "payload_sha256": row["payload_sha256"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "interaction": json.loads(row["payload"]),
    }


def _load_interaction_bindings(interaction_row) -> dict:
    """读取交互计划的冻结绑定（计划当前合同、锚点集、人物、动作模板），并校验版本仍有效。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        plan = db.execute("SELECT * FROM plans WHERE id = ?", (interaction_row["plan_id"],)).fetchone()
        if not plan:
            raise HTTPException(404, "计划不存在")
        contract = ensure_plan_contract(db, plan, owner_id, project_id)
        if contract["product_version_id"] != interaction_row["product_version_id"]:
            raise HTTPException(409, "计划已更新（产品版本变化），旧交互计划失效，请基于新版本重建")
        anchor_set = db.execute(
            "SELECT * FROM anchor_sets WHERE id = ?", (interaction_row["anchor_set_id"],),
        ).fetchone()
        if not anchor_set or anchor_set["product_version_id"] != interaction_row["product_version_id"]:
            raise HTTPException(409, "锚点集不属于当前计划的产品版本，交互计划失效")
        character = db.execute(
            "SELECT * FROM characters WHERE id = ?", (interaction_row["character_id"],),
        ).fetchone()
    if not character:
        raise HTTPException(404, "人物不存在")
    anchor_payload = json.loads(anchor_set["payload"])
    return {
        "plan": plan,
        "plan_frame_count": _plan_frame_count(plan),
        "anchor_set_payload": anchor_payload,
        "anchors": anchor_payload.get("anchors", []),
        "character_payload": json.loads(character["payload"]),
        "template": V4_MOTION_TEMPLATES_BY_ID[json.loads(interaction_row["payload"])["action"]],
    }


@app.post("/api/v1/plans/{plan_id}/interactions", status_code=201)
def create_interaction(plan_id: str, request: InteractionRequest) -> dict:
    """创建交互计划草稿（版本化）：绑定当前计划合同的产品版本、冻结锚点集、人物与动作模板。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "计划不存在")
        contract = ensure_plan_contract(db, plan, owner_id, project_id)
        product_version_id = contract["product_version_id"]
        frame_count = _plan_frame_count(plan)
        anchor_set = db.execute(
            "SELECT * FROM anchor_sets WHERE id = ? AND product_version_id = ?",
            (request.anchor_set_id, product_version_id),
        ).fetchone()
        character = db.execute(
            "SELECT * FROM characters WHERE id = ? AND owner_id = ?",
            (request.character_id, owner_id),
        ).fetchone()
        latest = db.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM interaction_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        version = int(latest["version"]) + 1
    if not anchor_set:
        raise HTTPException(409, "锚点集不存在或不属于该计划当前的产品版本（必须先批准锚点集）")
    if not character:
        raise HTTPException(404, "人物不存在或不属于当前 Owner")
    template = V4_MOTION_TEMPLATES_BY_ID[request.action]
    anchor_payload = json.loads(anchor_set["payload"])
    anchors = anchor_payload.get("anchors", [])
    if template.get("requires_anchor") and not any(
        request.action in anchor.get("allowed_actions", []) for anchor in anchors
    ):
        raise HTTPException(409, f"动作 {request.action} 需要允许该动作的锚点，但冻结锚点集没有匹配项")
    if frame_count and request.end_frame > frame_count:
        raise HTTPException(422, f"end_frame({request.end_frame}) 超过计划总帧数({frame_count})")
    payload = request.model_dump()
    interaction_id = str(uuid.uuid4())
    payload["interaction_plan_id"] = interaction_id
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO interaction_plans(id, plan_id, product_version_id, anchor_set_id, character_id, version, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (interaction_id, plan_id, product_version_id, request.anchor_set_id, request.character_id,
             version, payload_text, digest, now, now),
        )
        row = db.execute("SELECT * FROM interaction_plans WHERE id = ?", (interaction_id,)).fetchone()
    return _interaction_public(row)


@app.get("/api/v1/plans/{plan_id}/interactions")
def list_interactions(plan_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM interaction_plans WHERE plan_id = ? ORDER BY version DESC", (plan_id,),
        ).fetchall()
    return [_interaction_public(row) for row in rows]


@app.post("/api/v1/interaction-plans/{interaction_id}/validate")
def validate_interaction_plan(interaction_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM interaction_plans WHERE id = ?", (interaction_id,)).fetchone()
    if not row:
        raise HTTPException(404, "交互计划不存在")
    bindings = _load_interaction_bindings(row)
    interaction = json.loads(row["payload"])
    report = interaction_validation.validate_interaction(
        interaction, bindings["anchors"], bindings["character_payload"],
        bindings["template"], bindings["plan_frame_count"],
    )
    report["interaction_plan_id"] = interaction_id
    report["version"] = row["version"]
    return report


@app.post("/api/v1/interaction-plans/{interaction_id}/previz")
def previz_interaction_plan(interaction_id: str) -> dict:
    """数据版预演：确定性事件时间线与逐帧代理手部位置（Blender 人体 Proxy 渲染在渲染侧接入）。"""
    with connect() as db:
        row = db.execute("SELECT * FROM interaction_plans WHERE id = ?", (interaction_id,)).fetchone()
    if not row:
        raise HTTPException(404, "交互计划不存在")
    bindings = _load_interaction_bindings(row)
    interaction = json.loads(row["payload"])
    timeline = interaction_validation.previz_timeline(interaction, bindings["anchors"], bindings["character_payload"])
    timeline["interaction_plan_id"] = interaction_id
    timeline["version"] = row["version"]
    return timeline


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
        if payload.get("scene_generation"):
            raise HTTPException(409, "场景计划按完整提示词冻结，请重新解析创建新计划，不能用基础分镜覆盖")
        payload["intent"] = request.intent
        payload["shots"] = [shot.model_dump() for shot in request.shots]
        payload["crop_anchor"] = request.crop_anchor.value
        payload["product_pose"] = request.product_pose.model_dump()
        payload["scene"] = request.scene.model_dump()
        output_changed = False
        if request.output is not None:
            previous_output = payload.get("output") or {}
            payload["output"] = request.output.model_dump()
            payload["output"]["duration_seconds"] = request.duration_seconds
            payload["output"]["frame_count"] = sum(shot.duration_frames for shot in request.shots)
            output_changed = (previous_output.get("width"), previous_output.get("height")) != (
                request.output.width, request.output.height)
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
            "output_changed": output_changed,
            "approval_invalidated": True,
            "note": ("输出规格已更新并写入冻结合同；原审批已失效，必须重新审批后再启动渲染"
                     if output_changed else "计划已更新（新合同版本）；原审批已失效"),
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
            if plan_snapshot.get("from_reference"):
                # V5 重演计划：时长与镜头数来自参考映射（量化整帧），不套用 V1 三镜头 5–8 秒合同。
                _validate_from_reference_plan_snapshot(db, plan_snapshot, plan["id"])
            elif plan_snapshot.get("production_plan"):
                # V6 生产计划：按 Profile 规格校验多场景时长与镜头数。
                profile_ref = plan_snapshot["production_plan"].get("profile_id") or ""
                profile = _profile_for_request(db, profile_ref, owner_id, project_id) if profile_ref else None
                validate_production_plan_snapshot(plan_snapshot, profile["spec"] if profile else None)
            else:
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
        run_hash = fidelity_run_request_hash(plan_contract_payload_hash(json.loads(plan["payload"]))[1], request)
        run_id, job_id, created_new, fidelity_snapshot = create_run_record(
            plan,
            owner_id,
            project_id,
            request.idempotency_key,
            run_hash,
            plan_contract_id=request.plan_contract_id,
            product_review_id=request.product_review_id,
            product_review_sha256=request.product_review_sha256,
            fidelity_policy_id=request.fidelity_policy_id,
            fidelity_policy_sha256=request.fidelity_policy_sha256,
            require_fidelity_snapshot=request.require_fidelity_snapshot,
            background_workflow=request.background_workflow,
            background_source_mode=request.background_source_mode,
            required_strict_layers=_required_strict_layers_from_plan(plan_snapshot),
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
        "fidelity_snapshot": fidelity_snapshot,
        "reused_idempotent": not created_new,
        "created": created_new,
    }



@app.post("/api/v1/runs/{run_id}/strict-source-manifest")
def register_strict_sources(
    run_id: str,
    request: StrictSourceManifestRequest,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """在 Run 执行前冻结真实 Strict 层来源；只验证文件与身份，不升级发布资格。"""
    resolve_run_owner_scope(run_id, owner_id=owner_id, project_id=project_id)
    with connect() as db:
        begin_immediate(db)
        run = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run 不存在")
        if run["status"] != "QUEUED":
            raise HTTPException(409, "Strict 输入来源只能在 Run 领取/执行前冻结")
        if run["strict_source_manifest_json"]:
            raise HTTPException(409, "Strict 输入来源已冻结")
        if not run["fidelity_snapshot_json"]:
            raise HTTPException(409, "非 Strict Run 不能注册 Strict 输入来源")
        snapshot = json.loads(run["fidelity_snapshot_json"])
        contract = db.execute(
            """
            SELECT pc.*, pv.product_asset_id, pv.owner_id, pv.project_id
            FROM plan_contracts pc
            JOIN product_versions pv ON pv.id = pc.product_version_id
            WHERE pc.id = ? AND pc.plan_id = ?
            """,
            (run["plan_contract_id"], run["plan_id"]),
        ).fetchone()
        if not contract:
            raise HTTPException(404, "冻结合同不存在或不属于该计划")
        if contract["owner_id"] != run["owner_id"] or contract["project_id"] != project_id:
            raise HTTPException(403, "越权访问冻结合同")
        latest = get_default_contract(run["plan_id"], db, run["owner_id"], contract["project_id"])
        if not latest or latest["id"] != contract["id"]:
            raise HTTPException(409, "计划已更新，旧保真审批已失效，请引用最新冻结合同")
        frozen = freeze_fidelity_binding(
            db, contract, snapshot["product_review_id"], snapshot["product_review_sha256"],
            snapshot["fidelity_policy_id"], snapshot["fidelity_policy_sha256"], run["owner_id"],
        )
        if frozen["product_review_sha256"] != snapshot["product_review_sha256"]:
            raise HTTPException(409, "产品审核 hash 与 Run 快照不一致")
        if frozen["fidelity_policy_sha256"] != snapshot["fidelity_policy_sha256"]:
            raise HTTPException(409, "保真策略 hash 与 Run 快照不一致")
        policy_row = db.execute(
            "SELECT payload, payload_sha256 FROM fidelity_policies WHERE id = ?", (snapshot["fidelity_policy_id"],),
        ).fetchone()
        if not policy_row:
            raise HTTPException(404, "冻结保真策略不存在")
        policy_payload = json.loads(policy_row["payload"])
        if policy_payload.get("mode") != "STRICT":
            raise HTTPException(409, "Strict Run 仅接受 STRICT 保真策略")
        background = request.background_workflow.model_dump()
        background["output_ref"] = "strict/background"
        if not _approved_background_workflow(policy_payload, background):
            raise HTTPException(409, "背景工作流未出现在已冻结 STRICT 保真策略的批准列表")
        plan_row = db.execute("SELECT payload FROM plans WHERE id = ?", (run["plan_id"],)).fetchone()
        if not plan_row:
            raise HTTPException(404, "计划不存在")
        plan_snapshot = json.loads(plan_row["payload"])
        output_spec = output_spec_for_plan(plan_snapshot)
        job_id = run["job_id"]
        strict_root = RUNS / job_id / "strict"
        for folder in ("passes", "product", "mask", "background"):
            if not (strict_root / folder).exists():
                raise HTTPException(409, f"缺少 Strict 层目录 strict/{folder}")
        layers = _collect_strict_layer_files(strict_root)
        required_layer_frames, plan_contract_failures = _required_layer_frame_contracts(
            plan_snapshot, output_spec.total_frames
        )
        layer_contract_failures = _strict_layer_contract_failures(
            layers, policy_payload, snapshot.get("required_strict_layers"), required_layer_frames,
            set(range(1, output_spec.total_frames + 1)),
        )
        if plan_contract_failures or layer_contract_failures:
            raise HTTPException(409, "Strict 图层合同与冻结策略不一致: " + "；".join((plan_contract_failures + layer_contract_failures)[:12]))
        source_report, source_error = _run_strict_source_validator(strict_root, output_spec)
        if source_report is None:
            raise HTTPException(409, source_error or "Strict 来源一致性校验失败")
        if not source_report.get("passed"):
            failures = source_report.get("failures", ["Strict 来源一致性校验未通过"])
            raise HTTPException(409, "Strict 来源一致性校验未通过: " + "；".join(failures[:12]))
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "job_id": job_id,
            "plan_id": run["plan_id"],
            "plan_contract_id": contract["id"],
            "product_version_id": snapshot["product_version_id"],
            "asset_id": contract["product_asset_id"],
            "owner_id": run["owner_id"],
            "fidelity_policy_id": snapshot["fidelity_policy_id"],
            "fidelity_complete": False,
            "input_trust": "REGISTERED_LOCAL_SAMPLE",
            "render_evidence": request.render_evidence.model_dump(),
            "background_workflow": background,
            "frames": {"start_frame": 1, "frame_count": output_spec.total_frames},
            "background_frame_offset": 0,
            "layers": layers,
        }
        manifest_text = _canonical_json_text(manifest)
        manifest_sha256 = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
        (strict_root / "source_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        now = utc_now()
        db.execute(
            "UPDATE runs SET strict_source_manifest_json = ?, strict_source_manifest_sha256 = ?, updated_at = ? WHERE id = ?",
            (manifest_text, manifest_sha256, now, run_id),
        )
        append_job_event(db, job_id, "strict.sources.registered", {"manifest_sha256": manifest_sha256})
    return {
        "run_id": run_id,
        "job_id": job_id,
        "manifest_sha256": manifest_sha256,
        "source_report": source_report,
        "fidelity_complete": False,
        "status_url": f"/api/v1/jobs/{job_id}",
    }

@app.get("/api/v1/jobs")
def list_jobs(owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            """
            SELECT j.*, MAX(r.id) AS run_id
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
    job = get_job(job_id)
    eligibility = job_release_eligibility(job)
    job["release_eligible"] = eligibility["eligible"]
    job["release_status"] = eligibility
    return job




@app.get("/api/v1/jobs/{job_id}/release-status")
def job_release_status(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    """下游聚合/发布前必须查询的资格门；资格 false 时不提供可发布成果引用。"""
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    job = get_job(job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        **job_release_eligibility(job),
    }
@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict:
    resolve_job_owner_scope(job_id, owner_id=owner_id, project_id=project_id)
    job = get_job(job_id)
    if job["status"] in TERMINAL_JOB_STATUSES:
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
    if job["status"] not in {"SUCCEEDED", VERIFICATION_PASSED} or not job["output_path"]:
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
    if job["status"] not in {"SUCCEEDED", VERIFICATION_PASSED} or not job["manifest_path"]:
        raise HTTPException(409, "清单尚未准备完成")
    return FileResponse(
        _resolve_job_artifact_path(job_id, job["manifest_path"]),
        media_type="application/json",
        filename=f"metadata-{job_id}.json",
    )


# ---------------------------------------------------------------------------
# V6-01：Platform Profile 与后期模板（版本化导出配置、安全区、规格校验）
# ---------------------------------------------------------------------------


class PlatformProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: Literal[
        "tiktok", "youtube", "instagram", "facebook_page", "facebook_ads", "marketplace", "pinterest",
    ]
    purpose: Literal["organic_post", "ads_material", "marketplace_listing"] = "organic_post"
    # 完整规格快照；缺省时按平台默认模板生成草稿（仍会返回真实校验结果）。
    spec: dict | None = None
    profile_key: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=2000)


class PlatformProfileVersionRequest(BaseModel):
    spec: dict
    notes: str = Field(default="", max_length=2000)


class PlatformProfileValidateRequest(BaseModel):
    account_id: str = Field(default="", max_length=120)
    output: dict | None = None


class PostproductionPresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    preset_key: str = Field(default="", max_length=80)
    spec: dict
    notes: str = Field(default="", max_length=2000)


def _platform_profile_public(db, row) -> dict:
    latest = db.execute(
        "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    spec = platform_profiles.spec_from_payload(json.loads(latest["payload"]))
    problems = platform_profiles.validate_spec(spec)
    summary = platform_profiles.summarize(problems)
    return {
        "id": row["id"],
        "profile_key": row["profile_key"],
        "name": row["name"],
        "platform": row["platform"],
        "version": latest["version"],
        "version_id": latest["id"],
        "payload_sha256": latest["payload_sha256"],
        "aspect_ratio": spec.composition.aspect_ratio,
        "locale": spec.market.locale,
        "resolution": f"{spec.video.width}x{spec.video.height}",
        "fps": spec.video.fps,
        "duration_range_seconds": [spec.video.duration_min_seconds, spec.video.duration_max_seconds],
        "rules_status": platform_profiles.rules_status(spec),
        "valid": summary["valid"],
        "blocking_count": summary["blocking_count"],
        "warning_count": summary["warning_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _create_platform_profile_version(db, profile_id: str, spec_payload: dict, notes: str = "") -> dict:
    """写入一个不可变 Profile 版本；版本号由服务端强制递增，不接受客户端指定。"""
    latest = db.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM platform_profile_versions WHERE profile_id = ?",
        (profile_id,),
    ).fetchone()
    version = int(latest["version"]) + 1
    payload = dict(spec_payload)
    identity = dict(payload.get("identity") or {})
    identity["profile_id"] = profile_id
    identity["version"] = version
    payload["identity"] = identity
    if notes:
        payload["notes"] = notes
    spec = platform_profiles.PlatformProfileSpec.model_validate(payload)
    payload_text = platform_profiles.profile_payload_json(spec)
    version_id = str(uuid.uuid4())
    now = utc_now()
    db.execute(
        "INSERT INTO platform_profile_versions(id, profile_id, version, payload, payload_sha256, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (version_id, profile_id, version, payload_text,
         hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
    )
    db.execute(
        "UPDATE platform_profiles SET name = ?, platform = ?, updated_at = ? WHERE id = ?",
        (spec.identity.name, spec.identity.platform, now, profile_id),
    )
    return {
        "id": version_id,
        "profile_id": profile_id,
        "version": version,
        "payload_sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        "spec": spec.model_dump(),
        "validation": platform_profiles.summarize(platform_profiles.validate_spec(spec)),
        "rules_status": platform_profiles.rules_status(spec),
        "created_at": now,
    }


def _default_profile_spec_payload(
    name: str, platform: str, purpose: str, notes: str
) -> dict:
    """按平台选一个种子模板作为草稿骨架（显式照抄，不做隐式猜测）。"""
    for seed in platform_profiles.SEED_PROFILES:
        if seed["identity"]["platform"] == platform:
            payload = json.loads(json.dumps(seed))
            payload["identity"]["name"] = name
            payload["identity"]["purpose"] = purpose
            payload["identity"]["profile_id"] = "draft"
            if notes:
                payload["notes"] = notes
            return payload
    raise HTTPException(422, f"平台 {platform} 没有可用的草稿模板，请直接提交完整 spec")


@app.get("/api/v1/platform-profiles")
def list_platform_profiles(
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    """导出 Profile 列表（含最新版本的真实校验摘要）。选择 Profile 不代表账号存在。"""
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM platform_profiles WHERE owner_id = ? AND project_id = ? ORDER BY created_at ASC",
            (owner_id, project_id),
        ).fetchall()
        return [_platform_profile_public(db, row) for row in rows]


@app.post("/api/v1/platform-profiles", status_code=201)
def create_platform_profile(request: PlatformProfileCreateRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    profile_key = request.profile_key or f"draft-{uuid.uuid4().hex[:8]}"
    payload = request.spec or _default_profile_spec_payload(
        request.name, request.platform, request.purpose, request.notes
    )
    payload = dict(payload)
    payload["identity"] = {
        **(payload.get("identity") or {}),
        "name": request.name,
        "platform": request.platform,
        "purpose": request.purpose,
        "version": 1,
    }
    try:
        platform_profiles.PlatformProfileSpec.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, f"Profile 规格无效: {exc.errors()[:4]}") from exc
    with connect() as db:
        begin_immediate(db)
        exists = db.execute(
            "SELECT 1 FROM platform_profiles WHERE owner_id = ? AND project_id = ? AND profile_key = ?",
            (owner_id, project_id, profile_key),
        ).fetchone()
        if exists:
            raise HTTPException(409, "同名 profile_key 已存在")
        now = utc_now()
        profile_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO platform_profiles(id, owner_id, project_id, profile_key, name, platform, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, owner_id, project_id, profile_key, request.name, request.platform, now, now),
        )
        created = _create_platform_profile_version(db, profile_id, payload, request.notes)
        row = db.execute("SELECT * FROM platform_profiles WHERE id = ?", (profile_id,)).fetchone()
        public = _platform_profile_public(db, row)
    return {"profile": public, "version": created}


@app.get("/api/v1/platform-profiles/{profile_id}")
def get_platform_profile(profile_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
            (profile_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Profile 不存在")
        latest = db.execute(
            "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    spec = platform_profiles.spec_from_payload(json.loads(latest["payload"]))
    problems = platform_profiles.validate_spec(spec)
    return {
        "profile": {
            "id": row["id"], "profile_key": row["profile_key"], "name": row["name"],
            "platform": row["platform"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        },
        "latest_version": {
            "id": latest["id"], "version": latest["version"], "payload_sha256": latest["payload_sha256"],
            "created_at": latest["created_at"],
        },
        "spec": spec.model_dump(),
        "validation": platform_profiles.summarize(problems),
        "rules_status": platform_profiles.rules_status(spec),
    }


@app.get("/api/v1/platform-profiles/{profile_id}/versions")
def list_platform_profile_versions(profile_id: str) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
            (profile_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Profile 不存在")
        versions = db.execute(
            "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version ASC",
            (profile_id,),
        ).fetchall()
    return [
        {
            "id": item["id"], "version": item["version"], "payload_sha256": item["payload_sha256"],
            "created_at": item["created_at"],
            "spec": json.loads(item["payload"]),
        }
        for item in versions
    ]


@app.post("/api/v1/platform-profiles/{profile_id}/versions", status_code=201)
def create_platform_profile_version(profile_id: str, request: PlatformProfileVersionRequest) -> dict:
    """新版本是不可变快照；旧版本保留（修改永远生成新版本）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
            (profile_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Profile 不存在")
        try:
            created = _create_platform_profile_version(db, profile_id, request.spec, request.notes)
        except ValidationError as exc:
            raise HTTPException(422, f"Profile 规格无效: {exc.errors()[:4]}") from exc
    return created


@app.post("/api/v1/platform-profiles/{profile_id}/validate")
def validate_platform_profile(profile_id: str, request: PlatformProfileValidateRequest) -> dict:
    """规格校验 + （可选）实际成片兼容性 + （可选）账号能力。

    账号能力需要真实平台授权；未配置时如实返回 NOT_CONFIGURED，不伪造能力结论。
    """
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
            (profile_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Profile 不存在")
        latest = db.execute(
            "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    spec = platform_profiles.spec_from_payload(json.loads(latest["payload"]))
    problems = list(platform_profiles.validate_spec(spec))
    output_report = None
    if request.output:
        output_problems = platform_profiles.output_compatibility(spec, request.output)
        problems.extend(output_problems)
        output_report = platform_profiles.summarize(output_problems)
    account_report = {
        "account_id": request.account_id,
        "status": "NOT_CONFIGURED" if request.account_id else "NOT_REQUESTED",
        "reason": "未配置该平台账号授权：账号能力需 V6-C 连接器现场验证，不能用规格校验代替",
        "capabilities": None,
    }
    summary = platform_profiles.summarize(problems)
    return {
        "profile_id": profile_id,
        "version": latest["version"],
        "payload_sha256": latest["payload_sha256"],
        "valid": summary["valid"],
        "blocking": summary["blocking"],
        "warnings": summary["warnings"],
        "output_compatibility": output_report,
        "rules_status": platform_profiles.rules_status(spec),
        "account": account_report,
        "checked_at": utc_now(),
    }


# --- 后期模板（字幕/配音/BGM/混音） ---------------------------------------


def _preset_public(db, row) -> dict:
    latest = db.execute(
        "SELECT * FROM postproduction_preset_versions WHERE preset_id = ? ORDER BY version DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    spec = platform_profiles.PostproductionPresetSpec.model_validate(json.loads(latest["payload"]))
    problems = platform_profiles.validate_preset_spec(spec)
    summary = platform_profiles.summarize(problems)
    return {
        "id": row["id"],
        "preset_key": row["preset_key"],
        "name": row["name"],
        "version": latest["version"],
        "version_id": latest["id"],
        "payload_sha256": latest["payload_sha256"],
        "locale": spec.locale,
        "subtitles_enabled": spec.subtitles.enabled,
        "voice_enabled": spec.voice.enabled,
        "music_enabled": spec.music.enabled,
        "adaptation_policy": spec.adaptation_policy,
        "valid": summary["valid"],
        "blocking_count": summary["blocking_count"],
        "warning_count": summary["warning_count"],
        "created_at": row["created_at"],
    }


@app.get("/api/v1/postproduction-presets")
def list_postproduction_presets(
    owner_id: str = DEFAULT_OWNER_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM postproduction_presets WHERE owner_id = ? AND project_id = ? ORDER BY created_at ASC",
            (owner_id, project_id),
        ).fetchall()
        return [_preset_public(db, row) for row in rows]


@app.get("/api/v1/postproduction-presets/{preset_id}")
def get_postproduction_preset(preset_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM postproduction_presets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (preset_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "后期模板不存在")
        latest = db.execute(
            "SELECT * FROM postproduction_preset_versions WHERE preset_id = ? ORDER BY version DESC LIMIT 1",
            (preset_id,),
        ).fetchone()
    spec = platform_profiles.PostproductionPresetSpec.model_validate(json.loads(latest["payload"]))
    return {
        "preset": {
            "id": row["id"], "preset_key": row["preset_key"], "name": row["name"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        },
        "latest_version": {"id": latest["id"], "version": latest["version"],
                           "payload_sha256": latest["payload_sha256"], "created_at": latest["created_at"]},
        "spec": spec.model_dump(),
        "validation": platform_profiles.summarize(platform_profiles.validate_preset_spec(spec)),
    }


@app.post("/api/v1/postproduction-presets", status_code=201)
def create_postproduction_preset(request: PostproductionPresetCreateRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    preset_key = request.preset_key or f"preset-{uuid.uuid4().hex[:8]}"
    try:
        spec = platform_profiles.PostproductionPresetSpec.model_validate(request.spec)
    except ValidationError as exc:
        raise HTTPException(422, f"后期模板规格无效: {exc.errors()[:4]}") from exc
    with connect() as db:
        begin_immediate(db)
        exists = db.execute(
            "SELECT 1 FROM postproduction_presets WHERE owner_id = ? AND project_id = ? AND preset_key = ?",
            (owner_id, project_id, preset_key),
        ).fetchone()
        if exists:
            raise HTTPException(409, "同名 preset_key 已存在")
        now = utc_now()
        preset_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO postproduction_presets(id, owner_id, project_id, preset_key, name, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'postproduction', ?, ?)",
            (preset_id, owner_id, project_id, preset_key, request.name, now, now),
        )
        payload_text = platform_profiles.preset_payload_json(spec)
        version_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO postproduction_preset_versions(id, preset_id, version, payload, payload_sha256, created_at) "
            "VALUES (?, ?, 1, ?, ?, ?)",
            (version_id, preset_id, payload_text, hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
        )
        row = db.execute("SELECT * FROM postproduction_presets WHERE id = ?", (preset_id,)).fetchone()
        public = _preset_public(db, row)
    return {"preset": public, "version": 1, "version_id": version_id,
            "spec": spec.model_dump(),
            "validation": platform_profiles.summarize(platform_profiles.validate_preset_spec(spec))}


@app.post("/api/v1/postproduction-presets/{preset_id}/versions", status_code=201)
def create_postproduction_preset_version(preset_id: str, request: PlatformProfileVersionRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM postproduction_presets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (preset_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "后期模板不存在")
        try:
            spec = platform_profiles.PostproductionPresetSpec.model_validate(request.spec)
        except ValidationError as exc:
            raise HTTPException(422, f"后期模板规格无效: {exc.errors()[:4]}") from exc
        latest = db.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM postproduction_preset_versions WHERE preset_id = ?",
            (preset_id,),
        ).fetchone()
        version = int(latest["version"]) + 1
        payload_text = platform_profiles.preset_payload_json(spec)
        version_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO postproduction_preset_versions(id, preset_id, version, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (version_id, preset_id, version, payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
        )
        db.execute("UPDATE postproduction_presets SET updated_at = ? WHERE id = ?", (now, preset_id))
    return {
        "id": version_id, "preset_id": preset_id, "version": version,
        "payload_sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        "spec": spec.model_dump(),
        "validation": platform_profiles.summarize(platform_profiles.validate_preset_spec(spec)),
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# V6-02：本地化文案、字幕生成/对齐与离线 TTS 试听
# ---------------------------------------------------------------------------

# 配音上传上限：授权配音通常为几十 MB；超过视为异常输入。
MAX_VOICEOVER_BYTES = 200 * 1024 * 1024


class LocalizationCreateRequest(BaseModel):
    locale: Literal["es-MX", "en-US"] = "es-MX"
    profile_id: str = Field(default="", max_length=80)
    preset_id: str = Field(default="", max_length=80)
    product_name: str = Field(default="", max_length=120)
    facts: dict = Field(default_factory=dict)
    style_index: int = Field(default=0, ge=0, le=20)
    notes: str = Field(default="", max_length=2000)


class LocalizationPatchRequest(BaseModel):
    headline: str | None = Field(default=None, max_length=400)
    body: str | None = Field(default=None, max_length=4000)
    cta: str | None = Field(default=None, max_length=400)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    notes: str = Field(default="", max_length=2000)


class SubtitleGenerateRequest(BaseModel):
    formats: list[Literal["srt", "vtt"]] = Field(default_factory=lambda: ["srt", "vtt"], min_length=1, max_length=2)
    source: Literal["auto", "voice", "timeline"] = "auto"
    voiceover_id: str = Field(default="", max_length=80)
    video_duration_s: float | None = Field(default=None, gt=0, le=3600)
    max_chars_per_line: int = Field(default=localization.DEFAULT_MAX_CHARS_PER_LINE, ge=16, le=80)
    max_lines: int = Field(default=2, ge=1, le=6)
    # 广告/多场景必须能精确对位文案：显式给出每条字幕的起止时间（秒）与文本
    cues: list[dict] = Field(default_factory=list, max_length=64)
    notes: str = Field(default="", max_length=2000)


class AudioPreviewRequest(BaseModel):
    locale: Literal["es-MX", "en-US"] = "es-MX"
    text: str = Field(default="", max_length=2000)
    localization_id: str = Field(default="", max_length=80)
    voice_ref: str = Field(default="", max_length=80)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pronunciation_dictionary: list[dict] = Field(default_factory=list, max_length=200)
    max_seconds: float = Field(default=60.0, gt=0, le=600)


def _localization_public(revision_row) -> dict:
    payload = json.loads(revision_row["payload"])
    return {
        "revision_id": revision_row["id"],
        "revision": revision_row["revision"],
        "status": revision_row["status"],
        "edited_from_revision": revision_row["edited_from_revision"],
        "payload_sha256": revision_row["payload_sha256"],
        "created_at": revision_row["created_at"],
        **payload,
    }


def _latest_localization_revision(db, localization_id: str):
    return db.execute(
        "SELECT * FROM localization_revisions WHERE localization_id = ? ORDER BY revision DESC LIMIT 1",
        (localization_id,),
    ).fetchone()


def _product_facts_for_run(db, run_id: str) -> dict:
    """从批准数据读取产品事实：仅使用已核实维度，没有就返回空（不猜）。"""
    row = db.execute(
        "SELECT p.product_asset_id FROM runs r JOIN plans p ON p.id = r.plan_id WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        return {}
    asset = db.execute("SELECT name FROM assets WHERE id = ?", (row["product_asset_id"],)).fetchone()
    facts: dict = {"product_asset_id": row["product_asset_id"]}
    if asset:
        facts["name"] = asset["name"]
    version = db.execute(
        "SELECT verified_dimensions FROM product_versions WHERE product_asset_id = ? AND verified_dimensions IS NOT NULL "
        "ORDER BY version DESC LIMIT 1",
        (row["product_asset_id"],),
    ).fetchone()
    if version and version["verified_dimensions"]:
        try:
            facts["verified_dimensions"] = json.loads(version["verified_dimensions"])
        except json.JSONDecodeError:
            pass
    return facts


def _resolve_run_scope(db, run_id: str) -> dict:
    """Run → 计划/产品范围（owner/project 通过冻结合同关联，runs 表本身不存 owner）。"""
    row = db.execute(
        "SELECT r.*, p.product_asset_id, p.payload AS plan_payload, rv.owner_id AS owner_id, rv.project_id AS project_id "
        "FROM runs r JOIN plans p ON p.id = r.plan_id "
        "LEFT JOIN plan_contracts pc ON pc.id = r.plan_contract_id "
        "LEFT JOIN product_versions rv ON rv.id = pc.product_version_id "
        "WHERE r.id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Run 不存在")
    keys = row.keys()
    owner_id = row["owner_id"] if "owner_id" in keys and row["owner_id"] else DEFAULT_OWNER_ID
    project_id = row["project_id"] if "project_id" in keys and row["project_id"] else DEFAULT_PROJECT_ID
    return {
        "id": row["id"], "job_id": row["job_id"], "plan_id": row["plan_id"],
        "owner_id": owner_id, "project_id": project_id,
        "product_asset_id": row["product_asset_id"], "plan_payload": row["plan_payload"],
    }


def _create_localization_revision(
    db, localization_id: str, payload: dict, *, edited_from: int | None = None, status: str = "DRAFT"
) -> dict:
    latest = db.execute(
        "SELECT COALESCE(MAX(revision), 0) AS revision FROM localization_revisions WHERE localization_id = ?",
        (localization_id,),
    ).fetchone()
    revision = int(latest["revision"]) + 1
    payload = dict(payload)
    payload["revision"] = revision
    problems = localization.find_prohibited_claims(
        f"{payload.get('headline', '')}\n{payload.get('body', '')}\n{payload.get('cta', '')}"
    )
    payload["claims_scan"] = problems
    # 修改文案/标签会改变下游包 hash：显式标记旧审批失效（V6-05 打包时对照）。
    payload["downstream_invalidated"] = edited_from is not None
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision_id = str(uuid.uuid4())
    now = utc_now()
    db.execute(
        "INSERT INTO localization_revisions(id, localization_id, revision, status, payload, payload_sha256, "
        "edited_from_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (revision_id, localization_id, revision, status, payload_text,
         hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), edited_from, now),
    )
    db.execute("UPDATE localizations SET updated_at = ? WHERE id = ?", (now, localization_id))
    return {
        "id": revision_id, "localization_id": localization_id, "revision": revision,
        "payload_sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        "edited_from_revision": edited_from, "created_at": now, **payload,
    }


@app.get("/api/v1/audio/engines")
def list_audio_engines() -> dict:
    """TTS 能力如实上报：无可引擎时 NOT_CONFIGURED，而不是假装能配音。"""
    capability = localization.tts_capability()
    return {
        **capability,
        "fallbacks": [
            "上传授权配音（POST /runs/{id}/voiceovers）",
            "显式关闭配音并在 Manifest 标注 disabled",
        ],
    }


@app.post("/api/v1/runs/{run_id}/localizations", status_code=201)
def create_localization(run_id: str, request: LocalizationCreateRequest) -> dict:
    """按 Run 建立本地化草稿：文案（事实与生成语分离）+ 话题标签 + 字幕计划。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        run = _resolve_run_scope(db, run_id)
        if run["owner_id"] != owner_id or run["project_id"] != project_id:
            raise HTTPException(403, "越权访问 Run")
        profile_spec = None
        copy_spec = {"max_length": 2200, "hashtag_max_count": 8}
        if request.profile_id:
            row = db.execute(
                "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.profile_id, owner_id, project_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Profile 不存在")
            version = db.execute(
                "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
                (request.profile_id,),
            ).fetchone()
            profile_spec = platform_profiles.spec_from_payload(json.loads(version["payload"]))
            copy_spec = {
                "max_length": profile_spec.copy_spec.max_length,
                "hashtag_max_count": profile_spec.copy_spec.hashtag_max_count,
                "length_algorithm": profile_spec.copy_spec.length_algorithm,
            }
        facts = dict(_product_facts_for_run(db, run_id))
        facts.update(request.facts or {})
        product_name = request.product_name or str(facts.get("name") or "Producto")
        copy_payload = localization.build_copy(
            locale=request.locale, product_name=product_name, facts=facts,
            style_index=request.style_index, hashtag_max_count=int(copy_spec.get("hashtag_max_count") or 5),
        )
        hashtag_problems = localization.validate_hashtags(
            copy_payload["hashtags"], max_count=int(copy_spec.get("hashtag_max_count") or 5)
        )
        over_length = len(copy_payload["body"]) > int(copy_spec.get("max_length") or 2200)
        localization_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO localizations(id, run_id, owner_id, project_id, locale, profile_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (localization_id, run_id, owner_id, project_id, request.locale,
             request.profile_id or None, now, now),
        )
        payload = {
            "locale": request.locale,
            "profile_id": request.profile_id,
            "preset_id": request.preset_id,
            "headline": copy_payload["headline"],
            "body": copy_payload["body"],
            "cta": copy_payload["cta"],
            "hashtags": copy_payload["hashtags"],
            "facts": copy_payload["facts"],
            "generated": copy_payload["generated"],
            "generator": copy_payload["generator"],
            "copy_limits": copy_spec,
            "hashtag_problems": hashtag_problems,
            "copy_over_length": over_length,
            "notes": request.notes,
        }
        created = _create_localization_revision(db, localization_id, payload)
    return {
        "localization_id": localization_id,
        "run_id": run_id,
        "locale": request.locale,
        "revision": created,
        "issues": {
            "claims": created.get("claims_scan", []),
            "hashtags": hashtag_problems,
            "copy_over_length": over_length,
        },
        "generated_by": copy_payload["generator"],
    }


@app.get("/api/v1/runs/{run_id}/localizations")
def list_localizations(run_id: str) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM localizations WHERE run_id = ? AND owner_id = ? AND project_id = ? ORDER BY created_at ASC",
            (run_id, owner_id, project_id),
        ).fetchall()
        return [
            {"id": row["id"], "locale": row["locale"], "profile_id": row["profile_id"],
             "created_at": row["created_at"], "updated_at": row["updated_at"],
             "latest": _localization_public(_latest_localization_revision(db, row["id"]))}
            for row in rows
        ]


@app.get("/api/v1/localizations/{localization_id}")
def get_localization(localization_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM localizations WHERE id = ? AND owner_id = ? AND project_id = ?",
            (localization_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "本地化不存在")
        revisions = db.execute(
            "SELECT * FROM localization_revisions WHERE localization_id = ? ORDER BY revision ASC",
            (localization_id,),
        ).fetchall()
        previews = db.execute(
            "SELECT id, engine, voice, locale, path, created_at FROM audio_previews WHERE localization_id = ? "
            "ORDER BY created_at ASC",
            (localization_id,),
        ).fetchall()
        subtitles = db.execute(
            "SELECT * FROM subtitle_tracks WHERE localization_id = ? ORDER BY created_at ASC",
            (localization_id,),
        ).fetchall()
    return {
        "localization": {
            "id": row["id"], "run_id": row["run_id"], "locale": row["locale"],
            "profile_id": row["profile_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        },
        "revisions": [_localization_public(item) for item in revisions],
        "latest": _localization_public(revisions[-1]) if revisions else None,
        "audio_previews": [dict(item) for item in previews],
        "subtitle_tracks": [dict(item) for item in subtitles],
    }


@app.patch("/api/v1/localizations/{localization_id}")
def edit_localization(localization_id: str, request: LocalizationPatchRequest) -> dict:
    """人工修改文案/标签 → 生成新 revision（不覆盖已审核文案），并标记下游包审批失效。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM localizations WHERE id = ? AND owner_id = ? AND project_id = ?",
            (localization_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "本地化不存在")
        latest = _latest_localization_revision(db, localization_id)
        base = json.loads(latest["payload"])
        profile_id = row["profile_id"]
        max_count = int((base.get("copy_limits") or {}).get("hashtag_max_count") or 8)
        payload = {
            **base,
            "headline": request.headline if request.headline is not None else base.get("headline", ""),
            "body": request.body if request.body is not None else base.get("body", ""),
            "cta": request.cta if request.cta is not None else base.get("cta", ""),
            "hashtags": list(request.hashtags) if request.hashtags is not None else base.get("hashtags", []),
            "notes": request.notes or base.get("notes", ""),
            "profile_id": profile_id or "",
        }
        payload["hashtag_problems"] = localization.validate_hashtags(payload["hashtags"], max_count=max_count)
        payload["copy_over_length"] = len(payload["body"]) > int((base.get("copy_limits") or {}).get("max_length") or 2200)
        updated = _create_localization_revision(db, localization_id, payload, edited_from=latest["revision"])
    return {
        "localization_id": localization_id,
        "revision": updated,
        "issues": {
            "claims": updated.get("claims_scan", []),
            "hashtags": updated.get("hashtag_problems", []),
            "copy_over_length": updated.get("copy_over_length", False),
        },
        "downstream_invalidated": True,
    }


@app.post("/api/v1/localizations/{localization_id}/subtitles", status_code=201)
def generate_subtitles(localization_id: str, request: SubtitleGenerateRequest) -> dict:
    """生成 SRT/VTT：优先按实际配音音频对齐；无配音时按时间线比例并如实标注。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM localizations WHERE id = ? AND owner_id = ? AND project_id = ?",
            (localization_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "本地化不存在")
        latest = _latest_localization_revision(db, localization_id)
        payload = json.loads(latest["payload"])
        run = _resolve_run_scope(db, row["run_id"])
        voiceover = None
        if request.voiceover_id:
            voiceover = db.execute(
                "SELECT * FROM voiceovers WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.voiceover_id, owner_id, project_id),
            ).fetchone()
            if not voiceover:
                raise HTTPException(404, "配音不存在")
        elif request.source in ("auto", "voice"):
            voiceover = db.execute(
                "SELECT * FROM voiceovers WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (row["run_id"],),
            ).fetchone()
    plan_payload = json.loads(run["plan_payload"] or "{}")
    output = plan_payload.get("output") or {}
    fps = int(output.get("fps") or DEFAULT_FPS)
    frame_count = int(output.get("frame_count") or (fps * int(output.get("duration_seconds") or 6)))
    video_duration = float(request.video_duration_s or (frame_count / fps if fps else 6.0))
    text = " ".join(filter(None, [payload.get("headline"), payload.get("body")]))
    explicit_cues = [cue for cue in (request.cues or []) if str(cue.get("text") or "").strip()]
    chunks = localization.split_copy_into_chunks(text)
    alignment = {"status": "TIMELINE", "reason": "未提供配音：按时间线比例分配（不是语音精确同步）"}
    cues: list[dict] = []
    audio_report = None
    if explicit_cues:
        # 广告场景文案：按显式时间轴排布，仍走同一套合法性校验（不与配音做语音对齐）
        cues = []
        for index, cue in enumerate(explicit_cues, start=1):
            start = float(cue.get("start_s") or 0.0)
            end = float(cue.get("end_s") or 0.0)
            value = str(cue["text"]).strip()
            cues.append({
                "index": index, "start_s": round(start, 3), "end_s": round(end, 3), "text": value,
                "lines": localization.wrap_text(value, max_chars_per_line=request.max_chars_per_line,
                                                max_lines=request.max_lines),
            })
        alignment = {"status": "EXPLICIT_SCENE_TIMELINE",
                     "reason": "按显式场景时间轴排布（广告文案），不与配音做语音对齐",
                     "cue_count": len(cues)}
        audio_report = None
        if voiceover and "path" in voiceover.keys() and Path(voiceover["path"]).exists():
            audio_report = localization.detect_speech_segments(Path(voiceover["path"]))
    elif request.source in ("auto", "voice") and voiceover:
        audio_path = Path(voiceover["path"]) if "path" in voiceover.keys() else None
        if audio_path and audio_path.exists():
            audio_report = localization.detect_speech_segments(audio_path)
            cues, alignment = localization.cues_from_speech(
                chunks, audio_report, duration_seconds=video_duration,
                max_chars_per_line=request.max_chars_per_line, max_lines=request.max_lines,
            )
            alignment["voiceover_id"] = voiceover["id"]
            if alignment["status"] != "ALIGNED":
                alignment["fallback_to"] = "timeline_proportional"
        else:
            alignment = {"status": "TIMELINE", "reason": "配音记录缺少音频文件，回退时间线比例",
                         "voiceover_id": voiceover["id"]}
    if not cues:
        cues = localization.cues_from_timeline(
            chunks, duration_seconds=video_duration,
            max_chars_per_line=request.max_chars_per_line, max_lines=request.max_lines,
        )
        if alignment.get("status") not in ("TIMELINE",):
            alignment = {**alignment, "status": "TIMELINE", "fallback_used": True}
    audio_duration = float((audio_report or {}).get("duration_s") or 0.0)
    over_length = audio_duration > video_duration + (1.0 / max(1, fps))
    problems = localization.validate_cues(
        cues, video_duration_s=video_duration, max_lines=request.max_lines,
        max_chars_per_line=request.max_chars_per_line,
    )
    if over_length:
        # 配音过长：同一根因只保留一条显式问题（越界由它解释），并给出主规划 12.4 的适配策略。
        problems = [item for item in problems if item["code"] != "cue_beyond_video"]
        alignment = {**alignment, "status": "AUDIO_LONGER_THAN_VIDEO", "over_length_s": round(audio_duration - video_duration, 3)}
        problems.append({
            "code": "audio_longer_than_video",
            "cue": None,
            "message": (
                f"配音时长 {audio_duration:.2f}s 超过视频 {video_duration:.2f}s："
                "请选择允许的适配策略（在 Profile 范围内延长非接触镜头 / 在允许速率内调速 / 改文案重新生成），"
                "不得截断视频或旁白，也不得对人物接触镜头变速"
            ),
            "audio_duration_s": round(audio_duration, 3),
            "video_duration_s": round(video_duration, 3),
            "overflow_s": round(audio_duration - video_duration, 3),
        })
    directory = VAR / "localizations" / localization_id / f"rev{latest['revision']}"
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for fmt in request.formats:
        content = localization.render_srt(cues) if fmt == "srt" else localization.render_vtt(cues)
        path = directory / f"{row['locale']}.{fmt}"
        path.write_text(content, encoding="utf-8")
        files.append({
            "format": fmt, "path": str(path), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "mime": "application/x-subrip" if fmt == "srt" else "text/vtt",
        })
    track_payload = {
        "localization_id": localization_id,
        "revision": latest["revision"],
        "locale": row["locale"],
        "alignment": alignment,
        "audio": ({"duration_s": audio_report.get("duration_s"),
                   "speech_segments": len(audio_report.get("segments") or []),
                   "method": audio_report.get("method")} if audio_report else None),
        "video_duration_s": video_duration,
        "cue_count": len(cues),
        "cues": cues,
        "problems": problems,
        "files": files,
        "notes": request.notes,
    }
    track_text = json.dumps(track_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    track_id = str(uuid.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO subtitle_tracks(id, localization_id, revision, locale, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (track_id, localization_id, latest["revision"], row["locale"], track_text,
             hashlib.sha256(track_text.encode("utf-8")).hexdigest(), utc_now()),
        )
    return {
        "subtitle_track_id": track_id,
        "localization_id": localization_id,
        "revision": latest["revision"],
        "locale": row["locale"],
        "alignment": alignment,
        "cue_count": len(cues),
        "problems": problems,
        "files": files,
        "cues": cues,
        "audio": track_payload["audio"],
    }


@app.post("/api/v1/audio/previews", status_code=201)
def create_audio_preview(request: AudioPreviewRequest) -> dict:
    """试听配音：真实离线合成（引擎不可用时 NOT_CONFIGURED），可带发音词典与语速。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    text = request.text
    localization_id = request.localization_id or None
    with connect() as db:
        if localization_id:
            row = db.execute(
                "SELECT * FROM localizations WHERE id = ? AND owner_id = ? AND project_id = ?",
                (localization_id, owner_id, project_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "本地化不存在")
            if not text:
                payload = json.loads(_latest_localization_revision(db, localization_id)["payload"])
                text = " ".join(filter(None, [payload.get("headline"), payload.get("body")]))
    if not text.strip():
        raise HTTPException(422, "试听文本为空")
    preview_id = str(uuid.uuid4())
    out_path = VAR / "audio-previews" / f"{preview_id}.wav"
    try:
        result = localization.synthesize(
            text, locale=request.locale, out_path=out_path, rate=request.rate,
            pronunciation_dictionary=request.pronunciation_dictionary, voice_ref=request.voice_ref,
        )
    except RuntimeError as exc:
        try:
            detail = json.loads(str(exc))
        except json.JSONDecodeError:
            detail = {"code": "TTS_FAILED", "detail": str(exc)}
        raise HTTPException(503 if detail.get("code") == "NOT_CONFIGURED" else 422, detail) from exc
    if result["duration_s"] > request.max_seconds + 1e-6:
        out_path.unlink(missing_ok=True)
        raise HTTPException(422, {
            "code": "PREVIEW_TOO_LONG",
            "detail": f"合成时长 {result['duration_s']}s 超过上限 {request.max_seconds}s（不截断音频）",
            "duration_s": result["duration_s"],
        })
    payload = {
        "engine": result["engine"], "engine_kind": result["engine_kind"], "voice": result["voice"],
        "locale": request.locale, "rate": request.rate, "speed": result["speed"],
        "characters": result["characters"], "duration_s": result["duration_s"],
        "pronunciation_applied": result["pronunciation_applied"],
        "text": text, "localization_id": localization_id,
        "note": "离线预览引擎产物；正式发行音色需接入已配置的 TTS Provider",
    }
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO audio_previews(id, localization_id, owner_id, project_id, locale, engine, voice, text, path, "
            "payload, payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (preview_id, localization_id, owner_id, project_id, request.locale, result["engine"],
             result["voice"], text, str(out_path), payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
        )
    return {"id": preview_id, "path": str(out_path), "sha256": _file_sha256(out_path), **payload,
            "download_url": f"/api/v1/audio/previews/{preview_id}/content",
            "usage": record_internal_usage(
                provider=result["engine"], unit="tts_character", quantity=float(result["characters"]),
                operation_id=preview_id, capability="tts_preview",
                note=f"离线预览合成 {result['engine']}（{result['engine_kind']}），仅内部估算")}

@app.get("/api/v1/audio/previews/{preview_id}/content")
def get_audio_preview_content(preview_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM audio_previews WHERE id = ? AND owner_id = ? AND project_id = ?",
            (preview_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "试听不存在")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "试听音频已不存在")
    return FileResponse(path, media_type="audio/wav", filename=f"preview-{preview_id}.wav")


@app.get("/api/v1/runs/{run_id}/voiceovers")
def list_voiceovers(run_id: str) -> list[dict]:
    """该 Run 的授权配音列表（控制台选择配音用）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        run = _resolve_run_scope(db, run_id)
        if run["owner_id"] != owner_id or run["project_id"] != project_id:
            raise HTTPException(403, "越权访问 Run")
        rows = db.execute(
            "SELECT * FROM voiceovers WHERE run_id = ? ORDER BY created_at DESC", (run_id,),
        ).fetchall()
    return [{"id": row["id"], "locale": row["locale"], "license_ref": row["license_ref"],
             "created_at": row["created_at"], "download_url": f"/api/v1/voiceovers/{row['id']}/content",
             **json.loads(row["payload"])} for row in rows]


@app.get("/api/v1/voiceovers/{voiceover_id}/content")
def get_voiceover_content(voiceover_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM voiceovers WHERE id = ? AND owner_id = ? AND project_id = ?",
            (voiceover_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "配音不存在")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "配音文件已不存在")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/v1/audio/mixes")
def list_audio_mixes(run_id: str = "") -> list[dict]:
    """混音列表（可按 Run 过滤）；控制台打包时选择混音用。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        if run_id:
            rows = db.execute(
                "SELECT * FROM audio_mixes WHERE run_id = ? AND owner_id = ? AND project_id = ? "
                "ORDER BY created_at DESC", (run_id, owner_id, project_id),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM audio_mixes WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC LIMIT 100",
                (owner_id, project_id),
            ).fetchall()
    mixes = []
    for row in rows:
        payload = json.loads(row["payload"])
        mixes.append({
            "id": row["id"], "run_id": row["run_id"], "created_at": row["created_at"],
            "path": row["path"], "duration_s": payload.get("duration_s"),
            "measurement": payload.get("measurement"), "verdict": payload.get("verdict"),
            "ducking": payload.get("ducking"), "voice": payload.get("voice"), "music": payload.get("music"),
            "download_url": f"/api/v1/audio/mixes/{row['id']}/content",
        })
    return mixes


@app.post("/api/v1/runs/{run_id}/voiceovers", status_code=201)
async def upload_voiceover(
    run_id: str,
    file: UploadFile = File(...),
    locale: str = Form(default="es-MX"),
    license_ref: str = Form(default=""),
    notes: str = Form(default=""),
) -> dict:
    """上传已授权配音（无 TTS Provider 时的合法路线）；记录许可引用，不做二次分发。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        run = _resolve_run_scope(db, run_id)
    if run["owner_id"] != owner_id or run["project_id"] != project_id:
        raise HTTPException(403, "越权访问 Run")
    payload_bytes = await file.read()
    if len(payload_bytes) > MAX_VOICEOVER_BYTES:
        raise HTTPException(413, f"配音文件超过 {MAX_VOICEOVER_BYTES // (1024 * 1024)}MB 上限")
    suffix = Path(file.filename or "voiceover.wav").suffix.lower() or ".wav"
    if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}:
        raise HTTPException(422, f"不支持的配音格式 {suffix}")
    voiceover_id = str(uuid.uuid4())
    directory = VAR / "voiceovers" / voiceover_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"voiceover{suffix}"
    path.write_bytes(payload_bytes)
    if not FFMPEG:
        raise HTTPException(503, "FFmpeg 未安装，无法校验配音")
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration,format_name", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        probe_payload = json.loads(probe.stdout)["format"]
    except (KeyError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        raise HTTPException(422, "配音文件无法解析为音频")
    duration = float(probe_payload.get("duration") or 0.0)
    payload = {
        "run_id": run_id, "locale": locale, "license_ref": license_ref, "notes": notes,
        "bytes": len(payload_bytes), "duration_s": round(duration, 3),
        "format": probe_payload.get("format_name"), "sha256": _file_sha256(path),
        "origin": "owner_upload",
        "note": "上传的已授权配音；音乐/配音原文件默认不进入发布包，仅保留许可引用与混音记录",
    }
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO voiceovers(id, run_id, owner_id, project_id, asset_id, locale, license_ref, path, payload, "
            "payload_sha256, created_at) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)",
            (voiceover_id, run_id, owner_id, project_id, locale, license_ref, str(path), payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
        )
    return {"id": voiceover_id, "path": str(path), **payload}


# ---------------------------------------------------------------------------
# V6-03：BGM、ducking 混音、响度测量、时长适配与编码输出
# ---------------------------------------------------------------------------

MAX_MUSIC_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SOURCE_BYTES = 500 * 1024 * 1024
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


class AudioMixRequest(BaseModel):
    # None = 自动取该 Run 最新配音；"" = 显式关闭该音轨（开关必须真的改变输出）
    voiceover_id: str | None = Field(default=None, max_length=80)
    music_asset_id: str | None = Field(default=None, max_length=80)
    profile_id: str = Field(default="", max_length=80)
    duration_s: float | None = Field(default=None, gt=0, le=3600)
    voice_gain_db: float = Field(default=0.0, ge=-30.0, le=30.0)
    music_gain_db: float = Field(default=-18.0, ge=-60.0, le=12.0)
    ducking: dict = Field(default_factory=dict)
    fade_in_s: float = Field(default=1.0, ge=0.0, le=30.0)
    fade_out_s: float = Field(default=1.0, ge=0.0, le=30.0)
    loop_music: bool = True
    target_lufs: float | None = Field(default=None, ge=-40.0, le=-6.0)
    true_peak_max_dbtp: float | None = Field(default=None, ge=-6.0, le=0.0)
    notes: str = Field(default="", max_length=2000)


class DurationAdaptRequest(BaseModel):
    policy: Literal["extend_non_contact_shot", "adjust_speech_rate", "rewrite_copy", "fail_and_review"]
    voiceover_id: str = Field(default="", max_length=80)
    audio_duration_s: float | None = Field(default=None, gt=0, le=3600)
    profile_id: str = Field(default="", max_length=80)
    preset_id: str = Field(default="", max_length=80)
    interaction_plan_id: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=2000)


class OutputRenderRequest(BaseModel):
    profile_id: str = Field(default="", max_length=80)
    video_source_id: str = Field(default="", max_length=80)
    audio_mix_id: str = Field(default="", max_length=80)
    subtitle_track_id: str = Field(default="", max_length=80)
    clean_master: bool = True
    thumbnail_at_s: float = Field(default=0.5, ge=0.0, le=3600.0)
    notes: str = Field(default="", max_length=2000)


def _music_asset_public(row) -> dict:
    payload = json.loads(row["payload"])
    return {
        "id": row["id"], "name": row["name"], "license_ref": row["license_ref"],
        "commercial_use_allowed": bool(row["commercial_use_allowed"]),
        "duration_s": row["duration_s"], "path": row["path"], "created_at": row["created_at"],
        "download_url": f"/api/v1/music-assets/{row['id']}/content",
        "sha256": payload.get("sha256"),
        "note": "原文件默认不进发布包；仅保留许可引用与混音记录",
    }


def _profile_for_request(db, profile_id: str, owner_id: str, project_id: str):
    if not profile_id:
        return None
    row = db.execute(
        "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
        (profile_id, owner_id, project_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Profile 不存在")
    version = db.execute(
        "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
        (profile_id,),
    ).fetchone()
    return {"row": row, "version": version,
            "spec": platform_profiles.spec_from_payload(json.loads(version["payload"]))}


@app.get("/api/v1/music-assets")
def list_music_assets(
    owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID,
) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM music_assets WHERE owner_id = ? AND project_id = ? ORDER BY created_at ASC",
            (owner_id, project_id),
        ).fetchall()
        return [_music_asset_public(row) for row in rows]


@app.post("/api/v1/music-assets", status_code=201)
async def upload_music_asset(
    file: UploadFile = File(...),
    name: str = Form(default=""),
    license_ref: str = Form(...),
    commercial_use_allowed: bool = Form(default=False),
    notes: str = Form(default=""),
) -> dict:
    """上传 BGM 素材：**必须提供许可引用**，未知许可不得入库（主规划 12.4）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if not license_ref.strip():
        raise HTTPException(422, "必须提供 license_ref：未知商业许可不得默认用于商业发布")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise HTTPException(422, f"不支持的音频格式 {suffix}（支持 {sorted(AUDIO_SUFFIXES)}）")
    data = await file.read()
    if not data or len(data) > MAX_MUSIC_BYTES:
        raise HTTPException(413 if data else 422, f"文件为空或超过 {MAX_MUSIC_BYTES // (1024 * 1024)}MB 上限")
    asset_id = str(uuid.uuid4())
    directory = VAR / "music" / asset_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"music{suffix}"
    path.write_bytes(data)
    probe = audio_post.probe_audio(path)
    if not probe["has_audio"]:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "上传文件没有可解析的音频流")
    payload = {
        "name": name or file.filename, "license_ref": license_ref, "commercial_use_allowed": commercial_use_allowed,
        "duration_s": probe["duration_s"], "channels": probe["channels"], "sample_rate": probe["sample_rate"],
        "sha256": _file_sha256(path), "notes": notes, "origin": "owner_upload",
    }
    payload_text = _canonical_json_text(payload)
    with connect() as db:
        db.execute(
            "INSERT INTO music_assets(id, owner_id, project_id, name, license_ref, commercial_use_allowed, path, "
            "duration_s, payload, payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, owner_id, project_id, payload["name"], license_ref, int(bool(commercial_use_allowed)),
             str(path), probe["duration_s"], payload_text, hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
             utc_now()),
        )
        row = db.execute("SELECT * FROM music_assets WHERE id = ?", (asset_id,)).fetchone()
        return _music_asset_public(row)


@app.get("/api/v1/music-assets/{music_asset_id}/content")
def get_music_asset_content(music_asset_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM music_assets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (music_asset_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "BGM 素材不存在")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "BGM 文件已不存在")
    return FileResponse(path, media_type="audio/*", filename=Path(row["path"]).name)


@app.post("/api/v1/runs/{run_id}/audio/mix", status_code=201)
def mix_run_audio(run_id: str, request: AudioMixRequest) -> dict:
    """真实混音：旁白优先（ducking）+ 响度归一，返回实测响度与判定。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        run = _resolve_run_scope(db, run_id)
        if run["owner_id"] != owner_id or run["project_id"] != project_id:
            raise HTTPException(403, "越权访问 Run")
        voiceover = None
        if request.voiceover_id is None:
            voiceover = db.execute(
                "SELECT * FROM voiceovers WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        elif request.voiceover_id:
            voiceover = db.execute(
                "SELECT * FROM voiceovers WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.voiceover_id, owner_id, project_id),
            ).fetchone()
            if not voiceover:
                raise HTTPException(404, "配音不存在")
        music = None
        if request.music_asset_id:
            music = db.execute(
                "SELECT * FROM music_assets WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.music_asset_id, owner_id, project_id),
            ).fetchone()
            if not music:
                raise HTTPException(404, "BGM 素材不存在")
        profile = _profile_for_request(db, request.profile_id, owner_id, project_id)
    if voiceover is None and music is None:
        raise HTTPException(422, "配音与 BGM 都为关闭状态：不会生成静音混音（开关必须真的改变输出）")
    if profile and music is not None:
        commercial_targets = {"ads_material", "listing"}
        if profile["spec"].publish.output_target in commercial_targets and not bool(music["commercial_use_allowed"]):
            raise HTTPException(422, {
                "code": "MUSIC_NOT_COMMERCIAL",
                "detail": "该 Profile 用于商业用途，但 BGM 未标注允许商业使用（未知商业许可不得默认用于商业发布）",
            })
    duration = request.duration_s
    if duration is None:
        plan_payload = json.loads(run["plan_payload"] or "{}")
        output = plan_payload.get("output") or {}
        fps = int(output.get("fps") or DEFAULT_FPS)
        frame_count = output.get("frame_count")
        duration = (float(frame_count) / fps) if frame_count else float(output.get("duration_seconds") or 6.0)
    mix_id = str(uuid.uuid4())
    out_path = VAR / "audio-mixes" / f"{mix_id}.wav"
    try:
        result = audio_post.mix_tracks(
            voice_path=Path(voiceover["path"]) if voiceover else None,
            music_path=Path(music["path"]) if music else None,
            out_path=out_path, duration_s=float(duration),
            voice_gain_db=request.voice_gain_db, music_gain_db=request.music_gain_db,
            ducking=request.ducking, fade_in_s=request.fade_in_s, fade_out_s=request.fade_out_s,
            loop_music=request.loop_music,
            target_lufs=request.target_lufs if request.target_lufs is not None else audio_post.DEFAULT_TARGET_LUFS,
            true_peak_max_dbtp=(request.true_peak_max_dbtp if request.true_peak_max_dbtp is not None
                                else audio_post.DEFAULT_TRUE_PEAK_DBTP),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, {"code": "MIX_FAILED", "detail": str(exc)}) from exc
    payload = {
        "run_id": run_id,
        "voiceover_id": voiceover["id"] if voiceover else None,
        "music_asset_id": music["id"] if music else None,
        "music_license_ref": music["license_ref"] if music else None,
        "music_commercial_use_allowed": bool(music["commercial_use_allowed"]) if music else None,
        "profile_id": request.profile_id,
        "duration_s": float(duration),
        "voice": result["voice"], "music": result["music"], "ducking": result["ducking"],
        "measurement": result["measurement"], "verdict": result["verdict"],
        "notes": request.notes,
    }
    payload_text = _canonical_json_text(payload)
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO audio_mixes(id, run_id, owner_id, project_id, voiceover_id, music_asset_id, path, payload, "
            "payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mix_id, run_id, owner_id, project_id, voiceover["id"] if voiceover else None,
             music["id"] if music else None, str(out_path), payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
        )
    return {"id": mix_id, "path": str(out_path), "sha256": _file_sha256(out_path),
            "download_url": f"/api/v1/audio/mixes/{mix_id}/content", **payload,
            "usage": record_internal_usage(
                provider="ffmpeg", unit="cpu_second", quantity=float(duration),
                operation_id=mix_id, capability="audio_mix", run_id=run_id,
                note=f"混音 {mix_id} 的 CPU 秒（按输出时长折算，内部估算）")}


@app.get("/api/v1/audio/mixes/{mix_id}/content")
def get_audio_mix_content(mix_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM audio_mixes WHERE id = ? AND owner_id = ? AND project_id = ?",
            (mix_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "混音不存在")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "混音文件已不存在")
    return FileResponse(path, media_type="audio/wav", filename=f"mix-{mix_id}.wav")


@app.post("/api/v1/runs/{run_id}/audio/adapt", status_code=201)
def adapt_run_duration(run_id: str, request: DurationAdaptRequest) -> dict:
    """配音过长的显式适配：只允许主规划 12.4 的三种策略。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        run = _resolve_run_scope(db, run_id)
        if run["owner_id"] != owner_id or run["project_id"] != project_id:
            raise HTTPException(403, "越权访问 Run")
        audio_duration = request.audio_duration_s
        voiceover = None
        if request.voiceover_id:
            voiceover = db.execute(
                "SELECT * FROM voiceovers WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.voiceover_id, owner_id, project_id),
            ).fetchone()
            if not voiceover:
                raise HTTPException(404, "配音不存在")
        elif audio_duration is None:
            voiceover = db.execute(
                "SELECT * FROM voiceovers WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if audio_duration is None and voiceover is not None:
            audio_duration = float(json.loads(voiceover["payload"]).get("duration_s") or 0.0)
        if audio_duration is None:
            raise HTTPException(422, "缺少配音或显式 audio_duration_s，无法判断时长适配")
        profile = _profile_for_request(db, request.profile_id, owner_id, project_id)
        preset_spec = None
        if request.preset_id:
            preset_row = db.execute(
                "SELECT * FROM postproduction_presets WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.preset_id, owner_id, project_id),
            ).fetchone()
            if not preset_row:
                raise HTTPException(404, "后期模板不存在")
            preset_version = db.execute(
                "SELECT * FROM postproduction_preset_versions WHERE preset_id = ? ORDER BY version DESC LIMIT 1",
                (request.preset_id,),
            ).fetchone()
            preset_spec = platform_profiles.PostproductionPresetSpec.model_validate(
                json.loads(preset_version["payload"])
            )
        interaction_plan = None
        if request.interaction_plan_id:
            interaction_row = db.execute(
                "SELECT * FROM interaction_plans WHERE id = ?", (request.interaction_plan_id,),
            ).fetchone()
            if interaction_row:
                interaction_payload = json.loads(interaction_row["payload"])
                plan_row = db.execute("SELECT payload FROM plans WHERE id = ?", (run["plan_id"],)).fetchone()
                plan_shots = (json.loads(plan_row["payload"]) or {}).get("shots") or []
                first_contact = next(
                    (shot["id"] for shot in plan_shots
                     if str(interaction_payload.get("action") or "") in {"press_button", "single_punch_target"}),
                    None,
                )
                interaction_plan = {"action": interaction_payload.get("action"), "shot_id": first_contact}
    plan_payload = json.loads(run["plan_payload"] or "{}")
    extend_max = int(preset_spec.extend_max_frames) if preset_spec else 0
    rate_range = (0.9, 1.1)
    if preset_spec and preset_spec.voice.enabled:
        rate_range = (max(0.5, preset_spec.voice.rate * 0.9), min(2.0, preset_spec.voice.rate * 1.1))
    profile_max = profile["spec"].video.duration_max_seconds if profile else None
    decision = audio_post.adapt_plan_duration(
        plan_payload, audio_duration_s=float(audio_duration), policy=request.policy,
        extend_max_frames=extend_max,
        rate=preset_spec.voice.rate if preset_spec else 1.0,
        rate_range=rate_range, profile_max_seconds=profile_max, interaction_plan=interaction_plan,
    )
    adaptation_id = str(uuid.uuid4())
    payload = {
        "run_id": run_id, "policy": request.policy, "audio_duration_s": float(audio_duration),
        "voiceover_id": voiceover["id"] if voiceover else None,
        "profile_id": request.profile_id, "preset_id": request.preset_id,
        "interaction_plan_id": request.interaction_plan_id,
        "decision": decision, "notes": request.notes,
        "allowed_policies": ["extend_non_contact_shot", "adjust_speech_rate", "rewrite_copy"],
        "constraints": {
            "contact_shots_must_not_change": True,
            "no_truncation_of_video_or_voice": True,
            "profile_max_seconds": profile_max,
            "extend_max_frames": extend_max,
            "speech_rate_range": list(rate_range),
        },
    }
    payload_text = _canonical_json_text(payload)
    with connect() as db:
        db.execute(
            "INSERT INTO duration_adaptations(id, run_id, policy, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (adaptation_id, run_id, request.policy, payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), utc_now()),
        )
    return {"id": adaptation_id, **payload}


@app.post("/api/v1/video-sources", status_code=201)
async def upload_video_source(
    file: UploadFile = File(...),
    run_id: str = Form(default=""),
    name: str = Form(default=""),
    notes: str = Form(default=""),
) -> dict:
    """登记待编码的 Master 视频（真实渲染产物）；保留来源链供 lineage 记录。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(422, f"不支持的视频格式 {suffix}（支持 {sorted(VIDEO_SUFFIXES)}）")
    data = await file.read()
    if not data or len(data) > MAX_VIDEO_SOURCE_BYTES:
        raise HTTPException(413 if data else 422, f"文件为空或超过 {MAX_VIDEO_SOURCE_BYTES // (1024 * 1024)}MB 上限")
    source_id = str(uuid.uuid4())
    directory = VAR / "video-sources" / source_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"master{suffix}"
    path.write_bytes(data)
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        info = json.loads(probe.stdout)
    except json.JSONDecodeError:
        info = {}
    video_stream = next((s for s in (info.get("streams") or []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "上传文件没有可解析的视频流")
    payload = {
        "run_id": run_id or None, "name": name or file.filename, "notes": notes,
        "width": video_stream.get("width"), "height": video_stream.get("height"),
        "fps": video_stream.get("r_frame_rate"), "nb_frames": video_stream.get("nb_frames"),
        "duration_s": round(float((info.get("format") or {}).get("duration") or 0), 3),
        "sha256": _file_sha256(path), "bytes": len(data),
    }
    payload_text = _canonical_json_text(payload)
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO video_sources(id, run_id, owner_id, project_id, name, path, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, run_id or None, owner_id, project_id, payload["name"], str(path), payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
        )
    return {"id": source_id, "path": str(path), **payload}


@app.post("/api/v1/runs/{run_id}/outputs", status_code=201)
def render_outputs(run_id: str, request: OutputRenderRequest) -> dict:
    """按 Profile 规格编码成片（可带混音与烧录字幕）、可选无字幕 Master 与封面。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        run = _resolve_run_scope(db, run_id)
        if run["owner_id"] != owner_id or run["project_id"] != project_id:
            raise HTTPException(403, "越权访问 Run")
        source = None
        if request.video_source_id:
            source = db.execute(
                "SELECT * FROM video_sources WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.video_source_id, owner_id, project_id),
            ).fetchone()
            if not source:
                raise HTTPException(404, "视频来源不存在")
        mix = None
        if request.audio_mix_id:
            mix = db.execute(
                "SELECT * FROM audio_mixes WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.audio_mix_id, owner_id, project_id),
            ).fetchone()
            if not mix:
                raise HTTPException(404, "混音不存在")
        subtitle = None
        if request.subtitle_track_id:
            subtitle = db.execute(
                "SELECT * FROM subtitle_tracks WHERE id = ?", (request.subtitle_track_id,),
            ).fetchone()
            if not subtitle:
                raise HTTPException(404, "字幕轨不存在")
        profile = _profile_for_request(db, request.profile_id, owner_id, project_id)
    if source is None:
        raise HTTPException(422, "缺少 video_source_id：编码需要真实 Master 视频")
    source_payload = json.loads(source["payload"])
    width = profile["spec"].video.width if profile else int(source_payload.get("width") or 540)
    height = profile["spec"].video.height if profile else int(source_payload.get("height") or 960)
    fps = profile["spec"].video.fps if profile else 24
    codec = profile["spec"].video.codec if profile else "h264"
    out_dir = VAR / "outputs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = uuid.uuid4().hex[:8]
    rendition_ids: list[dict] = []
    now = utc_now()

    def _write_rendition(path: Path, kind: str, extra: dict, report: dict) -> dict:
        payload = {
            "run_id": run_id, "profile_id": request.profile_id, "kind": kind,
            "video_source_id": source["id"], "video_source_sha256": source_payload.get("sha256"),
            "audio_mix_id": mix["id"] if mix else None,
            "audio_mix_sha256": _file_sha256(Path(mix["path"])) if mix else None,
            "subtitle_track_id": subtitle["id"] if subtitle else None,
            "profile_version": profile["version"]["version"] if profile else None,
            "profile_payload_sha256": profile["version"]["payload_sha256"] if profile else None,
            "spec": {"width": width, "height": height, "fps": fps, "codec": codec,
                     "container": profile["spec"].video.container if profile else "mp4"},
            "report": report, "sha256": _file_sha256(path), "bytes": path.stat().st_size,
            "note": "保留渲染 Master 与发布编码之间的来源链",
            **extra,
        }
        payload_text = _canonical_json_text(payload)
        rendition_id = str(uuid.uuid4())
        with connect() as db:
            db.execute(
                "INSERT INTO output_renditions(id, run_id, owner_id, project_id, profile_id, kind, path, payload, "
                "payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rendition_id, run_id, owner_id, project_id, request.profile_id or None, kind, str(path),
                 payload_text, hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now),
            )
        rendition_ids.append({"id": rendition_id, "kind": kind, "path": str(path), **payload})
        return rendition_ids[-1]

    final_path = out_dir / f"final-{stamp}.mp4"
    burn_report = None
    if subtitle is not None:
        srt_files = [item for item in json.loads(subtitle["payload"]).get("files", []) if item["format"] == "srt"]
        if not srt_files:
            raise HTTPException(422, "字幕轨没有 SRT 文件，无法烧录")
        burn_path = out_dir / f"burned-{stamp}.mp4"
        burn = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", str(Path(source["path"])),
             "-vf", f"subtitles={srt_files[0]['path']}:force_style='FontName=DejaVu Sans'",
             "-an", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(burn_path)],
            capture_output=True, text=True,
        )
        if burn.returncode != 0 or not burn_path.exists():
            raise HTTPException(422, {"code": "BURN_FAILED", "detail": (burn.stderr or "")[-300:]})
        burn_report = {"burned_video": str(burn_path), "srt": srt_files[0]["path"],
                       "note": "烧录字幕版本仅用于最终成片；无字幕 Master 单独产出"}
        encode_source = burn_path
    else:
        encode_source = Path(source["path"])
    crop_policy = profile["spec"].composition.crop_policy if profile else "letterbox"
    source_size = {"width": source_payload.get("width"), "height": source_payload.get("height")}
    try:
        report = audio_post.encode_rendition(
            video_path=encode_source, audio_path=Path(mix["path"]) if mix else None, out_path=final_path,
            width=width, height=height, fps=fps, codec=codec,
            container=profile["spec"].video.container if profile else "mp4",
            crop_policy=crop_policy, source_size=source_size,
        )
    except ValueError as exc:
        raise HTTPException(422, {"code": "GEOMETRY_UNSUPPORTED", "detail": str(exc)}) from exc
    _write_rendition(final_path, "final", {"burned_subtitles": bool(subtitle), "burn_report": burn_report}, report)
    clean_path = None
    if request.clean_master:
        if subtitle is not None:
            clean_path = out_dir / f"clean_master-{stamp}.mp4"
            clean_report = audio_post.encode_rendition(
                video_path=Path(source["path"]), audio_path=Path(mix["path"]) if mix else None,
                out_path=clean_path, width=width, height=height, fps=fps, codec=codec,
                crop_policy=crop_policy, source_size=source_size,
            )
        else:
            clean_report = dict(report)
            clean_path = final_path
        _write_rendition(clean_path, "clean_master",
                         {"burned_subtitles": False, "same_as_final": clean_path == final_path}, clean_report)
    thumbnail = audio_post.make_thumbnail(final_path, out_dir / f"thumbnail-{stamp}.jpg",
                                          at_seconds=request.thumbnail_at_s)
    _write_rendition(Path(thumbnail["path"]), "thumbnail", {"at_seconds": thumbnail["at_seconds"]}, {"passed": True})
    audio_measurement = audio_post.measure_loudness(final_path) if mix else None
    return {
        "run_id": run_id,
        "renditions": rendition_ids,
        "final": next(item for item in rendition_ids if item["kind"] == "final"),
        "clean_master": next((item for item in rendition_ids if item["kind"] == "clean_master"), None),
        "thumbnail": next(item for item in rendition_ids if item["kind"] == "thumbnail"),
        "burn_report": burn_report,
        "final_loudness": audio_measurement,
        "final_loudness_verdict": audio_post.loudness_verdict(audio_measurement) if audio_measurement else None,
    }


@app.get("/api/v1/outputs/{rendition_id}")
def get_output_rendition(rendition_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM output_renditions WHERE id = ? AND owner_id = ? AND project_id = ?",
            (rendition_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "成片不存在")
    return {"id": row["id"], "kind": row["kind"], "profile_id": row["profile_id"],
            "created_at": row["created_at"], "path": row["path"],
            "download_url": f"/api/v1/outputs/{row['id']}/content",
            **json.loads(row["payload"])}


@app.get("/api/v1/outputs/{rendition_id}/content")
def get_output_rendition_content(rendition_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM output_renditions WHERE id = ? AND owner_id = ? AND project_id = ?",
            (rendition_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "成片不存在")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(404, "成片文件已不存在")
    media = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "video/mp4"
    return FileResponse(path, media_type=media, filename=path.name)


# ---------------------------------------------------------------------------
# V6-04：批次与变体（矩阵展开、并发调度、暂停/取消/失败重试）
# ---------------------------------------------------------------------------


class BatchMatrixSpec(BaseModel):
    product_version_ids: list[str] = Field(default_factory=list, max_length=50)
    plan_ids: list[str] = Field(default_factory=list, max_length=50)
    profile_ids: list[str] = Field(default_factory=list, max_length=50)
    variations_per_combination: int = Field(default=1, ge=1, le=20)
    seed_policy: Literal["plan_derived", "fixed", "per_variation"] = "plan_derived"
    base_seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    postproduction_preset_id: str = Field(default="", max_length=80)


class BatchCreateRequest(BaseModel):
    name: str = Field(default="", max_length=120)
    matrix: BatchMatrixSpec | None = None
    items: list[dict] = Field(default_factory=list, max_length=100)
    idempotency_key: str | None = Field(default=None, max_length=120)
    budget_limit: float | None = Field(default=None, ge=0)
    publish_intent: dict = Field(default_factory=dict)
    max_concurrent: int = Field(default=batch_rules.DEFAULT_MAX_CONCURRENT, ge=1, le=8)
    notes: str = Field(default="", max_length=2000)
    # V6-16（独立复核 BUG-06）：创建时必须带上前端预览返回的哈希；服务端复算不一致即拒绝，
    # 防止"改了参数/预览失败后仍用旧预览创建"。
    preview_hash: str | None = Field(default=None, max_length=80)


class BatchPreviewRequest(BaseModel):
    matrix: BatchMatrixSpec | None = None
    items: list[dict] = Field(default_factory=list, max_length=100)
    max_concurrent: int = Field(default=batch_rules.DEFAULT_MAX_CONCURRENT, ge=1, le=8)


class BatchActionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)
    scope: Literal["not_started", "all_unfinished"] = "not_started"
    item_filter: list[str] = Field(default_factory=list, max_length=100)


def _batch_public(db, row) -> dict:
    items = db.execute(
        "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY item_index ASC", (row["id"],),
    ).fetchall()
    summary = batch_rules.summarize([dict(item) for item in items])
    return {
        "id": row["id"], "name": row["name"], "status": row["status"],
        "revision": row["revision"], "paused": bool(row["paused"]), "cancelled": bool(row["cancelled"]),
        "summary": summary,
        "publish_status": json.loads(row["payload"]).get("publish_status", "NOT_REQUESTED"),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "payload": json.loads(row["payload"]),
    }


def _batch_item_public(row) -> dict:
    payload = json.loads(row["payload"])
    return {
        "id": row["id"], "batch_id": row["batch_id"], "index": row["item_index"],
        "status": row["status"], "run_id": row["run_id"], "job_id": row["job_id"],
        "cache_key": row["cache_key"], "error": row["error"],
        "updated_at": row["updated_at"], **payload,
    }


def _resolve_matrix_inputs(db, matrix: BatchMatrixSpec, owner_id: str, project_id: str) -> dict:
    """把矩阵字段解析为具体的版本行；任何缺失/越权都如实报错，不静默替换。"""
    products: list[dict] = []
    for version_id in matrix.product_version_ids:
        row = db.execute(
            "SELECT * FROM product_versions WHERE id = ? AND owner_id = ? AND project_id = ?",
            (version_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"产品版本不存在或越权: {version_id}")
        products.append({"id": row["id"], "product_asset_id": row["product_asset_id"],
                         "status": row["status"]})
    plans: list[dict] = []
    for plan_id in matrix.plan_ids:
        row = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"计划不存在: {plan_id}")
        if not row["approved"]:
            raise HTTPException(409, f"计划未批准，不能进入批次: {plan_id}")
        ensure_plan_contract(db, row, owner_id, project_id)
        contract = get_default_contract(plan_id, db, owner_id, project_id)
        plans.append({"plan_id": plan_id, "product_asset_id": row["product_asset_id"],
                      "product_version_id": contract["product_version_id"] if contract else None})
    profiles: list[dict] = []
    for profile_id in matrix.profile_ids:
        row = db.execute(
            "SELECT * FROM platform_profiles WHERE id = ? AND owner_id = ? AND project_id = ?",
            (profile_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Profile 不存在: {profile_id}")
        version = db.execute(
            "SELECT * FROM platform_profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        profiles.append({"profile_id": profile_id, "id": version["id"], "version": version["version"]})
    preset_version_id = ""
    if matrix.postproduction_preset_id:
        preset = db.execute(
            "SELECT * FROM postproduction_presets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (matrix.postproduction_preset_id, owner_id, project_id),
        ).fetchone()
        if not preset:
            raise HTTPException(404, "后期模板不存在")
        preset_version = db.execute(
            "SELECT * FROM postproduction_preset_versions WHERE preset_id = ? ORDER BY version DESC LIMIT 1",
            (matrix.postproduction_preset_id,),
        ).fetchone()
        preset_version_id = preset_version["id"]
    return {"products": products, "plans": plans, "profiles": profiles,
            "preset_version_id": preset_version_id}


def _expand_batch_request(db, request: BatchPreviewRequest | BatchCreateRequest, owner_id: str, project_id: str) -> dict:
    """矩阵与 items[] 互斥；展开结果同时给出总数与逐项明细。"""
    has_matrix = request.matrix is not None
    has_items = bool(request.items)
    if has_matrix and has_items:
        raise HTTPException(422, {
            "code": "matrix_and_items_exclusive",
            "detail": "矩阵展开字段与 items[] 不能同时使用：请二选一",
        })
    if not has_matrix and not has_items:
        raise HTTPException(422, {"code": "nothing_to_expand", "detail": "需要 matrix 或 items[] 之一"})
    try:
        if has_items:
            expanded = batch_rules.expand_items(request.items)
            expanded["mode"] = "items"
        else:
            resolved = _resolve_matrix_inputs(db, request.matrix, owner_id, project_id)
            expanded = batch_rules.expand_matrix(
                product_versions=resolved["products"], plan_versions=resolved["plans"],
                profile_versions=resolved["profiles"],
                variations_per_combination=request.matrix.variations_per_combination,
                seed_policy=request.matrix.seed_policy, base_seed=request.matrix.base_seed,
                postproduction_preset_version_id=resolved["preset_version_id"],
            )
            expanded["mode"] = "matrix"
            expanded["resolved"] = resolved
    except batch_rules.ExpansionError as exc:
        raise HTTPException(422, exc.as_detail()) from exc
    # 计划绑定产品一致性（items 路线也要校验）
    conflicts = []
    for item in expanded["items"]:
        plan_row = db.execute("SELECT * FROM plans WHERE id = ?", (item["plan_id"],)).fetchone()
        if not plan_row or not plan_row["approved"]:
            conflicts.append({"index": item["index"], "plan_id": item["plan_id"],
                              "reason": "计划不存在或未批准"})
            continue
        if item.get("product_version_id"):
            version = db.execute(
                "SELECT * FROM product_versions WHERE id = ?", (item["product_version_id"],),
            ).fetchone()
            if not version or version["product_asset_id"] != plan_row["product_asset_id"]:
                conflicts.append({
                    "index": item["index"], "plan_id": item["plan_id"],
                    "product_version_id": item["product_version_id"],
                    "reason": "产品版本与已批准计划绑定的产品不一致",
                })
    if conflicts:
        raise HTTPException(422, {
            "code": "incompatible_combination",
            "detail": f"{len(conflicts)} 项与已批准计划的产品绑定不一致",
            "conflicts": conflicts,
        })
    return expanded


def _batch_preview_hash(expanded: dict, max_concurrent: int) -> str:
    """预览指纹：覆盖展开结果与并发上限，任何参数变化都会得到不同哈希。"""
    material = {
        "mode": expanded.get("mode"),
        "expanded_count": expanded.get("expanded_count"),
        "max_concurrent": int(max_concurrent),
        "items": [
            {key: item.get(key) for key in ("index", "plan_id", "profile_id", "product_version_id",
                                            "profile_version_id", "variation", "seed", "cache_key")}
            for item in expanded.get("items", [])
        ],
    }
    return hashlib.sha256(_canonical_json_text(material).encode("utf-8")).hexdigest()


@app.post("/api/v1/batches/preview")
def preview_batch(request: BatchPreviewRequest) -> dict:
    """只做展开与校验，不创建任何任务（展开数量由服务端给出）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        expanded = _expand_batch_request(db, request, owner_id, project_id)
    preview_hash = _batch_preview_hash(expanded, request.max_concurrent)
    return {
        "mode": expanded["mode"],
        "expanded_count": expanded["expanded_count"],
        "items": expanded["items"],
        "duplicates": expanded["duplicates"],
        "dedupe_note": expanded["dedupe_note"],
        "preview_hash": preview_hash,
        "limits": {"max_expanded_items": batch_rules.MAX_EXPANDED_ITEMS,
                   "max_concurrent": request.max_concurrent},
        "side_effects": "none（预览不创建任务）",
        "preview_note": ("创建批次时必须带同一个 preview_hash；矩阵/变体/并发任一变化都会得到新哈希，"
                         "服务端会拒绝过期预览"),
    }


@app.post("/api/v1/batches", status_code=202)
def create_batch(request: BatchCreateRequest, background: BackgroundTasks, response: Response,
                 http_request: Request) -> dict:
    return _create_batch_endpoint(request, background, response, http_request, scope="batches.create")


def _create_batch_endpoint(request: BatchCreateRequest, background: BackgroundTasks, response: Response,
                           http_request: Request, scope: str) -> dict:
    """批次创建入口（UI 与 Automation 共用同一实现，不另写绕过校验的快速路线）。"""
    gate = _idempotency_gate(http_request, scope, request.model_dump())
    replay = gate.replay()
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    result = _create_batch_record(request, background)
    gate.store(result, 202)
    return result


def _create_batch_record(request: BatchCreateRequest, background: BackgroundTasks) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        if request.idempotency_key:
            existing = db.execute(
                "SELECT * FROM batches WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, request.idempotency_key),
            ).fetchone()
            if existing:
                return {"batch": _batch_public(db, existing), "reused_idempotent": True, "created": False}
        expanded = _expand_batch_request(db, request, owner_id, project_id)
        # V6-16（BUG-06）：带 preview_hash 时服务端复算，拒绝过期预览（前端必须重跑预览）
        if request.preview_hash:
            recomputed = _batch_preview_hash(expanded, request.max_concurrent)
            if recomputed != request.preview_hash:
                raise HTTPException(409, {
                    "code": "preview_stale",
                    "detail": "预览已过期：参数（矩阵/变体/并发）与预览时不一致，请重新预览后再创建",
                    "expected_preview_hash": recomputed,
                    "submitted_preview_hash": request.preview_hash,
                })
        batch_id = str(uuid.uuid4())
        now = utc_now()
        publish_intent = batch_rules.normalize_publish_intent(request.publish_intent)
        payload = {
            "mode": expanded["mode"],
            "expanded_count": expanded["expanded_count"],
            "duplicates": expanded["duplicates"],
            "max_concurrent": request.max_concurrent,
            "budget_limit": request.budget_limit,
            "publish_intent": publish_intent,
            "publish_status": "NOT_REQUESTED",
            "notes": request.notes,
            "requested_total": expanded.get("requested_total"),
            "preview_hash": request.preview_hash,
        }
        payload_text = _canonical_json_text(payload)
        db.execute(
            "INSERT INTO batches(id, owner_id, project_id, name, status, revision, paused, cancelled, "
            "idempotency_key, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'QUEUED', 1, 0, 0, ?, ?, ?, ?, ?)",
            (batch_id, owner_id, project_id, request.name or f"批次 {batch_id[:8]}", request.idempotency_key,
             payload_text, hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now, now),
        )
        for item in expanded["items"]:
            item_payload = {
                "product_version_id": item.get("product_version_id"),
                "plan_id": item["plan_id"],
                "profile_id": item.get("profile_id"),
                "profile_version_id": item.get("profile_version_id"),
                "variation": item.get("variation", 1),
                "seed": item.get("seed"),
                "request_item_key": item.get("request_item_key", ""),
                "index": item["index"],
            }
            item_text = _canonical_json_text(item_payload)
            db.execute(
                "INSERT INTO batch_items(id, batch_id, item_index, status, run_id, job_id, cache_key, error, "
                "payload, payload_sha256, created_at, updated_at) VALUES (?, ?, ?, 'PENDING', NULL, NULL, ?, NULL, ?, ?, ?, ?)",
                (str(uuid.uuid4()), batch_id, item["index"], item["cache_key"], item_text,
                 hashlib.sha256(item_text.encode("utf-8")).hexdigest(), now, now),
            )
        row = db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        public = _batch_public(db, row)
    if not INLINE_EXECUTOR_DISABLED:
        background.add_task(advance_batch, batch_id)
    return {"batch": public, "reused_idempotent": False, "created": True}


def _existing_or_new_item_run(db, batch_row, item_row) -> dict:
    """幂等地为批次项取得 Run：已有同键 Run 就复用，否则新建。

    并发调度下同一项可能已被其他调度器建过 Run（`runs` 的 `(plan_id, idempotency_key)`
    唯一索引会拒绝第二次插入）；复用已有 Run 比报错更安全，也不会重复渲染。
    """
    payload = json.loads(item_row["payload"])
    existing = db.execute(
        "SELECT * FROM runs WHERE plan_id = ? AND idempotency_key = ?",
        (payload["plan_id"], f"batch:{item_row['batch_id']}:{item_row['item_index']}"),
    ).fetchone()
    if existing:
        _requeue_missing_run_job(db, existing["id"], existing["job_id"])
        return {"run_id": existing["id"], "job_id": existing["job_id"], "plan_contract_id": existing["plan_contract_id"],
                "reused": True}
    created = start_batch_item_run(db, batch_row, item_row)
    return {**created, "reused": False}


def start_batch_item_run(db, batch_row, item_row) -> dict:
    """为批次项创建独立 Run（每项独立失败/取消，不影响其他项）。"""
    payload = json.loads(item_row["payload"])
    plan_row = db.execute("SELECT * FROM plans WHERE id = ?", (payload["plan_id"],)).fetchone()
    if not plan_row:
        raise HTTPException(409, f"计划不存在: {payload['plan_id']}")
    contract = get_default_contract(plan_row["id"], db, batch_row["owner_id"], batch_row["project_id"])
    if contract is None:
        raise HTTPException(409, f"计划缺少冻结合同，无法启动批次项: {payload['plan_id']}")
    asset = db.execute("SELECT * FROM assets WHERE id = ?", (plan_row["product_asset_id"],)).fetchone()
    if not asset:
        raise HTTPException(409, "计划绑定的产品素材不存在")
    plan_snapshot = json.loads(plan_row["payload"])
    run_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = utc_now()
    request_hash = hashlib.sha256(
        _canonical_json_text({"batch_id": item_row["batch_id"], "index": item_row["item_index"],
                              "cache_key": item_row["cache_key"]}).encode("utf-8")
    ).hexdigest()
    # 先写 jobs 再写 runs：runs.job_id 有外键指向 jobs（SQLite 默认不校验，PostgreSQL 会拒绝）
    db.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, 'QUEUED', 'QUEUED', 0, NULL, NULL, NULL, 0, ?, ?)",
        (job_id, payload["plan_id"], asset["id"], asset["kind"], now, now),
    )
    db.execute(
        "INSERT INTO runs(id, plan_id, plan_contract_id, owner_id, request_hash, idempotency_key, status, stage, "
        "progress, job_id, attempt_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', 'QUEUED', 0, ?, 1, ?, ?)",
        (run_id, payload["plan_id"], contract["id"], batch_row["owner_id"], request_hash,
         f"batch:{item_row['batch_id']}:{item_row['item_index']}", job_id, now, now),
    )
    # 队列行（run_jobs）必须一起写：Worker 只从 run_jobs 领取任务，缺了它任务永远不会被执行
    _requeue_missing_run_job(db, run_id, job_id)
    # 执行器从 run 目录读取冻结计划快照：不写它，任务会在领取后立刻失败
    ensure_run_plan_snapshot(job_id, plan_snapshot)
    return {"run_id": run_id, "job_id": job_id, "plan_contract_id": contract["id"]}


SCHEDULING_STALE_SECONDS = 300


def _requeue_missing_run_job(db, run_id: str, job_id: str) -> bool:
    """补建缺失的 run_jobs 队列行：Worker 只从 run_jobs 领取任务。

    真实缺陷证据：产线上有批次项的 jobs/runs 已建但队列行为 0，项停在 QUEUED 永不执行。
    对账时补建（幂等）让这些项重新可被领取，不需要人工改库。
    """
    existing = db.execute("SELECT 1 FROM run_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if existing:
        return False
    run = db.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not run:
        return False
    now = utc_now()
    run_job_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO run_jobs (id, run_id, job_id, attempt, status, stage, created_at, updated_at, error) "
        "VALUES (?, ?, ?, 1, 'QUEUED', 'PREPARE', ?, ?, NULL)",
        (run_job_id, run_id, job_id, now, now),
    )
    db.execute(
        "INSERT INTO job_attempts (id, run_job_id, attempt, status, started_at, completed_at, error, metadata) "
        "VALUES (?, ?, 1, 'CREATED', ?, NULL, NULL, NULL)",
        (str(uuid.uuid4()), run_job_id, now),
    )
    return True


def ensure_run_plan_snapshot(job_id: str, plan_payload: dict) -> Path:
    """执行前必须存在 run 目录与 director_plan.json：执行器就是从这里读冻结计划快照的。"""
    run_dir = RUNS / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan_path = run_dir / "director_plan.json"
    if not plan_path.exists():
        plan_path.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan_path


def reconcile_batch_items(db, batch_id: str) -> None:
    """把关联 Run/Job 的终态回写到批次项；补建缺失队列行；释放超时的调度认领。"""
    items = db.execute(
        "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY item_index ASC", (batch_id,),
    ).fetchall()
    for item in items:
        if item["status"] == "SCHEDULING" and not item["run_id"]:
            # 认领后崩溃会留下 SCHEDULING：超时未落地 Run 就退回 PENDING，避免项永久卡住
            age = _epoch_from_iso(utc_now()) - _epoch_from_iso(item["updated_at"])
            if age > SCHEDULING_STALE_SECONDS:
                db.execute("UPDATE batch_items SET status = 'PENDING', error = ?, updated_at = ? WHERE id = ?",
                           (f"调度认领超时（{int(age)}s 未创建 Run），已退回待调度", utc_now(), item["id"]))
            continue
        if not item["job_id"] or item["status"] not in ("QUEUED", "RUNNING"):
            continue
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (item["job_id"],)).fetchone()
        if not job:
            continue
        if job["status"] in ("SUCCEEDED", "VERIFICATION_PASSED", "FAILED", "CANCELLED", "QA_REJECTED"):
            new_status = {
                "SUCCEEDED": "SUCCEEDED", "VERIFICATION_PASSED": "SUCCEEDED",
                "FAILED": "FAILED", "QA_REJECTED": "FAILED", "CANCELLED": "CANCELLED",
            }[job["status"]]
            db.execute("UPDATE batch_items SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                       (new_status, job["error"], utc_now(), item["id"]))
            _emit_item_terminal_event(db, item, job, new_status)
        elif job["status"] == "RUNNING" and item["status"] != "RUNNING":
            db.execute("UPDATE batch_items SET status = 'RUNNING', updated_at = ? WHERE id = ?",
                       (utc_now(), item["id"]))
        elif job["status"] == "QUEUED" and item["run_id"]:
            _requeue_missing_run_job(db, item["run_id"], item["job_id"])


def _emit_item_terminal_event(db, item, job, new_status: str) -> None:
    """批次项进入终态时写 Outbox（与状态回写同一事务）。CANCELLED 不发事件（见报告）。"""
    batch = db.execute("SELECT * FROM batches WHERE id = ?", (item["batch_id"],)).fetchone()
    if not batch:
        return
    revision = int(batch["revision"] or 0)
    if new_status == "SUCCEEDED":
        emit_event(
            db, event_type="item.completed", aggregate_type="batch_item", aggregate_id=item["id"],
            aggregate_revision=revision,
            payload={"batch_id": item["batch_id"], "item_id": item["id"], "item_index": item["item_index"],
                     "run_id": item["run_id"], "job_id": item["job_id"], "status": "SUCCEEDED",
                     "cache_key": item["cache_key"]},
            owner_id=batch["owner_id"], project_id=batch["project_id"],
        )
        return
    if new_status == "FAILED":
        emit_event(
            db, event_type="item.failed", aggregate_type="batch_item", aggregate_id=item["id"],
            aggregate_revision=revision,
            payload={"batch_id": item["batch_id"], "item_id": item["id"], "item_index": item["item_index"],
                     "run_id": item["run_id"], "job_id": item["job_id"],
                     "status": job["status"], "error": (job["error"] or "")[:500]},
            owner_id=batch["owner_id"], project_id=batch["project_id"],
        )
        if job["status"] == "QA_REJECTED":
            emit_event(
                db, event_type="review.required", aggregate_type="run", aggregate_id=item["run_id"] or item["job_id"],
                aggregate_revision=revision,
                payload={"batch_id": item["batch_id"], "item_id": item["id"], "job_id": item["job_id"],
                         "run_id": item["run_id"], "status": "QA_REJECTED",
                         "error": (job["error"] or "")[:500],
                         "next_action": "需要人工复核后才能进入发布包与发布流程"},
                owner_id=batch["owner_id"], project_id=batch["project_id"],
            )


def refresh_batch_status(db, batch_id: str) -> sqlite3.Row:
    """对账批次项并重算聚合状态（读取批次/项之前都调用，避免状态滞后）。"""
    reconcile_batch_items(db, batch_id)
    rows = db.execute("SELECT status FROM batch_items WHERE batch_id = ?", (batch_id,)).fetchall()
    batch = db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    previous = batch["status"]
    status = batch_rules.aggregate_status([row["status"] for row in rows],
                                         paused=bool(batch["paused"]), cancelled=bool(batch["cancelled"]))
    db.execute("UPDATE batches SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), batch_id))
    if status != previous:
        if status == "RUNNING":
            emit_event(db, event_type="batch.started", aggregate_type="batch", aggregate_id=batch_id,
                       aggregate_revision=int(batch["revision"] or 0),
                       payload={"batch_id": batch_id, "name": batch["name"],
                                "status": status, "previous_status": previous,
                                "paused": bool(batch["paused"])},
                       owner_id=batch["owner_id"], project_id=batch["project_id"])
        elif status in ("COMPLETED", "FAILED", "CANCELLED"):
            summary = batch_rules.summarize([
                dict(row) for row in db.execute("SELECT * FROM batch_items WHERE batch_id = ?", (batch_id,)).fetchall()
            ])
            emit_event(db, event_type="batch.completed", aggregate_type="batch", aggregate_id=batch_id,
                       aggregate_revision=int(batch["revision"] or 0),
                       payload={"batch_id": batch_id, "name": batch["name"], "status": status,
                                "previous_status": previous, "summary": summary},
                       owner_id=batch["owner_id"], project_id=batch["project_id"])
    return db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()


def advance_batch(batch_id: str, *, execute_inline: bool = True) -> dict:
    """批次调度：按并发上限为 PENDING 项创建 Run，并更新聚合状态。暂停时只停新调度。

    `execute_inline=False` 时只调度（写 Run 与队列行），由 Worker 从 run_jobs 领取执行：
    调度 Worker 与投递 Worker 用这种方式，避免调度请求被整段渲染阻塞。

    并发安全（V6-09 负载实测到的真实缺陷）：多个调度器（API 内联执行器、调度 tick、
    Worker 完成回调）会同时推进同一批次。因此这里分三步，**每一项各自一个事务**：
    1) 短事务：按并发上限原子认领 PENDING 项（`WHERE status = 'PENDING'` 的条件更新）；
    2) 每项独立事务：为已认领项创建 Run + 队列行（唯一键冲突只影响该项，且已有 Run 直接复用）；
    3) 短事务：重算聚合状态。
    这样一项失败不会污染其他项的事务，也不会让整批推进被 500 打断。
    """
    with connect() as db:
        begin_immediate(db)
        batch = db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        batch_payload = json.loads(batch["payload"])
        max_concurrent = int(batch_payload.get("max_concurrent") or batch_rules.DEFAULT_MAX_CONCURRENT)
        paused, cancelled = bool(batch["paused"]), bool(batch["cancelled"])
        reconcile_batch_items(db, batch_id)
        items = db.execute(
            "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY item_index ASC", (batch_id,),
        ).fetchall()
        active = sum(1 for item in items if item["status"] in batch_rules.ACTIVE_ITEM_STATUSES)
        claimed: list[dict] = []
        if not paused and not cancelled:
            for item in items:
                if active + len(claimed) >= max_concurrent:
                    break
                if item["status"] != "PENDING":
                    continue
                result = db.execute(
                    "UPDATE batch_items SET status = 'SCHEDULING', updated_at = ? WHERE id = ? AND status = 'PENDING'",
                    (utc_now(), item["id"]),
                )
                if int(getattr(result, "rowcount", 0) or 0) > 0:
                    claimed.append(dict(item))
        statuses = [row["status"] for row in db.execute(
            "SELECT status FROM batch_items WHERE batch_id = ?", (batch_id,)).fetchall()]
        planned_status = batch_rules.aggregate_status(
            statuses + ["QUEUED"] * len(claimed), paused=paused, cancelled=cancelled)
        db.execute("UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
                   (planned_status, utc_now(), batch_id))

    started: list[dict] = []
    for item in claimed:
        try:
            with connect() as item_db:
                begin_immediate(item_db)
                fresh_batch = item_db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
                if fresh_batch is None:
                    raise RuntimeError("批次已不存在")
                if fresh_batch["paused"] or fresh_batch["cancelled"]:
                    item_db.execute(
                        "UPDATE batch_items SET status = 'PENDING', updated_at = ? WHERE id = ?",
                        (utc_now(), item["id"]))
                    continue
                created = _existing_or_new_item_run(item_db, fresh_batch, item)
                item_db.execute(
                    "UPDATE batch_items SET status = 'QUEUED', run_id = ?, job_id = ?, error = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (created["run_id"], created["job_id"], utc_now(), item["id"]),
                )
            started.append({"item_id": item["id"], "index": item["item_index"], **created})
        except HTTPException as exc:
            with connect() as fail_db:
                fail_db.execute(
                    "UPDATE batch_items SET status = 'FAILED', error = ?, updated_at = ? WHERE id = ?",
                    (f"调度失败: {exc.detail}", utc_now(), item["id"]))
        except Exception as exc:  # 并发/唯一键/上游异常：释放认领，下一轮重试（不影响其他项）
            with connect() as retry_db:
                retry_db.execute(
                    "UPDATE batch_items SET status = 'PENDING', error = ?, updated_at = ? WHERE id = ?",
                    (f"调度异常（已释放认领，稍后重试）: {exc}", utc_now(), item["id"]))

    with connect() as db:
        row = refresh_batch_status(db, batch_id)
        public = _batch_public(db, row)
    for start in started:
        if execute_inline and not INLINE_EXECUTOR_DISABLED:
            execute_job(start["job_id"])
    return {"batch": public, "started": started}


@app.get("/api/v1/batches")
def list_batches(owner_id: str = DEFAULT_OWNER_ID, project_id: str = DEFAULT_PROJECT_ID) -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(owner_id, project_id)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM batches WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC LIMIT 100",
            (owner_id, project_id),
        ).fetchall()
        return [_batch_public(db, row) for row in rows]


@app.get("/api/v1/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        row = refresh_batch_status(db, batch_id)
        return _batch_public(db, row)


@app.get("/api/v1/batches/{batch_id}/items")
def list_batch_items(batch_id: str, cursor: int = 0, limit: int = 50, status: str = "") -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    limit = max(1, min(200, limit))
    with connect() as db:
        row = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        refresh_batch_status(db, batch_id)
        query = "SELECT * FROM batch_items WHERE batch_id = ? AND item_index >= ?"
        params: list = [batch_id, cursor]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY item_index ASC LIMIT ?"
        params.append(limit + 1)
        items = db.execute(query, tuple(params)).fetchall()
        next_cursor = items[limit]["item_index"] if len(items) > limit else None
        items = items[:limit]
        summary = batch_rules.summarize([
            dict(item) for item in db.execute(
                "SELECT * FROM batch_items WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        ])
    return {
        "batch_id": batch_id,
        "items": [_batch_item_public(item) for item in items],
        "next_cursor": next_cursor,
        "summary": summary,
        "note": "total 为服务端聚合，不从本页行数推算",
    }


@app.post("/api/v1/batches/{batch_id}/pause")
def pause_batch(batch_id: str, request: BatchActionRequest) -> dict:
    """暂停只停新调度：已在跑的任务继续（取消需显式调用 cancel）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        db.execute("UPDATE batches SET paused = 1, revision = revision + 1, status = 'PAUSED', updated_at = ? "
                   "WHERE id = ?", (utc_now(), batch_id))
        updated = db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        public = _batch_public(db, updated)
    return {"batch": public, "reason": request.reason,
            "note": "暂停只停新调度；已运行的任务保持运行，取消请显式调用 cancel"}


@app.post("/api/v1/batches/{batch_id}/resume")
def resume_batch(batch_id: str, request: BatchActionRequest, background: BackgroundTasks) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        db.execute("UPDATE batches SET paused = 0, revision = revision + 1, updated_at = ? WHERE id = ?",
                   (utc_now(), batch_id))
    if not INLINE_EXECUTOR_DISABLED:
        background.add_task(advance_batch, batch_id)
    return {"batch": get_batch(batch_id), "reason": request.reason}


@app.post("/api/v1/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, request: BatchActionRequest) -> dict:
    """取消可选范围：只取消未开始的项（not_started）或全部未完成项（all_unfinished）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    cancelled_items: list[dict] = []
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        items = db.execute(
            "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY item_index ASC", (batch_id,),
        ).fetchall()
        for item in items:
            target = item["status"] == "PENDING" or (
                request.scope == "all_unfinished" and item["status"] in ("QUEUED", "RUNNING")
            )
            if not target:
                continue
            db.execute("UPDATE batch_items SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
                       (utc_now(), item["id"]))
            cancelled_items.append({"item_id": item["id"], "index": item["item_index"],
                                    "run_id": item["run_id"]})
            if item["job_id"] and item["status"] in ("QUEUED", "RUNNING"):
                db.execute("UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                           (utc_now(), item["job_id"]))
        full = request.scope == "all_unfinished"
        db.execute("UPDATE batches SET cancelled = ?, paused = 0, revision = revision + 1, updated_at = ? WHERE id = ?",
                   (1 if full else 0, utc_now(), batch_id))
        remaining = db.execute(
            "SELECT status FROM batch_items WHERE batch_id = ? ORDER BY item_index ASC", (batch_id,),
        ).fetchall()
        new_status = batch_rules.aggregate_status([item["status"] for item in remaining],
                                                  cancelled=full)
        db.execute("UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
                   (new_status, utc_now(), batch_id))
        updated = db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        public = _batch_public(db, updated)
    return {"batch": public, "scope": request.scope, "cancelled": cancelled_items,
            "reason": request.reason,
            "note": "取消只影响所选范围的项；已成功的产物保留"}


@app.post("/api/v1/batches/{batch_id}/retry-failed")
def retry_failed_batch_items(batch_id: str, request: BatchActionRequest, background: BackgroundTasks) -> dict:
    """重试只针对失败/已取消项；已成功产物不会被重跑。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        items = [dict(item) for item in db.execute(
            "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY item_index ASC", (batch_id,),
        ).fetchall()]
        targets = batch_rules.select_retry_targets(items, item_filter=request.item_filter)
        for item in targets:
            db.execute(
                "UPDATE batch_items SET status = 'PENDING', run_id = NULL, job_id = NULL, error = NULL, "
                "updated_at = ? WHERE id = ?", (utc_now(), item["id"]),
            )
        db.execute("UPDATE batches SET paused = 0, cancelled = 0, revision = revision + 1, updated_at = ? WHERE id = ?",
                   (utc_now(), batch_id))
        retried = [{"item_id": item["id"], "index": item["item_index"], "previous_status": item["status"]}
                   for item in targets]
        public = _batch_public(db, db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone())
    if retried and not INLINE_EXECUTOR_DISABLED:
        background.add_task(advance_batch, batch_id)
    return {"batch": public, "retried": retried,
            "skipped_succeeded": [item["item_index"] for item in items if item["status"] == "SUCCEEDED"],
            "reason": request.reason,
            "note": "只重置失败/取消项；已成功项保持不动（复用需输入快照/能力/许可完全相符）"}


# ---------------------------------------------------------------------------
# V6-05：发布包（build → verify → approve，审批后不可变）
# ---------------------------------------------------------------------------


class PackageBuildRequest(BaseModel):
    profile_id: str = Field(default="", max_length=80)
    localization_id: str = Field(default="", max_length=80)
    subtitle_track_id: str = Field(default="", max_length=80)
    audio_mix_id: str = Field(default="", max_length=80)
    final_rendition_id: str = Field(default="", max_length=80)
    clean_master_rendition_id: str = Field(default="", max_length=80)
    thumbnail_rendition_id: str = Field(default="", max_length=80)
    include_voice_file: bool = False
    include_mixed_audio: bool = True
    batch_id: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=2000)


class PackageApproveRequest(BaseModel):
    content_hash: str = Field(min_length=8, max_length=128)
    reason: str = Field(default="", max_length=500)


def _latest_row(db, table: str, where: str, params: tuple, order: str = "created_at DESC"):
    return db.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order} LIMIT 1", params).fetchone()


def _run_qa_summary(db, run_id: str, job_id: str) -> dict | None:
    """QA 来源如实标注：优先严格 QA 报告，其次运行 manifest 的媒体质量门。"""
    row = _latest_row(db, "qa_reports", "run_id = ?", (run_id,))
    if row:
        payload = json.loads(row["payload"])
        return {
            "source": "strict_qa_report",
            "passed": bool(payload.get("passed")),
            "blocked": bool(payload.get("blocked", not payload.get("passed"))),
            "report_id": row["id"],
            "report_sha256": row["payload_sha256"],
            "approval_ref": {"kind": "qa_report", "id": row["id"], "sha256": row["payload_sha256"]},
        }
    manifest_path = RUNS / job_id / "metadata.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        qa = manifest.get("qa") or {}
        if qa:
            return {
                "source": "run_manifest_media_gate",
                "passed": bool(qa.get("passed")),
                "blocked": not bool(qa.get("passed")),
                "failures": qa.get("failures", []),
                "approval_ref": {"kind": "run_manifest", "id": job_id,
                                 "sha256": _file_sha256(manifest_path)},
                "note": "媒体质量门（黑帧/可见性）结果；完整保真 QA 属严格管线",
            }
    return None


@app.post("/api/v1/runs/{run_id}/packages", status_code=201)
def build_publish_package(run_id: str, request: PackageBuildRequest) -> dict:
    """构建发布包：先 build → verify，通过后才可 approve（审批后不可变）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        run = _resolve_run_scope(db, run_id)
        if run["owner_id"] != owner_id or run["project_id"] != project_id:
            raise HTTPException(403, "越权访问 Run")
        final = None
        if request.final_rendition_id:
            final = db.execute("SELECT * FROM output_renditions WHERE id = ? AND run_id = ? AND kind = 'final'",
                               (request.final_rendition_id, run_id)).fetchone()
        else:
            final = _latest_row(db, "output_renditions", "run_id = ? AND kind = 'final'", (run_id,))
        if not final:
            raise HTTPException(409, "缺少成片（final rendition）：请先编码成片再打包")
        clean = None
        if request.clean_master_rendition_id:
            clean = db.execute("SELECT * FROM output_renditions WHERE id = ? AND run_id = ?",
                               (request.clean_master_rendition_id, run_id)).fetchone()
        else:
            clean = _latest_row(db, "output_renditions", "run_id = ? AND kind = 'clean_master'", (run_id,))
        thumbnail = _latest_row(db, "output_renditions", "run_id = ? AND kind = 'thumbnail'", (run_id,))
        localization = None
        if request.localization_id:
            localization = db.execute(
                "SELECT * FROM localizations WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.localization_id, owner_id, project_id),
            ).fetchone()
        else:
            localization = _latest_row(db, "localizations", "run_id = ? AND owner_id = ? AND project_id = ?",
                                       (run_id, owner_id, project_id), order="created_at DESC")
        if not localization:
            raise HTTPException(409, "缺少本地化文案：请先创建 localizations 再打包")
        revision = _latest_localization_revision(db, localization["id"])
        copy_payload = json.loads(revision["payload"])
        if request.subtitle_track_id:
            subtitle = db.execute("SELECT * FROM subtitle_tracks WHERE id = ? AND localization_id = ?",
                                  (request.subtitle_track_id, localization["id"])).fetchone()
        else:
            subtitle = _latest_row(db, "subtitle_tracks", "localization_id = ?", (localization["id"],))
        mix = (db.execute("SELECT * FROM audio_mixes WHERE id = ? AND run_id = ?",
                          (request.audio_mix_id, run_id)).fetchone() if request.audio_mix_id
               else _latest_row(db, "audio_mixes", "run_id = ?", (run_id,)))
        profile = None
        if request.profile_id:
            profile = _profile_for_request(db, request.profile_id, owner_id, project_id)
        if profile is None and localization["profile_id"]:
            profile = _profile_for_request(db, localization["profile_id"], owner_id, project_id)
        qa = _run_qa_summary(db, run_id, run["job_id"])
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (run["job_id"],)).fetchone()
        existing_versions = db.execute(
            "SELECT COUNT(*) AS n FROM publish_packages WHERE run_id = ? AND locale = ?",
            (run_id, localization["locale"]),
        ).fetchone()["n"]
    if qa is None:
        raise HTTPException(409, "缺少 QA 结果：没有质量门结果的成片不能打包")
    rendition_payload = json.loads(final["payload"])
    subtitle_payload = json.loads(subtitle["payload"]) if subtitle else None
    profile_spec = profile["spec"] if profile else None
    output_info = {
        "width": rendition_payload["spec"]["width"], "height": rendition_payload["spec"]["height"],
        "fps": rendition_payload["spec"]["fps"], "duration_s": final and rendition_payload["report"]["duration_s"],
        "frame_count": int(rendition_payload["report"].get("nb_frames") or 0),
    }
    aspect = None
    if output_info["width"] and output_info["height"]:
        for name, (w, h) in (("9:16", (9, 16)), ("1:1", (1, 1)), ("2:3", (2, 3))):
            if abs(output_info["width"] / output_info["height"] - w / h) <= 0.005 * (w / h):
                aspect = name
                break
    subtitle_state = (
        {"enabled": True, "alignment": subtitle_payload["alignment"], "locale": subtitle["locale"],
         "files": subtitle_payload["files"], "cue_count": subtitle_payload["cue_count"]}
        if subtitle else publish_package.disabled_state(False, "未生成字幕轨（显式关闭）")
    )
    voice_state = publish_package.disabled_state(False, "未包含配音原文件（许可策略默认不分发原始音频）")
    music_state = publish_package.disabled_state(False, "未包含 BGM 原文件（许可策略不允许单独分发音乐）")
    if mix:
        mix_payload = json.loads(mix["payload"])
        voice_state = {"enabled": mix_payload["voice"]["enabled"],
                       "status": "mixed_only" if mix_payload["voice"]["enabled"] else "disabled",
                       "reason": "配音已进入混音轨；原始配音文件默认不入包",
                       "duration_s": mix_payload["duration_s"]}
        music_state = {
            "enabled": mix_payload["music"]["enabled"],
            "status": "licensed_reference_only" if mix_payload["music"]["enabled"] else "disabled",
            "reason": "只保留许可引用与混音记录",
            "license_ref": mix_payload.get("music_license_ref"),
            "commercial_use_allowed": mix_payload.get("music_commercial_use_allowed"),
        }
    problems = publish_package.validate_package_request(
        profile_spec={"aspect_ratio": profile_spec.composition.aspect_ratio} if profile_spec else {},
        output={"aspect_ratio": aspect}, qa={"passed": qa["passed"], "approval_ref": qa["approval_ref"]},
        subtitle_state=subtitle_state,
    )
    if problems:
        raise HTTPException(422, {"code": "package_preflight_failed", "detail": problems})
    package_id = str(uuid.uuid4())
    version = int(existing_versions) + 1
    directory_name = f"publish-package_{package_id}_v{version}"
    directory = VAR / "packages" / directory_name
    (directory / "video").mkdir(parents=True, exist_ok=True)
    (directory / "subtitles").mkdir(parents=True, exist_ok=True)
    (directory / "audio").mkdir(parents=True, exist_ok=True)
    (directory / "images").mkdir(parents=True, exist_ok=True)
    (directory / "copy").mkdir(parents=True, exist_ok=True)
    (directory / "metadata").mkdir(parents=True, exist_ok=True)
    (directory / "publish").mkdir(parents=True, exist_ok=True)
    files: list[dict] = []

    def place(source: Path, relative: str, role: str) -> None:
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        files.append(publish_package.file_entry(target, role=role,
                                                arcname=f"{directory_name}/{relative}"))

    place(Path(final["path"]), "video/final.mp4", "video")
    if clean and clean["path"] != final["path"]:
        place(Path(clean["path"]), "video/clean_master.mp4", "clean_master")
    if subtitle_payload:
        for item in subtitle_payload["files"]:
            place(Path(item["path"]), f"subtitles/{subtitle['locale']}.{item['format']}", "subtitle")
    if mix and request.include_mixed_audio:
        place(Path(mix["path"]), "audio/mixed.wav", "audio")
    if request.include_voice_file:
        voiceover = _latest_row(db, "voiceovers", "run_id = ?", (run_id,))
        if voiceover and voiceover["license_ref"]:
            place(Path(voiceover["path"]), "audio/voice.wav", "audio")
        else:
            request.include_voice_file = False
    if thumbnail:
        place(Path(thumbnail["path"]), "images/thumbnail.jpg", "image")
    caption_text = "\n\n".join(filter(None, [copy_payload.get("headline"), copy_payload.get("body"),
                                             copy_payload.get("cta")]))
    (directory / "copy" / "caption.txt").write_text(caption_text + "\n", encoding="utf-8")
    (directory / "copy" / "hashtags.txt").write_text(
        " ".join(copy_payload.get("hashtags", [])) + "\n", encoding="utf-8")
    (directory / "copy" / "product_facts.json").write_text(
        json.dumps({"facts": copy_payload.get("facts", {}),
                    "generated": copy_payload.get("generated", {}),
                    "note": "事实字段来自批准数据；生成式文案单独标注"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    for relative, role in (("copy/caption.txt", "copy"), ("copy/hashtags.txt", "copy"),
                           ("copy/product_facts.json", "copy")):
        files.append(publish_package.file_entry(directory / relative, role=role,
                                                arcname=f"{directory_name}/{relative}"))
    profile_doc = {
        "profile_id": request.profile_id or (localization["profile_id"] or ""),
        "profile_version": profile["version"]["version"] if profile else None,
        "payload_sha256": profile["version"]["payload_sha256"] if profile else None,
        "spec": profile_spec.model_dump() if profile_spec else None,
        "rules_status": platform_profiles.rules_status(profile_spec) if profile_spec else None,
        "note": "Profile 是产品预设；不代表账号存在或已核验平台规则",
    }
    (directory / "metadata" / "platform_profile.json").write_text(
        json.dumps(profile_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    lineage = {
        "schema_version": "1.0", "run_id": run_id, "job_id": run["job_id"], "plan_id": run["plan_id"],
        "product_version_id": rendition_payload.get("product_version_id"),
        "video_source_sha256": rendition_payload.get("video_source_sha256"),
        "audio_mix_sha256": rendition_payload.get("audio_mix_sha256"),
        "final_sha256": rendition_payload.get("sha256"),
        "clean_master_sha256": json.loads(clean["payload"]).get("sha256") if clean else None,
        "subtitle_track_id": subtitle["id"] if subtitle else None,
        "localization_id": localization["id"], "localization_revision": revision["revision"],
        "profile": {"profile_id": request.profile_id or localization["profile_id"],
                    "profile_version": profile["version"]["version"] if profile else None},
        "note": "保留渲染 Master → 混音 → 编码成片 → 发布包 的来源链",
    }
    (directory / "metadata" / "lineage.json").write_text(
        json.dumps(lineage, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "metadata" / "qa_report.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    rights = publish_package.rights_manifest(
        profile={"platform": profile_spec.identity.platform if profile_spec else "",
                 "rules_source_url": profile_spec.rules.rules_source_url if profile_spec else "",
                 "rules_verified_at": profile_spec.rules.rules_verified_at if profile_spec else ""},
        music=music_state, voice=voice_state,
        fonts=[{"font_ref": profile_spec.subtitles.font_ref,
                "license": profile_spec.subtitles.font_license}] if profile_spec else [],
        materials=[{"role": "video", "source": "本产品真实渲染/合成链路"},
                   {"role": "music", "included": False, "license_ref": music_state.get("license_ref")}],
    )
    (directory / "metadata" / "rights_manifest.json").write_text(
        json.dumps(rights, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "metadata" / "cost_summary.json").write_text(
        json.dumps({"note": "成本账本属于 V6-06；此处不编造数字"}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    for relative, role in (("metadata/platform_profile.json", "metadata"),
                           ("metadata/lineage.json", "metadata"),
                           ("metadata/qa_report.json", "metadata"),
                           ("metadata/rights_manifest.json", "metadata"),
                           ("metadata/cost_summary.json", "metadata")):
        files.append(publish_package.file_entry(directory / relative, role=role,
                                                arcname=f"{directory_name}/{relative}"))
    template = publish_package.publish_request_template(
        package_id=package_id, package_version=version,
        platform=profile_spec.identity.platform if profile_spec else "",
        locale=localization["locale"],
        output_target=profile_spec.publish.output_target if profile_spec else "feed",
        suggested_visibility=profile_spec.publish.suggested_visibility if profile_spec else "private",
        disclosure_flags=profile_spec.publish.disclosure_flags if profile_spec else [],
    )
    (directory / "publish" / "request.template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "publish" / "README.md").write_text(
        publish_package.publish_readme(
            profile_spec.identity.platform if profile_spec else "", localization["locale"],
            profile_spec.publish.suggested_visibility if profile_spec else "private"),
        encoding="utf-8")
    for relative, role in (("publish/request.template.json", "publish_template"),
                           ("publish/README.md", "publish_template")):
        files.append(publish_package.file_entry(directory / relative, role=role,
                                                arcname=f"{directory_name}/{relative}"))
    manifest = publish_package.build_manifest(
        package_id=package_id, version=version, project_id=project_id,
        product_version_id=rendition_payload.get("product_version_id"),
        plan_id=run["plan_id"], profile_id=request.profile_id or (localization["profile_id"] or ""),
        profile_version=profile["version"]["version"] if profile else 0,
        locale=localization["locale"], batch_id=request.batch_id or None, run_id=run_id,
        files=files,
        duration_s=float(output_info["duration_s"] or 0), fps=int(output_info["fps"] or 24),
        resolution=f"{output_info['width']}x{output_info['height']}",
        qa={"source": qa["source"], "passed": qa["passed"],
            "approval_ref": qa["approval_ref"], "note": qa.get("note", "")},
        approval_ref={"kind": "package_content_hash", "content_hash": publish_package.package_content_hash(files)},
        rights_refs={"rights_manifest": f"{directory_name}/metadata/rights_manifest.json",
                     "music_license_ref": music_state.get("license_ref")},
        warnings=([] if subtitle_payload else ["字幕未启用：清单标注 disabled，不代表已生成"]),
        subtitles_state=subtitle_state, voice_state=voice_state, music_state=music_state,
        publish_template_ref=f"{directory_name}/publish/request.template.json",
        created_at=utc_now(),
    )
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 以包目录的父目录为根校验：manifest 内的路径自带 publish-package_<id>_v<n>/ 前缀
    verification = publish_package.verify_package(directory.parent, manifest)
    content_hash = publish_package.package_content_hash(files)
    zip_info = publish_package.zip_package(directory, VAR / "package-zips" / f"{directory_name}.zip")
    now = utc_now()
    payload = {
        "manifest": manifest, "verification": verification, "zip": zip_info,
        "content_hash": content_hash, "include_voice_file": request.include_voice_file,
        "include_mixed_audio": request.include_mixed_audio, "notes": request.notes,
        "built_from": {
            "final_rendition_id": final["id"], "clean_master_rendition_id": clean["id"] if clean else None,
            "thumbnail_rendition_id": thumbnail["id"] if thumbnail else None,
            "audio_mix_id": mix["id"] if mix else None,
            "subtitle_track_id": subtitle["id"] if subtitle else None,
            "localization_revision_id": revision["id"],
        },
    }
    payload_text = _canonical_json_text(payload)
    with connect() as db:
        db.execute(
            "INSERT INTO publish_packages(id, run_id, batch_id, owner_id, project_id, profile_id, locale, version, "
            "status, content_hash, directory, zip_path, zip_sha256, approved_at, payload, payload_sha256, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
            (package_id, run_id, request.batch_id or None, owner_id, project_id,
             request.profile_id or (localization["profile_id"] or None), localization["locale"], version,
             "BUILT" if not verification else "INVALID", content_hash, str(directory),
             zip_info["path"], zip_info["sha256"], payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), now, now),
        )
    return {
        "id": package_id, "status": "BUILT" if not verification else "INVALID",
        "content_hash": content_hash, "verification": verification, "manifest": manifest,
        "zip": zip_info, "directory": str(directory),
        "download_url": f"/api/v1/packages/{package_id}/download",
        "next": "verify → approve：审批绑定 content_hash，审批后不可变",
    }


@app.get("/api/v1/packages")
def list_publish_packages(run_id: str = "", batch_id: str = "") -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        query = "SELECT * FROM publish_packages WHERE owner_id = ? AND project_id = ?"
        params: list = [owner_id, project_id]
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)
        query += " ORDER BY created_at DESC LIMIT 200"
        rows = db.execute(query, tuple(params)).fetchall()
    return [{
        "id": row["id"], "run_id": row["run_id"], "batch_id": row["batch_id"],
        "locale": row["locale"], "profile_id": row["profile_id"], "version": row["version"],
        "status": row["status"], "content_hash": row["content_hash"],
        "zip_sha256": row["zip_sha256"], "approved_at": row["approved_at"],
        "created_at": row["created_at"], "download_url": f"/api/v1/packages/{row['id']}/download",
    } for row in rows]


@app.get("/api/v1/packages/{package_id}")
def get_publish_package(package_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM publish_packages WHERE id = ? AND owner_id = ? AND project_id = ?",
            (package_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "发布包不存在")
    payload = json.loads(row["payload"])
    return {
        "id": row["id"], "run_id": row["run_id"], "batch_id": row["batch_id"], "locale": row["locale"],
        "profile_id": row["profile_id"], "version": row["version"], "status": row["status"],
        "content_hash": row["content_hash"], "zip": payload.get("zip"),
        "verification": payload.get("verification"), "manifest": payload.get("manifest"),
        "created_at": row["created_at"], "approved_at": row["approved_at"],
        "download_url": f"/api/v1/packages/{package_id}/download",
    }


@app.post("/api/v1/packages/{package_id}/verify")
def verify_publish_package(package_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM publish_packages WHERE id = ? AND owner_id = ? AND project_id = ?",
            (package_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "发布包不存在")
    payload = json.loads(row["payload"])
    directory = Path(row["directory"])
    if not directory.exists():
        raise HTTPException(409, "包目录已不存在")
    failures = publish_package.verify_package(directory.parent, payload["manifest"])
    recomputed = publish_package.package_content_hash(payload["manifest"]["files"])
    hash_matches = recomputed == row["content_hash"]
    if not hash_matches:
        failures.append("内容哈希与构建时不一致（文件被修改）")
    with connect() as db:
        db.execute("UPDATE publish_packages SET status = ?, updated_at = ? WHERE id = ?",
                   ("BUILT" if not failures else "INVALID", utc_now(), package_id))
    return {"id": package_id, "verified": not failures, "failures": failures,
            "content_hash": recomputed, "stored_hash": row["content_hash"], "hash_matches": hash_matches,
            "status": "BUILT" if not failures else "INVALID"}


@app.post("/api/v1/packages/{package_id}/approve")
def approve_publish_package(package_id: str, request: PackageApproveRequest) -> dict:
    """审批绑定 content_hash；审批后包不可变（任何修改都会让审批失效）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM publish_packages WHERE id = ? AND owner_id = ? AND project_id = ?",
            (package_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "发布包不存在")
        if request.content_hash != row["content_hash"]:
            raise HTTPException(409, {
                "code": "content_hash_mismatch",
                "detail": "审批必须绑定当前包内容哈希；内容已变化，请重新构建或重新校验",
            })
        payload = json.loads(row["payload"])
        directory = Path(row["directory"])
        failures = publish_package.verify_package(directory.parent, payload["manifest"])
        if failures:
            raise HTTPException(409, {"code": "package_invalid", "detail": failures[:5]})
        now = utc_now()
        db.execute("UPDATE publish_packages SET status = 'APPROVED', approved_at = ?, "
                   "content_hash = ?, updated_at = ? WHERE id = ?",
                   (now, request.content_hash, now, package_id))
        # V6-08：审批后包才可发布 → package.ready 与审批同一事务写入 Outbox
        emit_event(
            db, event_type="package.ready", aggregate_type="publish_package", aggregate_id=package_id,
            aggregate_revision=int(row["version"] or 1),
            payload={"package_id": package_id, "run_id": row["run_id"], "batch_id": row["batch_id"],
                     "locale": row["locale"], "profile_id": row["profile_id"], "version": row["version"],
                     "status": "APPROVED", "content_hash": request.content_hash,
                     "reason": request.reason},
            owner_id=row["owner_id"], project_id=row["project_id"],
        )
    return {"id": package_id, "status": "APPROVED", "approved_at": now,
            "content_hash": request.content_hash, "reason": request.reason,
            "note": "审批绑定内容哈希；此后修改任何文件都会使审批失效"}


@app.get("/api/v1/packages/{package_id}/download")
def download_publish_package(package_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM publish_packages WHERE id = ? AND owner_id = ? AND project_id = ?",
            (package_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "发布包不存在")
    path = Path(row["zip_path"])
    if not path.exists():
        raise HTTPException(404, "包 ZIP 已不存在，请重新构建")
    digest = _file_sha256(path)
    if digest != row["zip_sha256"]:
        raise HTTPException(409, {
            "code": "zip_hash_mismatch",
            "detail": "ZIP 内容哈希与记录不一致：包已被修改，请重新构建",
        })
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/api/v1/batches/{batch_id}/packages/archive", status_code=201)
def build_batch_archive(batch_id: str) -> dict:
    """批次聚合 ZIP：只收集已审批的包（未审批内容不进入可发布归档）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        batch = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        packages = db.execute(
            "SELECT * FROM publish_packages WHERE batch_id = ? AND owner_id = ? AND project_id = ? "
            "ORDER BY created_at ASC", (batch_id, owner_id, project_id),
        ).fetchall()
    approved = [row for row in packages if row["status"] == "APPROVED"]
    if not approved:
        raise HTTPException(409, {
            "code": "no_approved_packages",
            "detail": "批次没有已审批的发布包：请先 build → verify → approve，未审批内容不进入归档",
        })
    archive_dir = VAR / "package-zips" / f"batch-{batch_id}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    members = []
    for row in approved:
        zip_path = Path(row["zip_path"])
        if not zip_path.exists():
            continue
        target = archive_dir / zip_path.name
        target.write_bytes(zip_path.read_bytes())
        members.append({"package_id": row["id"], "zip": zip_path.name,
                        "sha256": _file_sha256(zip_path), "content_hash": row["content_hash"]})
    archive_path = VAR / "package-zips" / f"batch-{batch_id}-packages.zip"
    info = publish_package.zip_package(archive_dir, archive_path)
    manifest = {
        "schema_version": "1.0", "kind": "batch_package_archive", "batch_id": batch_id,
        "package_count": len(members), "packages": members, "created_at": utc_now(),
        "note": "只包含已审批的发布包；未审批内容不进入可发布归档",
    }
    (archive_dir / "batch-archive-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    info = publish_package.zip_package(archive_dir, archive_path)
    return {"batch_id": batch_id, "package_count": len(members), "archive": info, "manifest": manifest,
            "download_url": f"/api/v1/batches/{batch_id}/packages/archive/download"}


@app.get("/api/v1/batches/{batch_id}/packages/archive/download")
def download_batch_archive(batch_id: str) -> FileResponse:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        batch = db.execute(
            "SELECT * FROM batches WHERE id = ? AND owner_id = ? AND project_id = ?",
            (batch_id, owner_id, project_id),
        ).fetchone()
    if not batch:
        raise HTTPException(404, "批次不存在")
    path = VAR / "package-zips" / f"batch-{batch_id}-packages.zip"
    if not path.exists():
        raise HTTPException(404, "批次归档不存在，请先生成")
    return FileResponse(path, media_type="application/zip", filename=path.name)


# ---------------------------------------------------------------------------
# V6-06 资源与成本账本：用量事件去重、预算预留与对账、四类金额与可用额度
# 诚实边界：预估不是强制封顶；无已知报价不得假设 0；自管资源标注内部估算。
# ---------------------------------------------------------------------------

class UsageEventRequest(BaseModel):
    provider: str
    operation_id: str
    unit: str
    quantity: float
    billing_item: str = "default"
    event_id: str = ""
    capability: str | None = None
    run_id: str | None = None
    batch_id: str | None = None
    budget_id: str | None = None
    explicit_price: float | None = None
    currency: str | None = None
    occurred_at: str | None = None
    note: str = ""


class BudgetCreateRequest(BaseModel):
    name: str
    currency: str = "USD"
    limit_amount: float | None = None
    note: str = ""


class BudgetUpdateRequest(BaseModel):
    revision: int
    limit_amount: float | None = None
    reason: str = ""


class BudgetReserveRequest(BaseModel):
    estimate_upper_bound: float
    run_id: str | None = None
    batch_id: str | None = None
    note: str = ""


class BudgetSettleRequest(BaseModel):
    reservation_id: str
    actual_amount: float
    usage_event_id: str | None = None
    note: str = ""


def _ledger_entry_public(row: sqlite3.Row) -> dict:
    try:
        payload = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "id": row["id"], "budget_id": row["budget_id"], "kind": row["kind"],
        "amount": row["amount"], "currency": row["currency"], "run_id": row["run_id"],
        "batch_id": row["batch_id"], "usage_event_id": row["usage_event_id"],
        "note": row["note"], "created_at": row["created_at"], "refs": payload.get("refs", {}),
        "settled_at": (row["settled_at"] if "settled_at" in row.keys() else None),
        "settled_entry_id": (row["settled_entry_id"] if "settled_entry_id" in row.keys() else None),
    }


def _budget_rows(db: sqlite3.Connection, owner_id: str, project_id: str,
                 budget_id: str | None = None) -> list[sqlite3.Row]:
    if budget_id:
        return db.execute(
            "SELECT * FROM budgets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (budget_id, owner_id, project_id),
        ).fetchall()
    return db.execute(
        "SELECT * FROM budgets WHERE owner_id = ? AND project_id = ? ORDER BY created_at ASC",
        (owner_id, project_id),
    ).fetchall()


def _budget_public(row: sqlite3.Row, entries: list[dict]) -> dict:
    snapshot = ledger.budget_snapshot(entries, row["limit_amount"])
    return {
        "id": row["id"], "name": row["name"], "currency": row["currency"],
        "limit_amount": row["limit_amount"], "revision": row["revision"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "entries": len(entries), **snapshot,
    }


def _insert_ledger_entry(db: sqlite3.Connection, *, owner_id: str, project_id: str, budget_id: str | None,
                         kind: str, amount: float, currency: str, run_id: str | None = None,
                         batch_id: str | None = None, usage_event_id: str | None = None,
                         note: str = "", refs: dict | None = None) -> str:
    entry = ledger.ledger_entry(kind=kind, amount=amount, currency=currency, refs=refs or {}, note=note)
    entry_id = str(uuid.uuid4())
    key_identity = security.current_automation_key.get()
    actor_key_id = key_identity["key_id"] if key_identity else None
    payload_text = _canonical_json_text({
        "kind": entry["kind"], "amount": entry["amount"], "currency": entry["currency"],
        "refs": entry["refs"], "note": note,
    })
    db.execute(
        "INSERT INTO cost_ledger(id, owner_id, project_id, budget_id, kind, amount, currency, run_id, batch_id, "
        "usage_event_id, note, payload, payload_sha256, created_at, actor_key_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (entry_id, owner_id, project_id, budget_id, entry["kind"], entry["amount"], entry["currency"],
         run_id, batch_id, usage_event_id, note, payload_text,
         hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), utc_now(), actor_key_id),
    )
    return entry_id


def _budget_entries(db: sqlite3.Connection, budget_ids: list[str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {budget_id: [] for budget_id in budget_ids}
    if not budget_ids:
        return grouped
    placeholders = ", ".join("?" for _ in budget_ids)
    rows = db.execute(
        f"SELECT * FROM cost_ledger WHERE budget_id IN ({placeholders}) ORDER BY created_at ASC",
        tuple(budget_ids),
    ).fetchall()
    for row in rows:
        grouped.setdefault(row["budget_id"], []).append(_ledger_entry_public(row))
    return grouped


def record_internal_usage(*, provider: str, unit: str, quantity: float, operation_id: str,
                          capability: str, run_id: str | None = None, batch_id: str | None = None,
                          event_id: str = "", note: str = "") -> dict | None:
    """自管资源用量自动登记：真实生产动作（渲染/合成/合成音频）也进入账本。

    账本失败绝不阻断生产流程，但也绝不伪造金额：无已知报价时 amount 为 None 并给出原因。
    """
    try:
        if quantity is None or float(quantity) <= 0:
            return None
        owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
        billing_item = unit
        dedupe_key = ledger.usage_event_key(
            provider=provider, operation_id=operation_id, billing_item=billing_item,
            event_id=event_id or f"{provider}:{operation_id}",
        )
        with connect() as db:
            existing = db.execute("SELECT * FROM usage_events WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
            if existing:
                return {"id": existing["id"], "deduplicated": True, "amount": existing["amount"]}
            price = ledger.price_for_event(provider, unit, float(quantity))
            currency = price.get("currency") or "USD"
            budget_row = db.execute(
                "SELECT id FROM budgets WHERE owner_id = ? AND project_id = ? ORDER BY created_at ASC LIMIT 1",
                (owner_id, project_id),
            ).fetchone()
            usage_id = str(uuid.uuid4())
            occurred = utc_now()
            payload = {
                "provider": provider, "operation_id": operation_id, "billing_item": billing_item,
                "unit": unit, "quantity": round(float(quantity), 6), "capability": capability,
                "run_id": run_id, "batch_id": batch_id, "amount": price["amount"],
                "amount_source": price["source"], "currency": currency, "note": note,
                "internal_estimate": provider in ledger.INTERNAL_PROVIDERS,
                "collected_by": "system",
            }
            payload_text = _canonical_json_text(payload)
            db.execute(
                "INSERT INTO usage_events(id, owner_id, project_id, provider, operation_id, billing_item, event_id, "
                "dedupe_key, unit, quantity, run_id, batch_id, capability, internal_estimate, amount, amount_source, "
                "currency, occurred_at, payload, payload_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (usage_id, owner_id, project_id, provider, operation_id, billing_item, event_id or operation_id,
                 dedupe_key, unit, round(float(quantity), 6), run_id, batch_id, capability,
                 1 if provider in ledger.INTERNAL_PROVIDERS else 0, price["amount"], price["source"], currency,
                 occurred, payload_text, hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), occurred),
            )
            if price["priced"]:
                _insert_ledger_entry(
                    db, owner_id=owner_id, project_id=project_id,
                    budget_id=budget_row["id"] if budget_row else None, kind="accrued",
                    amount=float(price["amount"]), currency=currency, run_id=run_id, batch_id=batch_id,
                    usage_event_id=usage_id, note=note or "自管资源用量自动登记（内部估算）",
                    refs={"provider": provider, "unit": unit, "quantity": round(float(quantity), 6)},
                )
            return {"id": usage_id, "deduplicated": False, "priced": price["priced"],
                    "amount": price["amount"], "amount_source": price["source"]}
    except Exception as exc:  # 记账失败不影响生产，但会明确打印到 stderr
        print(f"[ledger] 内部用量登记失败 {provider}/{unit}: {exc}", file=sys.stderr)
        return None


@app.get("/api/v1/rate-card")
def get_internal_rate_card() -> dict:
    """内部成本率：仅用于估算，不代表真实云账单；不构成对外报价。"""
    return ledger.internal_rate_card()


@app.post("/api/v1/usage-events", status_code=201)
def register_usage_event(request: UsageEventRequest) -> dict:
    """登记 Provider 用量：按 (provider, operation_id, billing_item, event_id) 去重，重放不重复计费。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if request.unit not in ledger.RESOURCE_UNITS:
        raise HTTPException(422, {"code": "UNKNOWN_UNIT", "detail": f"未知资源单位 {request.unit}",
                                  "units": list(ledger.RESOURCE_UNITS)})
    if request.quantity < 0:
        raise HTTPException(422, {"code": "NEGATIVE_QUANTITY", "detail": "用量不能为负"})
    if request.currency and request.currency not in ledger.CURRENCIES:
        raise HTTPException(422, {"code": "UNKNOWN_CURRENCY", "detail": f"不支持的币种 {request.currency}",
                                  "currencies": list(ledger.CURRENCIES)})
    event_key = ledger.usage_event_key(
        provider=request.provider, operation_id=request.operation_id,
        billing_item=request.billing_item, event_id=request.event_id or f"{request.provider}:{request.operation_id}",
    )
    with connect() as db:
        begin_immediate(db)
        existing = db.execute("SELECT * FROM usage_events WHERE dedupe_key = ?", (event_key,)).fetchone()
        if existing:
            return {
                "id": existing["id"], "deduplicated": True, "provider": existing["provider"],
                "operation_id": existing["operation_id"], "unit": existing["unit"],
                "quantity": existing["quantity"], "amount": existing["amount"],
                "currency": existing["currency"], "occurred_at": existing["occurred_at"],
                "note": "同一 Provider 同一次操作的重复回调不会重复计费（返回已登记事件）",
            }
        price = ledger.price_for_event(request.provider, request.unit, request.quantity,
                                       explicit_price=request.explicit_price)
        currency = request.currency or price.get("currency") or "USD"
        budget_row = None
        if request.budget_id:
            budget_row = db.execute(
                "SELECT * FROM budgets WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.budget_id, owner_id, project_id),
            ).fetchone()
            if not budget_row:
                raise HTTPException(404, "预算账户不存在")
        else:
            budget_row = db.execute(
                "SELECT * FROM budgets WHERE owner_id = ? AND project_id = ? ORDER BY created_at ASC LIMIT 1",
                (owner_id, project_id),
            ).fetchone()
        usage_id = str(uuid.uuid4())
        occurred = request.occurred_at or utc_now()
        payload = {
            "provider": request.provider, "operation_id": request.operation_id,
            "billing_item": request.billing_item, "unit": request.unit,
            "quantity": round(float(request.quantity), 6), "capability": request.capability,
            "run_id": request.run_id, "batch_id": request.batch_id, "amount": price["amount"],
            "amount_source": price["source"], "currency": currency, "note": request.note,
            "internal_estimate": request.provider in ledger.INTERNAL_PROVIDERS,
            "collected_by": "api",
        }
        payload_text = _canonical_json_text(payload)
        db.execute(
            "INSERT INTO usage_events(id, owner_id, project_id, provider, operation_id, billing_item, event_id, "
            "dedupe_key, unit, quantity, run_id, batch_id, capability, internal_estimate, amount, amount_source, "
            "currency, occurred_at, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (usage_id, owner_id, project_id, request.provider, request.operation_id, request.billing_item,
             request.event_id or request.operation_id, event_key, request.unit, round(float(request.quantity), 6),
             request.run_id, request.batch_id, request.capability,
             1 if request.provider in ledger.INTERNAL_PROVIDERS else 0,
             price["amount"], price["source"], currency, occurred, payload_text,
             hashlib.sha256(payload_text.encode("utf-8")).hexdigest(), utc_now()),
        )
        ledger_entry_id = None
        if price["priced"]:
            ledger_entry_id = _insert_ledger_entry(
                db, owner_id=owner_id, project_id=project_id,
                budget_id=budget_row["id"] if budget_row else None, kind="accrued",
                amount=float(price["amount"]), currency=currency, run_id=request.run_id,
                batch_id=request.batch_id, usage_event_id=usage_id,
                note=request.note or f"{request.provider} 用量登记（{price['source']}）",
                refs={"provider": request.provider, "unit": request.unit,
                      "quantity": round(float(request.quantity), 6)},
            )
    return {
        "id": usage_id, "deduplicated": False, "provider": request.provider,
        "operation_id": request.operation_id, "billing_item": request.billing_item, "unit": request.unit,
        "quantity": round(float(request.quantity), 6), "amount": price["amount"], "currency": currency,
        "amount_source": price["source"], "priced": price["priced"],
        "internal_estimate": request.provider in ledger.INTERNAL_PROVIDERS,
        "budget_id": budget_row["id"] if budget_row else None,
        "ledger_entry_id": ledger_entry_id,
        "unpriced_reason": None if price["priced"] else price.get("reason"),
        "occurred_at": occurred,
    }


@app.get("/api/v1/usage")
def list_usage(run_id: str = "", batch_id: str = "", provider: str = "", unit: str = "",
               limit: int = 200) -> dict:
    """用量查询：服务端聚合，UI 不自行相加；未定价事件单独列出，不按 0 计入。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    query = "SELECT * FROM usage_events WHERE owner_id = ? AND project_id = ?"
    params: list = [owner_id, project_id]
    if run_id:
        query += " AND run_id = ?"
        params.append(run_id)
    if batch_id:
        query += " AND batch_id = ?"
        params.append(batch_id)
    if provider:
        query += " AND provider = ?"
        params.append(provider)
    if unit:
        query += " AND unit = ?"
        params.append(unit)
    query += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with connect() as db:
        rows = db.execute(query, tuple(params)).fetchall()
    events = [{
        "id": row["id"], "provider": row["provider"], "operation_id": row["operation_id"],
        "billing_item": row["billing_item"], "unit": row["unit"], "quantity": row["quantity"],
        "run_id": row["run_id"], "batch_id": row["batch_id"], "capability": row["capability"],
        "amount": row["amount"], "amount_source": row["amount_source"], "currency": row["currency"],
        "internal_estimate": bool(row["internal_estimate"]), "occurred_at": row["occurred_at"],
        "unpriced": row["amount"] is None,
    } for row in rows]
    summary = ledger.resource_summary(events)
    priced_total = round(sum(float(e["amount"]) for e in events if e["amount"] is not None), 6)
    return {
        "summary": summary, "events": events,
        "priced_total": priced_total,
        "unpriced_event_ids": [e["id"] for e in events if e["unpriced"]],
        "filters": {"run_id": run_id or None, "batch_id": batch_id or None,
                    "provider": provider or None, "unit": unit or None},
        "note": "未定价事件不按 0 计入金额；自管资源按内部成本率估算并标注 internal_estimate",
    }


@app.get("/api/v1/costs")
def get_costs(budget_id: str = "", run_id: str = "", batch_id: str = "") -> dict:
    """成本视图：预算快照 + 账本明细（四类金额分开显示，reserved 不与 settled 相加）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        budgets = _budget_rows(db, owner_id, project_id, budget_id or None)
        if budget_id and not budgets:
            raise HTTPException(404, "预算账户不存在")
        grouped = _budget_entries(db, [row["id"] for row in budgets])
        entry_query = "SELECT * FROM cost_ledger WHERE owner_id = ? AND project_id = ?"
        entry_params: list = [owner_id, project_id]
        if budget_id:
            entry_query += " AND budget_id = ?"
            entry_params.append(budget_id)
        if run_id:
            entry_query += " AND run_id = ?"
            entry_params.append(run_id)
        if batch_id:
            entry_query += " AND batch_id = ?"
            entry_params.append(batch_id)
        entry_query += " ORDER BY created_at DESC LIMIT 500"
        entry_rows = db.execute(entry_query, tuple(entry_params)).fetchall()
        unattached_rows = db.execute(
            "SELECT * FROM cost_ledger WHERE owner_id = ? AND project_id = ? AND budget_id IS NULL "
            "ORDER BY created_at ASC LIMIT 500",
            (owner_id, project_id),
        ).fetchall()
    unattached = [_ledger_entry_public(row) for row in unattached_rows]
    by_kind: dict[str, float] = {kind: 0.0 for kind in ledger.LEDGER_KINDS}
    by_currency: dict[str, float] = {}
    for row in entry_rows:
        by_kind[row["kind"]] = round(by_kind.get(row["kind"], 0.0) + float(row["amount"]), 6)
        if row["kind"] in ("settled", "accrued"):
            by_currency[row["currency"]] = round(by_currency.get(row["currency"], 0.0) + float(row["amount"]), 6)
    return {
        "budgets": [_budget_public(row, grouped.get(row["id"], [])) for row in budgets],
        "entries": [_ledger_entry_public(row) for row in entry_rows],
        "by_kind": by_kind,
        "actual_by_currency": by_currency,
        "unattached_snapshot": ledger.budget_snapshot(unattached, None),
        "rate_card": ledger.internal_rate_card(),
        "filters": {"budget_id": budget_id or None, "run_id": run_id or None, "batch_id": batch_id or None},
        "note": "actual = settled + accrued_unreserved；reserved 是承诺未对账，单独显示，不与 actual 相加",
    }


@app.get("/api/v1/budgets")
def list_budgets() -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        rows = _budget_rows(db, owner_id, project_id)
        grouped = _budget_entries(db, [row["id"] for row in rows])
    return [_budget_public(row, grouped.get(row["id"], [])) for row in rows]


@app.post("/api/v1/budgets", status_code=201)
def create_budget(request: BudgetCreateRequest) -> dict:
    """建立预算账户。无上限预算不能用于付费路线（无已知报价不得按 0 估算）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if request.currency not in ledger.CURRENCIES:
        raise HTTPException(422, {"code": "UNKNOWN_CURRENCY", "detail": f"不支持的币种 {request.currency}",
                                  "currencies": list(ledger.CURRENCIES)})
    if request.limit_amount is not None and request.limit_amount < 0:
        raise HTTPException(422, {"code": "NEGATIVE_LIMIT", "detail": "预算上限不能为负"})
    budget_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO budgets(id, owner_id, project_id, name, currency, limit_amount, revision, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (budget_id, owner_id, project_id, request.name, request.currency, request.limit_amount, 1, now, now),
        )
    return {
        "id": budget_id, "name": request.name, "currency": request.currency,
        "limit_amount": request.limit_amount, "revision": 1, "created_at": now, "updated_at": now,
        "entries": 0, **ledger.budget_snapshot([], request.limit_amount),
        "note": request.note or "预算上限变更只影响后续提交与预留，不追溯已发生金额",
    }


@app.patch("/api/v1/budgets/{budget_id}")
def update_budget(budget_id: str, request: BudgetUpdateRequest) -> dict:
    """调整预算上限：乐观锁（revision）+ 只影响后续提交；已发生的 settled 不被改写。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if request.limit_amount is not None and request.limit_amount < 0:
        raise HTTPException(422, {"code": "NEGATIVE_LIMIT", "detail": "预算上限不能为负"})
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM budgets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (budget_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "预算账户不存在")
        if int(request.revision) != int(row["revision"]):
            raise HTTPException(409, {
                "code": "revision_conflict",
                "detail": f"revision 不一致（提交 {request.revision}，当前 {row['revision']}）：请重新读取后再改",
                "current_revision": row["revision"],
            })
        now = utc_now()
        revision = int(row["revision"]) + 1
        db.execute("UPDATE budgets SET limit_amount = ?, revision = ?, updated_at = ? WHERE id = ?",
                   (request.limit_amount, revision, now, budget_id))
        grouped = _budget_entries(db, [budget_id])
        updated = db.execute("SELECT * FROM budgets WHERE id = ?", (budget_id,)).fetchone()
    return {
        **_budget_public(updated, grouped.get(budget_id, [])),
        "previous_limit_amount": row["limit_amount"], "reason": request.reason,
        "note": "上限调整只影响后续预留与提交；已确认金额保持不变（不追溯改写历史）",
    }


@app.post("/api/v1/budgets/{budget_id}/reserve")
def reserve_budget(budget_id: str, request: BudgetReserveRequest) -> dict:
    """提交前预留：可用额度不足则阻断（409），不静默降级也不假设 0 成本。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if request.estimate_upper_bound <= 0:
        raise HTTPException(422, {"code": "INVALID_ESTIMATE", "detail": "预估上界必须大于 0"})
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM budgets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (budget_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "预算账户不存在")
        grouped = _budget_entries(db, [budget_id])
        entries = grouped.get(budget_id, [])
        check = ledger.check_budget({"limit_amount": row["limit_amount"]}, entries, request.estimate_upper_bound)
        if not check["allowed"]:
            # 阻断本身没有业务写入，因此事件用独立事务写（不假装与检查原子）
            emit_event_standalone(
                event_type="budget.blocked", aggregate_type="budget", aggregate_id=budget_id,
                aggregate_revision=int(row["revision"] or 1),
                payload={"budget_id": budget_id, "block_code": check["code"], "reason": check["reason"],
                         "estimate_upper_bound": float(request.estimate_upper_bound),
                         "available": (check.get("snapshot") or {}).get("available"),
                         "status": "BLOCKED"},
                owner_id=owner_id, project_id=project_id,
            )
            raise HTTPException(409, {"code": check["code"], "detail": check["reason"],
                                      "snapshot": check.get("snapshot")})
        reservation_id = _insert_ledger_entry(
            db, owner_id=owner_id, project_id=project_id, budget_id=budget_id, kind="reserved",
            amount=float(request.estimate_upper_bound), currency=row["currency"], run_id=request.run_id,
            batch_id=request.batch_id, note=request.note or "提交前预留（预估上界）",
            refs={"estimate_upper_bound": round(float(request.estimate_upper_bound), 6)},
        )
        grouped_after = _budget_entries(db, [budget_id])
    snapshot = ledger.budget_snapshot(grouped_after.get(budget_id, []), row["limit_amount"])
    return {
        "budget_id": budget_id, "reservation_id": reservation_id,
        "reserved_amount": round(float(request.estimate_upper_bound), 6), "currency": row["currency"],
        "snapshot": snapshot,
        "note": "预留不是实际支出；对账后必须按实际金额 settle，超支记入 additional_accrual",
    }


@app.post("/api/v1/budgets/{budget_id}/settle")
def settle_budget(budget_id: str, request: BudgetSettleRequest) -> dict:
    """对账：冲销预留并记入实际金额；实际高于预留时如实报告超支（预估不是封顶）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    if request.actual_amount < 0:
        raise HTTPException(422, {"code": "NEGATIVE_AMOUNT", "detail": "实际金额不能为负"})
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM budgets WHERE id = ? AND owner_id = ? AND project_id = ?",
            (budget_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "预算账户不存在")
        reservation = db.execute(
            "SELECT * FROM cost_ledger WHERE id = ? AND budget_id = ? AND kind = 'reserved'",
            (request.reservation_id, budget_id),
        ).fetchone()
        if not reservation:
            raise HTTPException(404, {"code": "reservation_not_found",
                                      "detail": f"预留记录 {request.reservation_id} 不存在于该预算"})
        reserved_amount = float(reservation["amount"])
        if reservation["settled_at"]:
            raise HTTPException(409, {
                "code": "reservation_settled",
                "detail": f"该预留已于 {reservation['settled_at']} 对账，不能重复结算（避免双计成本）",
                "settled_entry_id": reservation["settled_entry_id"],
            })
        if reserved_amount <= 0:
            raise HTTPException(409, {"code": "reservation_settled",
                                      "detail": "该预留金额为 0 或已冲销，不能重复结算"})
        settle = ledger.settle_reservation(reserved_amount=reserved_amount,
                                          actual_amount=float(request.actual_amount))
        _insert_ledger_entry(
            db, owner_id=owner_id, project_id=project_id, budget_id=budget_id, kind="reserved",
            amount=-reserved_amount, currency=row["currency"], run_id=reservation["run_id"],
            batch_id=reservation["batch_id"], note=f"预留对账冲销 {request.reservation_id}",
            refs={"reversal_of": request.reservation_id},
        )
        settled_id = _insert_ledger_entry(
            db, owner_id=owner_id, project_id=project_id, budget_id=budget_id, kind="settled",
            amount=float(request.actual_amount), currency=row["currency"], run_id=reservation["run_id"],
            batch_id=reservation["batch_id"], usage_event_id=request.usage_event_id,
            note=request.note or "对账确认实际金额",
            refs={"reservation_id": request.reservation_id, "overrun": settle["overrun"]},
        )
        db.execute("UPDATE cost_ledger SET settled_at = ?, settled_entry_id = ? WHERE id = ?",
                   (utc_now(), settled_id, request.reservation_id))
        grouped_after = _budget_entries(db, [budget_id])
    snapshot = ledger.budget_snapshot(grouped_after.get(budget_id, []), row["limit_amount"])
    return {
        "budget_id": budget_id, "reservation_id": request.reservation_id, "settled_entry_id": settled_id,
        "currency": row["currency"], **settle, "snapshot": snapshot,
        "note": "取消不等于无费用：已提交的调用仍需按实际金额对账",
    }


# ---------------------------------------------------------------------------
# V6-07 Automation API：Key（scope/限额/IP/周期预算）、幂等、限流、外部调用面
# ---------------------------------------------------------------------------

class AutomationKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list, max_length=len(automation.SCOPES))
    workspace_id: str | None = None
    project_id: str | None = None
    ip_allowlist: str = Field(default="", max_length=200)
    rate_limit_per_minute: int = Field(default=automation.DEFAULT_RATE_LIMIT_PER_MINUTE, ge=1,
                                       le=automation.MAX_RATE_LIMIT_PER_MINUTE)
    budget_limit_amount: float | None = Field(default=None, ge=0)
    budget_period: Literal["minute", "hour", "day", "month"] = "month"
    expires_in_days: int | None = Field(default=None, ge=1, le=automation.KEY_TTL_MAX_DAYS)
    note: str = Field(default="", max_length=500)


class AutomationKeyRevokeRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class AutomationGenerateRequest(BatchCreateRequest):
    """复用批次创建（同一校验/预算/生产服务），只追加自动化意图字段。"""
    auto_publish: bool = False
    budget_id: str | None = None
    publish_intent: dict = Field(default_factory=dict)


def _automation_key_public(row: sqlite3.Row, *, reveal: str | None = None) -> dict:
    return {
        "id": row["id"],
        "key_id": row["key_id"],
        "prefix": row["prefix"],
        "name": row["name"],
        "scopes": [item for item in (row["scopes"] or "").split(",") if item],
        "workspace_id": row["workspace_id"],
        "project_id": row["project_id"],
        "ip_allowlist": row["ip_allowlist"],
        "rate_limit_per_minute": row["rate_limit_per_minute"],
        "budget_limit_amount": row["budget_limit_amount"],
        "budget_period": row["budget_period"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "revoked_reason": row["revoked_reason"],
        "last_used_at": row["last_used_at"],
        "call_count": row["call_count"],
        "fingerprint": row["fingerprint"],
        "created_at": row["created_at"],
        "key": reveal,
        "note": ("密钥只在创建时显示一次；服务端只保存哈希，日志只记录 Key ID"
                 if reveal else "密钥不可再次读取；如遗失请撤销后重建"),
    }


@app.get("/api/v1/automation-keys")
def list_automation_keys() -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM automation_keys WHERE owner_id = ? ORDER BY created_at DESC LIMIT 200",
            (owner_id,),
        ).fetchall()
    return [_automation_key_public(row) for row in rows]


@app.post("/api/v1/automation-keys", status_code=201)
def create_automation_key(request: AutomationKeyCreateRequest) -> dict:
    """创建 Automation Key（Owner 会话专用）。密钥只显示一次，撤销立即生效。"""
    owner_id, default_workspace, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    scopes = [item.strip() for item in request.scopes if item.strip()]
    if not scopes:
        raise HTTPException(422, {"code": "SCOPES_REQUIRED", "detail": f"至少需要一个 scope（可用：{list(automation.SCOPES)}）"})
    unknown = [item for item in scopes if item not in automation.SCOPES]
    if unknown:
        raise HTTPException(422, {"code": "UNKNOWN_SCOPE", "detail": f"未知 scope：{unknown}",
                                  "scopes": list(automation.SCOPES)})
    target_workspace = request.workspace_id or default_workspace
    target_project = request.project_id
    with connect() as db:
        if request.workspace_id:
            workspace = db.execute("SELECT id FROM workspaces WHERE id = ? AND owner_id = ?",
                                   (request.workspace_id, owner_id)).fetchone()
            if not workspace:
                raise HTTPException(404, "workspace 不存在")
        if target_project:
            project = db.execute("SELECT id FROM projects WHERE id = ? AND owner_id = ?",
                                 (target_project, owner_id)).fetchone()
            if not project:
                raise HTTPException(404, "project 不存在")
    try:
        ttl = automation.ttl_seconds(request.expires_in_days)
    except automation.AutomationError as exc:
        raise HTTPException(exc.status_code, exc.as_detail()) from exc
    generated = automation.generate_key()
    key_row_id = str(uuid.uuid4())
    now = utc_now()
    expires_at = (time.time() + ttl) if ttl else None
    with connect() as db:
        db.execute(
            "INSERT INTO automation_keys(id, key_id, prefix, name, owner_id, workspace_id, project_id, scopes, "
            "secret_hash, fingerprint, ip_allowlist, rate_limit_per_minute, budget_limit_amount, budget_period, "
            "expires_at, revoked_at, revoked_reason, last_used_at, call_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', NULL, 0, ?)",
            (key_row_id, generated["key_id"], generated["prefix"], request.name, owner_id, target_workspace,
             target_project, ",".join(scopes), generated["secret_hash"], generated["fingerprint"],
             request.ip_allowlist, request.rate_limit_per_minute, request.budget_limit_amount,
             request.budget_period, expires_at, now),
        )
        row = db.execute("SELECT * FROM automation_keys WHERE id = ?", (key_row_id,)).fetchone()
    payload = _automation_key_public(row, reveal=generated["plaintext"])
    payload["limits"] = automation.limits_document(key_limit=request.rate_limit_per_minute)
    payload["next_steps"] = [
        "用 Authorization: Bearer <key> 调用；创建任务/发布类接口必须带 Idempotency-Key 头",
        "GET /api/v1/automation/openapi 查看该调用面的路由、scope 与限额",
    ]
    return payload


@app.delete("/api/v1/automation-keys/{key_row_id}")
def revoke_automation_key(key_row_id: str, request: AutomationKeyRevokeRequest) -> dict:
    owner_id, _, _ = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute("SELECT * FROM automation_keys WHERE id = ? AND owner_id = ?",
                         (key_row_id, owner_id)).fetchone()
        if not row:
            raise HTTPException(404, "Automation Key 不存在")
        if row["revoked_at"]:
            return {**_automation_key_public(row), "revoked": True,
                    "note": "该 Key 早已撤销：撤销是幂等的，新调用一律 401"}
        now = utc_now()
        db.execute("UPDATE automation_keys SET revoked_at = ?, revoked_reason = ? WHERE id = ?",
                   (now, request.reason, key_row_id))
        updated = db.execute("SELECT * FROM automation_keys WHERE id = ?", (key_row_id,)).fetchone()
    return {**_automation_key_public(updated), "revoked": True, "revoked_by": "owner-session",
            "note": "撤销立即生效：该 Key 的新调用返回 401 key_revoked"}


@app.get("/api/v1/automation/openapi")
def automation_openapi() -> dict:
    """Automation 调用面说明：路由 → scope、强制幂等键、限额（本产品设置，附错误码）。"""
    identity = security.current_automation_key.get()
    return {
        "surface": "/api/v1",
        "auth": "Authorization: Bearer <automation key>；不使用 Cookie，不需要 CSRF 头",
        "scopes": automation.scopes_document(),
        "limits": automation.limits_document(key_limit=(identity or {}).get("rate_limit_per_minute")),
        "idempotency": {
            "header": "Idempotency-Key",
            "scope_isolation": "按 (owner, 调用方, 接口, 键) 隔离；绑定请求规范化哈希",
            "replay": "同键同体重放返回首次结果并带 Idempotency-Replayed: true",
            "conflict": "同键不同体 → 409 idempotency_conflict",
        },
        "automation_only_routes": [
            {"method": "POST", "path": "/api/v1/automation/generate", "scope": "generate:write",
             "note": "复用批次创建（同一校验/预算/生产服务）"},
            {"method": "GET", "path": "/api/v1/automation/batches/{id}", "scope": "jobs:read"},
            {"method": "GET", "path": "/api/v1/automation/jobs/{job_id}", "scope": "jobs:read",
             "note": "生产状态与发布状态分开返回"},
        ],
        "error_codes": {
            "401": ["key_revoked", "key_expired", "Key 无效"],
            "403": ["insufficient_scope", "ip_not_allowed", "owner_session_required"],
            "409": ["idempotency_conflict", "key_budget_exhausted"],
            "428": ["idempotency_key_required"],
            "429": ["rate_limited"],
        },
        "note": "未登记的路由不会对 Automation Key 放行；Key 管理接口只允许 Owner 会话",
    }


@app.post("/api/v1/automation/generate", status_code=202)
def automation_generate(request: AutomationGenerateRequest, background: BackgroundTasks, response: Response,
                        http_request: Request) -> dict:
    """外部系统创建生产批次：与 UI 同一实现；auto_publish 只是意图，不跳过授权与审批。"""
    identity = security.current_automation_key.get() or {}
    if identity.get("project_id") and identity["project_id"] != DEFAULT_PROJECT_ID:
        raise HTTPException(403, {"code": "project_out_of_scope",
                                  "detail": f"该 Key 只允许项目 {identity['project_id']}"})
    intent = batch_rules.normalize_publish_intent({**request.publish_intent, "auto_publish": request.auto_publish})
    if request.auto_publish and not request.budget_id:
        raise HTTPException(422, {
            "code": "BUDGET_REQUIRED_FOR_AUTO_PUBLISH",
            "detail": "auto_publish=true 必须先指定 budget_id（付费/发布路线不接受无预算提交）",
        })
    if request.budget_id:
        owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
        with connect() as db:
            budget = db.execute(
                "SELECT * FROM budgets WHERE id = ? AND owner_id = ? AND project_id = ?",
                (request.budget_id, owner_id, project_id),
            ).fetchone()
        if not budget:
            raise HTTPException(404, "预算账户不存在")
        if budget["limit_amount"] is None:
            raise HTTPException(409, {"code": "no_limit",
                                      "detail": "该预算未设置上限：无已知报价时不得按 0 估算"})
    payload = request.model_copy(update={"publish_intent": intent})
    result = _create_batch_endpoint(payload, background, response, http_request, scope="automation.generate")
    result["automation"] = {
        "actor": current_actor_label(),
        "budget_id": request.budget_id,
        "auto_publish_requested": intent["auto_publish"],
        "publish_status": (result.get("batch") or {}).get("publish_status", "NOT_REQUESTED"),
        "note": ("auto_publish=true 只是意图：平台授权、有效审批与可见性未满足时发布保持 "
                 "WAITING_APPROVAL/BLOCKED，不会自动公开"),
    }
    return result


@app.get("/api/v1/automation/batches/{batch_id}")
def automation_get_batch(batch_id: str) -> dict:
    """与 GET /batches/{id} 同一实现（同一资源不重复实现）；额外给出发布意图/状态分栏。"""
    batch = get_batch(batch_id)
    identity = security.current_automation_key.get() or {}
    if identity.get("project_id") and batch.get("project_id") and batch["project_id"] != identity["project_id"]:
        raise HTTPException(403, {"code": "project_out_of_scope",
                                  "detail": f"该 Key 只允许项目 {identity['project_id']}"})
    with connect() as db:
        row = db.execute("SELECT payload FROM batches WHERE id = ?", (batch_id,)).fetchone()
    payload = json.loads(row["payload"]) if row else {}
    intent = batch_rules.normalize_publish_intent(payload.get("publish_intent"))
    batch["publish_intent"] = intent
    batch["publish"] = {
        "intent": intent["auto_publish"],
        "status": payload.get("publish_status", "NOT_REQUESTED"),
        "note": "发布状态独立于生产状态：auto_publish 只是意图，不跳过平台授权与审批",
    }
    return batch


@app.get("/api/v1/automation/jobs/{reference_id}")
def automation_get_job(reference_id: str) -> dict:
    """按 BatchItem ID / Run ID / Job ID 返回：生产状态与发布状态分开（不混成一个状态）。"""
    item = None
    with connect() as db:
        item = db.execute("SELECT * FROM batch_items WHERE id = ? OR run_id = ? OR job_id = ?",
                          (reference_id, reference_id, reference_id)).fetchone()
    if item is None:
        job = get_job(reference_id)
        run = None
        with connect() as db:
            run = db.execute("SELECT * FROM runs WHERE job_id = ?", (job_id,)).fetchone()
        return {
            "job_id": job_id,
            "kind": "run",
            "production": {
                "status": job["status"], "stage": job.get("stage"), "progress": job.get("progress"),
                "error": job.get("error"), "run_id": run["id"] if run else None,
            },
            "publish": {"intent": "none", "status": "NOT_REQUESTED",
                        "note": "该 Job 不属于批次项，没有发布意图"},
        }
    with connect() as db:
        batch = db.execute("SELECT * FROM batches WHERE id = ?", (item["batch_id"],)).fetchone()
        payload = json.loads(batch["payload"]) if batch else {}
        job_row = db.execute("SELECT * FROM jobs WHERE id = ?", (item["job_id"],)).fetchone() if item["job_id"] else None
        run_row = db.execute("SELECT * FROM runs WHERE id = ?", (item["run_id"],)).fetchone() if item["run_id"] else None
    return {
        "batch_id": item["batch_id"],
        "item_id": item["id"],
        "item_index": item["item_index"],
        "kind": "batch_item",
        "production": {
            "status": item["status"],
            "job_id": item["job_id"],
            "run_id": item["run_id"],
            "job_status": job_row["status"] if job_row else None,
            "run_status": run_row["status"] if run_row else None,
            "error": item["error"],
        },
        "publish": {
            "intent": (payload.get("publish_intent") or {}).get("auto_publish", False),
            "status": payload.get("publish_status", "NOT_REQUESTED"),
            "note": "发布状态独立于生产状态：生产成功不等于已发布，发布需要平台授权与有效审批",
        },
        "cache_key": item["cache_key"],
        "updated_at": item["updated_at"],
    }


# ---------------------------------------------------------------------------
# V6-08 事务性 Outbox：业务状态与待投递事件同事务写入；投递 Worker 异步发送
# ---------------------------------------------------------------------------

def emit_event(db: sqlite3.Connection, *, event_type: str, aggregate_type: str, aggregate_id: str,
               aggregate_revision: int, payload: dict, owner_id: str | None = None,
               project_id: str | None = None, occurred_at: str | None = None) -> str:
    """把事件写进 Outbox（**与业务写入同一事务**）；载荷含凭证或文件内容时直接拒绝。"""
    owner = owner_id or DEFAULT_OWNER_ID
    project = project_id or DEFAULT_PROJECT_ID
    event_id = str(uuid.uuid4())
    envelope = webhooks.event_envelope(
        event_id=event_id, event_type=event_type, aggregate_type=aggregate_type,
        aggregate_id=aggregate_id, aggregate_revision=int(aggregate_revision or 0),
        payload=payload, occurred_at=occurred_at or utc_now(),
    )
    body = webhooks.canonical(envelope)
    db.execute(
        "INSERT INTO outbox_events(id, event_id, event_type, owner_id, project_id, aggregate_type, aggregate_id, "
        "aggregate_revision, schema_version, payload, payload_sha256, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), event_id, event_type, owner, project, aggregate_type, aggregate_id,
         int(aggregate_revision or 0), webhooks.SCHEMA_VERSION, body,
         hashlib.sha256(body.encode("utf-8")).hexdigest(), utc_now()),
    )
    return event_id


def emit_event_standalone(**kwargs) -> str | None:
    """无业务事务可绑定的场景（如 409 阻断路径）：独立事务写入，失败不影响主流程。"""
    try:
        with connect() as db:
            return emit_event(db, **kwargs)
    except Exception as exc:  # 事件写入失败不能改变阻断结论，但会被明确打印
        print(f"[webhooks] 事件写入失败 {kwargs.get('event_type')}: {exc}", file=sys.stderr)
        return None


def _endpoints_for_event(db: sqlite3.Connection, owner_id: str, project_id: str, event_type: str,
                         event_created_at: str | None = None) -> list:
    """订阅该事件的目标。

    默认**不回填历史事件**：端点只收注册之后产生的事件（`backfill_history=1` 才回填）。
    V6-09 实测缺陷：新端点若收到全部历史事件，会瞬间形成数百条待投递队列，
    把新事件的投递延迟推到几十分钟（"事件通常 3 秒内到 UI"直接失效）。
    """
    rows = db.execute(
        "SELECT * FROM webhook_endpoints WHERE owner_id = ? AND project_id = ? AND enabled = 1 "
        "AND paused_at IS NULL ORDER BY created_at ASC",
        (owner_id, project_id),
    ).fetchall()
    matched = []
    for row in rows:
        if event_type not in [item for item in (row["event_types"] or "").split(",") if item]:
            continue
        backfill = bool(row["backfill_history"]) if "backfill_history" in row.keys() else False
        if not backfill and event_created_at and row["created_at"] and event_created_at < row["created_at"]:
            continue
        matched.append(row)
    return matched


ENDPOINT_COOLDOWN_SECONDS = 60
ENDPOINT_TICK_BUDGET = 5


def _pending_deliveries(db: sqlite3.Connection, limit: int, *, force_due: bool = False,
                        endpoint_id: str | None = None, event_id: str | None = None,
                        per_endpoint_limit: int = ENDPOINT_TICK_BUDGET) -> tuple[list[dict], dict]:
    """待投递队列：**按端点轮询**选取，保证每个目标都能推进。

    这里刻意不做「全局按事件时间排序」：那样会让历史目标（或不可达目标）长期占满名额，
    新注册的目标永远轮不到自己的事件（V6-09 负载实测到的饥饿问题）。规则：
    - 每个目标每次 tick 最多 `per_endpoint_limit` 条；
    - 默认只投递目标注册之后产生的事件（`backfill_history=1` 才回填历史）；
    - 最近一次投递是连接级失败的目标进入冷却期，整体跳过，不拖慢其他目标。
    """
    now = utc_now()
    now_epoch = _epoch_from_iso(now)
    endpoints = db.execute(
        "SELECT * FROM webhook_endpoints WHERE enabled = 1 AND paused_at IS NULL ORDER BY created_at ASC"
    ).fetchall()
    if endpoint_id:
        endpoints = [row for row in endpoints if row["id"] == endpoint_id]
    pending: list[dict] = []
    skipped: dict[str, str] = {}
    per_endpoint: dict[str, int] = {}
    for endpoint in endpoints:
        if len(pending) >= limit:
            skipped.setdefault(endpoint["id"], "global_limit")
            continue
        event_types = [item for item in (endpoint["event_types"] or "").split(",") if item]
        if not event_types:
            continue
        last_attempt = db.execute(
            "SELECT * FROM delivery_attempts WHERE endpoint_id = ? ORDER BY created_at DESC LIMIT 1",
            (endpoint["id"],),
        ).fetchone()
        if last_attempt and not force_due and last_attempt["status"] == "RETRY" \
                and last_attempt["response_status"] is None:
            age = now_epoch - _epoch_from_iso(last_attempt["created_at"])
            if age < ENDPOINT_COOLDOWN_SECONDS:
                skipped.setdefault(endpoint["id"], f"cooldown_{int(ENDPOINT_COOLDOWN_SECONDS - age)}s")
                continue
        placeholders = ", ".join("?" for _ in event_types)
        conditions = [f"event_type IN ({placeholders})", "owner_id = ?", "project_id = ?"]
        params: list = [*event_types, endpoint["owner_id"], endpoint["project_id"]]
        if not bool(endpoint["backfill_history"] if "backfill_history" in endpoint.keys() else False):
            conditions.append("created_at >= ?")
            params.append(endpoint["created_at"])
        if event_id:
            conditions.append("event_id = ?")
            params.append(event_id)
        params.append(max(per_endpoint_limit * 4, per_endpoint_limit))
        events = db.execute(
            f"SELECT * FROM outbox_events WHERE {' AND '.join(conditions)} ORDER BY created_at ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        for event in events:
            if len(pending) >= limit or per_endpoint.get(endpoint["id"], 0) >= per_endpoint_limit:
                break
            previous = db.execute(
                "SELECT * FROM delivery_attempts WHERE endpoint_id = ? AND event_id = ? ORDER BY attempt DESC LIMIT 1",
                (endpoint["id"], event["event_id"]),
            ).fetchone()
            dead = db.execute(
                "SELECT * FROM dead_letter_events WHERE endpoint_id = ? AND event_id = ? AND replayed_at IS NULL",
                (endpoint["id"], event["event_id"]),
            ).fetchone()
            if dead or (previous and previous["status"] == "DELIVERED"):
                continue
            if previous and previous["next_retry_at"] and not force_due and previous["next_retry_at"] > now:
                continue
            per_endpoint[endpoint["id"]] = per_endpoint.get(endpoint["id"], 0) + 1
            pending.append({
                "endpoint": endpoint,
                "event": event,
                "attempt": (int(previous["attempt"]) + 1) if previous else 1,
            })
    return pending, skipped


def _deliver_once(endpoint, event, attempt: int) -> dict:
    """投递一次：10 秒超时，签名放两个头，重投使用新时间戳、event_id 不变。"""
    body = event["payload"].encode("utf-8")
    timestamp = str(int(time.time()))
    signature = webhooks.signing_key(endpoint["secret_current"], timestamp, body)
    request = urllib.request.Request(endpoint["url"], data=body, method="POST")
    # 直接写入 headers 字典：urllib 的 add_header 会把名字首字母大写，投递头保持规范大小写
    request.headers["Content-Type"] = "application/json"
    request.headers[webhooks.TIMESTAMP_HEADER] = timestamp
    request.headers[webhooks.SIGNATURE_HEADER] = signature
    request.headers[webhooks.EVENT_ID_HEADER] = event["event_id"]
    request.headers[webhooks.DELIVERY_HEADER] = str(attempt)
    started = time.monotonic()
    status_code = None
    excerpt = ""
    error = None
    try:
        with urllib.request.urlopen(request, timeout=webhooks.REQUEST_TIMEOUT_SECONDS) as response:
            status_code = int(response.status)
            excerpt = response.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        excerpt = (exc.read(400) or b"").decode("utf-8", "replace")
    except Exception as exc:  # 网络/超时/地址错误都算可重试
        error = f"{type(exc).__name__}: {exc}"
    classification = webhooks.classify_response(status_code, error)
    plan = webhooks.next_attempt_plan(attempt, classification)
    return {
        "status_code": status_code,
        "excerpt": excerpt,
        "error": error,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "signature": signature,
        "timestamp": timestamp,
        "classification": classification,
        "plan": plan,
    }


def dispatch_webhooks(limit: int = 20, *, force_due: bool = False, endpoint_id: str | None = None,
                      event_id: str | None = None) -> dict:
    """投递循环：可被内部 Worker 调用；测试投递/死信重放只针对指定事件与目标。"""
    delivered = retried = dead = 0
    results: list[dict] = []
    with connect() as db:
        pending, skipped = _pending_deliveries(db, limit, force_due=force_due, endpoint_id=endpoint_id,
                                               event_id=event_id)
    for entry in pending:
        endpoint, event, attempt = entry["endpoint"], entry["event"], entry["attempt"]
        outcome = _deliver_once(endpoint, event, attempt)
        classification, plan = outcome["classification"], outcome["plan"]
        status = {"delivered": "DELIVERED", "retry": "RETRY", "dead": "DEAD"}[classification["outcome"]]
        next_retry_at = None
        if plan["action"] == "retry":
            next_retry_at = _iso_from_epoch(time.time() + plan["next_retry_in"])
        with connect() as db:
            db.execute(
                "INSERT INTO delivery_attempts(id, endpoint_id, event_id, event_type, attempt, status, "
                "response_status, response_excerpt, signature, error, next_retry_at, delivered_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), endpoint["id"], event["event_id"], event["event_type"], attempt, status,
                 outcome["status_code"], webhooks.redact_response(outcome["excerpt"]),
                 outcome["signature"], outcome["error"], next_retry_at,
                 utc_now() if status == "DELIVERED" else None, utc_now()),
            )
            if plan["dead_letter"]:
                db.execute(
                    "INSERT INTO dead_letter_events(id, endpoint_id, event_id, event_type, attempts, last_error, "
                    "reason, payload, payload_sha256, moved_at, replayed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (str(uuid.uuid4()), endpoint["id"], event["event_id"], event["event_type"], attempt,
                     outcome["error"] or outcome["excerpt"][:200], plan.get("reason", ""),
                     event["payload"], event["payload_sha256"], utc_now()),
                )
                dead += 1
                if not classification["retryable"]:
                    db.execute(
                        "UPDATE webhook_endpoints SET paused_at = ?, paused_reason = ?, updated_at = ? WHERE id = ?",
                        (utc_now(), f"投递返回 {outcome['status_code']}（非 408/429 的 4xx）：已暂停，修复后可恢复",
                         utc_now(), endpoint["id"]),
                    )
            elif status == "DELIVERED":
                delivered += 1
            else:
                retried += 1
        results.append({
            "endpoint_id": endpoint["id"], "event_id": event["event_id"], "event_type": event["event_type"],
            "attempt": attempt, "status": status, "response_status": outcome["status_code"],
            "next_retry_at": next_retry_at, "elapsed_ms": outcome["elapsed_ms"],
            "error": outcome["error"],
        })
    return {"attempted": len(results), "delivered": delivered, "retried": retried, "dead_letter": dead,
            "results": results, "force_due": force_due,
            "skipped_endpoints": skipped,
            "fairness": {"per_endpoint_tick_budget": ENDPOINT_TICK_BUDGET,
                         "endpoint_cooldown_seconds": ENDPOINT_COOLDOWN_SECONDS,
                         "note": "不可达目标不会吃掉整轮投递名额（否则健康目标事件会排队数分钟）"}}


# ---------------------------------------------------------------------------
# V6-08 Webhook 目标管理、测试投递、脱敏投递历史、死信重放、投递 Worker
# ---------------------------------------------------------------------------

class WebhookCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=500)
    event_types: list[str] = Field(default_factory=list, max_length=len(webhooks.EVENT_TYPES))
    enabled: bool = True
    secret: str | None = Field(default=None, min_length=16, max_length=200)


class WebhookUpdateRequest(BaseModel):
    revision: int
    name: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    event_types: list[str] | None = None
    enabled: bool | None = None
    reason: str = Field(default="", max_length=300)


class WebhookRotateRequest(BaseModel):
    grace_seconds: int = Field(default=86400, ge=0, le=7 * 86400)
    secret: str | None = Field(default=None, min_length=16, max_length=200)


class WebhookPauseRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


def _webhook_row(db: sqlite3.Connection, owner_id: str, project_id: str, endpoint_id: str):
    row = db.execute(
        "SELECT * FROM webhook_endpoints WHERE id = ? AND owner_id = ? AND project_id = ?",
        (endpoint_id, owner_id, project_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Webhook 目标不存在")
    return row


def _webhook_stats(db: sqlite3.Connection, endpoint_id: str) -> dict:
    rows = db.execute(
        "SELECT status, count(*) AS n FROM delivery_attempts WHERE endpoint_id = ? GROUP BY status",
        (endpoint_id,),
    ).fetchall()
    by_status = {row["status"]: row["n"] for row in rows}
    dead = db.execute(
        "SELECT count(*) AS n FROM dead_letter_events WHERE endpoint_id = ? AND replayed_at IS NULL",
        (endpoint_id,),
    ).fetchone()["n"]
    last = db.execute(
        "SELECT * FROM delivery_attempts WHERE endpoint_id = ? ORDER BY created_at DESC LIMIT 1",
        (endpoint_id,),
    ).fetchone()
    return {
        "attempts": sum(by_status.values()),
        "by_status": by_status,
        "pending_retry": by_status.get("RETRY", 0),
        "dead_letter": dead,
        "last_delivery": webhooks.delivery_public(last) if last else None,
    }


@app.get("/api/v1/webhooks/catalog")
def webhook_catalog() -> dict:
    """事件目录与签名/重试约定（投递语义为至少一次，接收方按 event_id 去重）。"""
    return webhooks.catalog()


@app.get("/api/v1/webhooks")
def list_webhooks() -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM webhook_endpoints WHERE owner_id = ? AND project_id = ? ORDER BY created_at DESC LIMIT 200",
            (owner_id, project_id),
        ).fetchall()
        return [{**webhooks.endpoint_public(row), "stats": _webhook_stats(db, row["id"])} for row in rows]


@app.post("/api/v1/webhooks", status_code=201)
def create_webhook(request: WebhookCreateRequest) -> dict:
    """注册目标：只接受 HTTPS（本机回环联调允许 http），密钥只在创建时返回一次。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    try:
        url = webhooks.validate_url(request.url)
        event_types = webhooks.normalize_event_types(request.event_types)
    except webhooks.WebhookError as exc:
        raise HTTPException(exc.status_code, exc.as_detail()) from exc
    secret = request.secret or secrets.token_urlsafe(32)
    endpoint_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO webhook_endpoints(id, owner_id, project_id, name, url, event_types, enabled, paused_at, "
            "paused_reason, secret_current, secret_previous, secret_previous_expires_at, revision, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '', ?, NULL, NULL, 1, ?, ?)",
            (endpoint_id, owner_id, project_id, request.name, url, ",".join(event_types),
             1 if request.enabled else 0, secret, now, now),
        )
        row = _webhook_row(db, owner_id, project_id, endpoint_id)
    return {**webhooks.endpoint_public(row, reveal_secret=secret),
            "note": "密钥只在创建时显示一次；请用它与 X-PDA-Timestamp 一起验签"}


@app.patch("/api/v1/webhooks/{endpoint_id}")
def update_webhook(endpoint_id: str, request: WebhookUpdateRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = _webhook_row(db, owner_id, project_id, endpoint_id)
        if int(request.revision) != int(row["revision"]):
            raise HTTPException(409, {"code": "revision_conflict",
                                      "detail": f"revision 不一致（提交 {request.revision}，当前 {row['revision']}）",
                                      "current_revision": row["revision"]})
        url = row["url"]
        if request.url is not None:
            try:
                url = webhooks.validate_url(request.url)
            except webhooks.WebhookError as exc:
                raise HTTPException(exc.status_code, exc.as_detail()) from exc
        event_types = row["event_types"]
        if request.event_types is not None:
            try:
                event_types = ",".join(webhooks.normalize_event_types(request.event_types))
            except webhooks.WebhookError as exc:
                raise HTTPException(exc.status_code, exc.as_detail()) from exc
        db.execute(
            "UPDATE webhook_endpoints SET name = ?, url = ?, event_types = ?, enabled = ?, revision = revision + 1, "
            "updated_at = ? WHERE id = ?",
            (request.name if request.name is not None else row["name"], url, event_types,
             int(row["enabled"]) if request.enabled is None else (1 if request.enabled else 0),
             utc_now(), endpoint_id),
        )
        updated = _webhook_row(db, owner_id, project_id, endpoint_id)
    return {**webhooks.endpoint_public(updated), "change_reason": request.reason}


@app.post("/api/v1/webhooks/{endpoint_id}/rotate-secret")
def rotate_webhook_secret(endpoint_id: str, request: WebhookRotateRequest) -> dict:
    """轮换密钥：旧密钥在 grace 期内仍可验签（短期双密钥），到期由投递循环清除。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = _webhook_row(db, owner_id, project_id, endpoint_id)
        new_secret = request.secret or secrets.token_urlsafe(32)
        expires_at = time.time() + request.grace_seconds if request.grace_seconds else None
        db.execute(
            "UPDATE webhook_endpoints SET secret_previous = ?, secret_previous_expires_at = ?, "
            "secret_current = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
            (row["secret_current"], expires_at, new_secret, utc_now(), endpoint_id),
        )
        updated = _webhook_row(db, owner_id, project_id, endpoint_id)
    return {**webhooks.endpoint_public(updated, reveal_secret=new_secret),
            "rotation": {"grace_seconds": request.grace_seconds,
                         "previous_valid_until": expires_at,
                         "note": "宽限期内两个密钥都能验签；到期后只接受新密钥"}}


@app.post("/api/v1/webhooks/{endpoint_id}/pause")
def pause_webhook(endpoint_id: str, request: WebhookPauseRequest) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = _webhook_row(db, owner_id, project_id, endpoint_id)
        db.execute("UPDATE webhook_endpoints SET paused_at = ?, paused_reason = ?, updated_at = ? WHERE id = ?",
                   (utc_now(), request.reason or "管理员暂停", utc_now(), endpoint_id))
        updated = _webhook_row(db, owner_id, project_id, endpoint_id)
    return {**webhooks.endpoint_public(updated), "paused": True,
            "note": "暂停只停止新投递；已入队事件保留，恢复后继续（event_id 不变）"}


@app.post("/api/v1/webhooks/{endpoint_id}/resume")
def resume_webhook(endpoint_id: str) -> dict:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        _webhook_row(db, owner_id, project_id, endpoint_id)
        db.execute("UPDATE webhook_endpoints SET paused_at = NULL, paused_reason = '', updated_at = ? WHERE id = ?",
                   (utc_now(), endpoint_id))
        updated = _webhook_row(db, owner_id, project_id, endpoint_id)
    return {**webhooks.endpoint_public(updated), "paused": False}


@app.post("/api/v1/webhooks/{endpoint_id}/test")
def test_webhook(endpoint_id: str) -> dict:
    """测试投递：写入一条测试事件并立即投递一次（不受重试计划限制）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        row = _webhook_row(db, owner_id, project_id, endpoint_id)
        if not int(row["enabled"]):
            raise HTTPException(409, {"code": "endpoint_disabled", "detail": "该目标已禁用，测试投递不会发送"})
        if row["paused_at"]:
            raise HTTPException(409, {"code": "endpoint_paused", "detail": f"该目标已暂停：{row['paused_reason']}"})
        event_id = emit_event(
            db, event_type="webhook.test", aggregate_type="webhook_endpoint", aggregate_id=endpoint_id,
            aggregate_revision=int(row["revision"]),
            payload={"endpoint_id": endpoint_id, "endpoint_name": row["name"],
                     "status": "TEST", "message": "这是一条测试事件：请验证签名与去重逻辑"},
            owner_id=owner_id, project_id=project_id,
        )
    outcome = dispatch_webhooks(limit=5, force_due=True, endpoint_id=endpoint_id, event_id=event_id)
    delivery = next((item for item in outcome["results"] if item["event_id"] == event_id), None)
    return {"endpoint_id": endpoint_id, "event_id": event_id, "delivery": delivery,
            "note": "测试事件与真实事件走同一签名/重试路径；本次立即投递一次，失败仍按计划重试"}


@app.get("/api/v1/webhooks/{endpoint_id}/deliveries")
def list_webhook_deliveries(endpoint_id: str, cursor: int = 0, limit: int = 50, status: str = "") -> dict:
    """脱敏投递历史：只返回截断并抹除凭证的响应片段，不返回密钥。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    limit = max(1, min(200, limit))
    with connect() as db:
        _webhook_row(db, owner_id, project_id, endpoint_id)
        query = "SELECT * FROM delivery_attempts WHERE endpoint_id = ?"
        params: list = [endpoint_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, attempt DESC LIMIT ? OFFSET ?"
        params.extend([limit, cursor])
        rows = db.execute(query, tuple(params)).fetchall()
        dead = db.execute(
            "SELECT * FROM dead_letter_events WHERE endpoint_id = ? ORDER BY moved_at DESC LIMIT 50",
            (endpoint_id,),
        ).fetchall()
    return {
        "endpoint_id": endpoint_id,
        "deliveries": [webhooks.delivery_public(row) for row in rows],
        "next_cursor": cursor + limit if len(rows) == limit else None,
        "dead_letters": [{
            "event_id": item["event_id"], "event_type": item["event_type"], "attempts": item["attempts"],
            "reason": item["reason"], "last_error": webhooks.redact_response(item["last_error"]),
            "moved_at": item["moved_at"], "replayed_at": item["replayed_at"],
        } for item in dead],
        "note": "投递语义为至少一次：同一 event_id 可能重复到达，接收方需按 event_id 去重",
    }


@app.post("/api/v1/webhooks/{endpoint_id}/dead-letters/{event_id}/replay")
def replay_dead_letter(endpoint_id: str, event_id: str) -> dict:
    """死信重放：清除死信标记并立即重投一次（event_id 不变，接收方仍需去重）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        _webhook_row(db, owner_id, project_id, endpoint_id)
        item = db.execute(
            "SELECT * FROM dead_letter_events WHERE endpoint_id = ? AND event_id = ?",
            (endpoint_id, event_id),
        ).fetchone()
        if not item:
            raise HTTPException(404, "死信记录不存在")
        db.execute("UPDATE dead_letter_events SET replayed_at = ? WHERE id = ?", (utc_now(), item["id"]))
        db.execute(
            "UPDATE webhook_endpoints SET paused_at = NULL, paused_reason = '', updated_at = ? WHERE id = ?",
            (utc_now(), endpoint_id),
        )
    outcome = dispatch_webhooks(limit=5, force_due=True, endpoint_id=endpoint_id, event_id=event_id)
    delivery = next((entry for entry in outcome["results"] if entry["event_id"] == event_id), None)
    return {"endpoint_id": endpoint_id, "event_id": event_id, "delivery": delivery,
            "note": "重放使用原 event_id（不变）与新的时间戳；接收方按 event_id 去重"}


@app.post("/internal/v1/webhooks/dispatch")
def internal_dispatch_webhooks(request: Request, limit: int = 20, force_due: bool = False) -> dict:
    """投递 Worker 入口（Worker 令牌；非浏览器）。force_due 用于演练与故障恢复，忽略重试排期。"""
    security.ensure_worker(request)
    return dispatch_webhooks(limit=max(1, min(200, limit)), force_due=force_due)


# ---------------------------------------------------------------------------
# V6-09 总控台：全部指标在服务端按数据库真实数据聚合（不推算、不生成）
# ---------------------------------------------------------------------------

def _count(db: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    row = db.execute(query, params).fetchone()
    return int(row["n"] or 0) if row else 0


@app.get("/api/v1/console/overview")
def console_overview(window_minutes: int = 60) -> dict:
    """总控台概览：队列深度、吞吐、失败原因、成本、事件与投递健康。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    window = max(5, min(24 * 60, int(window_minutes)))
    since = _iso_from_epoch(time.time() - window * 60)
    with connect() as db:
        job_rows = db.execute(
            "SELECT status, count(*) AS n FROM jobs GROUP BY status ORDER BY n DESC").fetchall()
        jobs_by_status = {row["status"]: row["n"] for row in job_rows}
        recent_jobs = db.execute(
            "SELECT status, count(*) AS n FROM jobs WHERE created_at >= ? GROUP BY status", (since,)).fetchall()
        run_rows = db.execute(
            "SELECT status, count(*) AS n FROM runs WHERE owner_id = ? GROUP BY status ORDER BY n DESC",
            (owner_id,)).fetchall()
        queue = {
            "queued": _count(db, "SELECT count(*) AS n FROM run_jobs WHERE status = 'QUEUED'"),
            "running": _count(db, "SELECT count(*) AS n FROM run_jobs WHERE status = 'RUNNING'"),
            "oldest_queued_at": (db.execute(
                "SELECT min(created_at) AS t FROM run_jobs WHERE status = 'QUEUED'").fetchone() or {"t": None})["t"],
            "jobs_without_queue_row": _count(
                db,
                "SELECT count(*) AS n FROM jobs j WHERE j.status = 'QUEUED' "
                "AND NOT EXISTS (SELECT 1 FROM run_jobs q WHERE q.job_id = j.id)"),
        }
        batches = {
            "by_status": {row["status"]: row["n"] for row in db.execute(
                "SELECT status, count(*) AS n FROM batches WHERE owner_id = ? GROUP BY status", (owner_id,)).fetchall()},
            "items_by_status": {row["status"]: row["n"] for row in db.execute(
                "SELECT status, count(*) AS n FROM batch_items GROUP BY status").fetchall()},
            "recent_items": {row["status"]: row["n"] for row in db.execute(
                "SELECT status, count(*) AS n FROM batch_items WHERE updated_at >= ? GROUP BY status",
                (since,)).fetchall()},
        }
        failures = [{"status": row["status"], "error": (row["error"] or "")[:160], "count": row["n"],
                     "last_at": row["last_at"]}
                    for row in db.execute(
                        "SELECT status, error, count(*) AS n, max(updated_at) AS last_at FROM jobs "
                        "WHERE status IN ('FAILED', 'QA_REJECTED') GROUP BY status, error ORDER BY n DESC LIMIT 5"
                    ).fetchall()]
        ledger = db.execute(
            "SELECT kind, currency, sum(amount) AS total FROM cost_ledger WHERE owner_id = ? GROUP BY kind, currency",
            (owner_id,),
        ).fetchall()
        usage_events = _count(db, "SELECT count(*) AS n FROM usage_events WHERE owner_id = ?", (owner_id,))
        unpriced = _count(db, "SELECT count(*) AS n FROM usage_events WHERE owner_id = ? AND amount IS NULL",
                          (owner_id,))
        outbox = {
            "total": _count(db, "SELECT count(*) AS n FROM outbox_events WHERE owner_id = ?", (owner_id,)),
            "window": _count(db, "SELECT count(*) AS n FROM outbox_events WHERE owner_id = ? AND created_at >= ?",
                             (owner_id, since)),
            "undelivered": _count(
                db,
                "SELECT count(*) AS n FROM outbox_events e WHERE e.owner_id = ? AND EXISTS ("
                "SELECT 1 FROM webhook_endpoints w WHERE w.owner_id = e.owner_id AND w.project_id = e.project_id "
                "AND w.enabled = 1 AND w.paused_at IS NULL AND (',' || w.event_types || ',') LIKE '%,' || e.event_type || ',%') "
                "AND NOT EXISTS (SELECT 1 FROM delivery_attempts a WHERE a.event_id = e.event_id AND a.status = 'DELIVERED')",
                (owner_id,)),
        }
        latency_rows = db.execute(
            "SELECT a.created_at AS delivered_at, e.created_at AS event_at FROM delivery_attempts a "
            "JOIN outbox_events e ON e.event_id = a.event_id "
            "WHERE a.status = 'DELIVERED' AND a.created_at >= ? ORDER BY a.created_at DESC LIMIT 200",
            (since,),
        ).fetchall()
        latencies = sorted(
            max(0.0, (_epoch_from_iso(row["delivered_at"]) - _epoch_from_iso(row["event_at"])) * 1000.0)
            for row in latency_rows
        )
        deliveries = {
            "attempts": _count(db, "SELECT count(*) AS n FROM delivery_attempts WHERE created_at >= ?", (since,)),
            "delivered": _count(db, "SELECT count(*) AS n FROM delivery_attempts WHERE status = 'DELIVERED' AND created_at >= ?", (since,)),
            "retry": _count(db, "SELECT count(*) AS n FROM delivery_attempts WHERE status = 'RETRY' AND created_at >= ?", (since,)),
            "dead": _count(db, "SELECT count(*) AS n FROM delivery_attempts WHERE status = 'DEAD' AND created_at >= ?", (since,)),
            "dead_letters_open": _count(db, "SELECT count(*) AS n FROM dead_letter_events WHERE replayed_at IS NULL"),
            "paused_endpoints": _count(db, "SELECT count(*) AS n FROM webhook_endpoints WHERE paused_at IS NOT NULL"),
            "delivery_latency_ms": {
                "sample": len(latencies),
                "p50": round(latencies[len(latencies) // 2], 1) if latencies else None,
                "p95": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1) if latencies else None,
                "max": round(latencies[-1], 1) if latencies else None,
                "note": "事件写入 Outbox 到成功投递的实测耗时（同一数据库时间源）",
            },
        }
        keys = {
            "active": _count(db, "SELECT count(*) AS n FROM automation_keys WHERE revoked_at IS NULL"),
            "revoked": _count(db, "SELECT count(*) AS n FROM automation_keys WHERE revoked_at IS NOT NULL"),
            "calls_window": _count(db, "SELECT coalesce(sum(call_count), 0) AS n FROM automation_keys"),
        }
        latest_events = [{
            "event_id": row["event_id"], "event_type": row["event_type"], "created_at": row["created_at"],
            "aggregate_id": row["aggregate_id"],
            "delivered": _count(db, "SELECT count(*) AS n FROM delivery_attempts WHERE event_id = ? AND status = 'DELIVERED'",
                                (row["event_id"],)),
            "attempts": _count(db, "SELECT count(*) AS n FROM delivery_attempts WHERE event_id = ?", (row["event_id"],)),
        } for row in db.execute(
            "SELECT * FROM outbox_events WHERE owner_id = ? ORDER BY created_at DESC LIMIT 10", (owner_id,)).fetchall()]
    return {
        "measured_at": utc_now(),
        "window_minutes": window,
        "jobs": {
            "by_status": jobs_by_status,
            "total": sum(jobs_by_status.values()),
            "window": {row["status"]: row["n"] for row in recent_jobs},
            # 成功口径：生产成功包括 SUCCEEDED（批次/普通 Run）与 VERIFICATION_PASSED（受控渲染）
            "succeeded": (jobs_by_status.get("SUCCEEDED", 0) + jobs_by_status.get("VERIFICATION_PASSED", 0)),
            "success_rate": (round((jobs_by_status.get("SUCCEEDED", 0) + jobs_by_status.get("VERIFICATION_PASSED", 0))
                                   / max(1, jobs_by_status.get("SUCCEEDED", 0)
                                         + jobs_by_status.get("VERIFICATION_PASSED", 0)
                                         + jobs_by_status.get("FAILED", 0)
                                         + jobs_by_status.get("QA_REJECTED", 0)), 4)),
        },
        "runs": {row["status"]: row["n"] for row in run_rows},
        "queue": queue,
        "batches": batches,
        "failures": failures,
        "cost": {
            "by_kind_currency": [{"kind": row["kind"], "currency": row["currency"],
                                  "total": round(float(row["total"] or 0), 6)} for row in ledger],
            "usage_events": usage_events, "unpriced_usage_events": unpriced,
            "note": "自管资源为内部估算；未定价事件不按 0 计入",
        },
        "events": {**outbox, "latest": latest_events},
        "webhooks": deliveries,
        "automation_keys": keys,
        "notes": [
            "全部数值来自数据库实时聚合（jobs/runs/run_jobs/batches/batch_items/cost_ledger/outbox_events/delivery_attempts）",
            "queue.jobs_without_queue_row > 0 表示存在无法被 Worker 领取的作业：对账会自动补建队列行",
            "success_rate 用真实终态作业计算，不包含仍在队列中的作业",
        ],
    }


@app.post("/internal/v1/batches/advance")
def internal_advance_batches(request: Request, limit: int = 20) -> dict:
    """调度 Worker 入口：推进所有未完成且未暂停的批次（等价于部署里的调度 tick）。"""
    security.ensure_worker(request)
    owner_id, _, _ = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        rows = db.execute(
            "SELECT id FROM batches WHERE owner_id = ? AND paused = 0 AND cancelled = 0 "
            "AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at ASC LIMIT ?",
            (owner_id, max(1, min(200, limit))),
        ).fetchall()
    results = []
    for row in rows:
        try:
            outcome = advance_batch(row["id"], execute_inline=False)
            results.append({"batch_id": row["id"], "scheduled": len(outcome.get("started") or []),
                            "status": outcome["batch"]["status"]})
        except HTTPException as exc:
            results.append({"batch_id": row["id"], "error": exc.detail})
    return {"advanced": len(results), "results": results,
            "note": "调度不执行渲染：只按并发上限为 PENDING 项创建 Run 与队列行，Worker 领取后才会真正执行"}


# ---------------------------------------------------------------------------
# V6-10…15 发布：连接器状态、账号授权、预检、审批、发布任务与对账
# 诚实边界：无真实平台授权时一律 BLOCKED/NOT_CONFIGURED，不伪造发布成功；
# 提交成功 ≠ 已发布；只有上游查询确认才标 PUBLISHED；拿不到链接只留平台 ID。
# ---------------------------------------------------------------------------

class PublishingConnectRequest(BaseModel):
    platform: Literal["tiktok", "youtube", "instagram", "facebook_page"]
    return_path: str = Field(default="/", max_length=300)


class PublishingOAuthCallbackRequest(BaseModel):
    state: str = Field(min_length=8, max_length=200)
    code: str = Field(min_length=4, max_length=2000)
    code_verifier: str | None = Field(default=None, max_length=200)


class PublishingPreflightRequest(BaseModel):
    package_ids: list[str] = Field(min_length=1, max_length=20)
    account_ids: list[str] = Field(default_factory=list, max_length=20)
    options: dict = Field(default_factory=dict)


class PublishingApprovalRequest(BaseModel):
    preflight_id: str
    confirmations: dict = Field(default_factory=dict)
    scope: Literal["single_publish", "automation_policy"] = "single_publish"
    expires_in_seconds: int = Field(default=3600, ge=1, le=30 * 86400)


class PublishingJobRequest(BaseModel):
    approval_id: str
    package_id: str
    account_id: str
    publish_intent_id: str = Field(min_length=1, max_length=120)
    options: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=120)


class PublishingActionRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


def _connector_credentials(platform: str) -> dict:
    """从环境读取平台凭据（本部署未配置任何平台凭据 → 一律 NOT_CONFIGURED）。"""
    return publishing_connectors.credentials_from_env(platform)


def normalize_context_for_publishing() -> tuple[str, str, str]:
    """发布相关接口的归属上下文（与其它接口同一套冻结上下文校验）。"""
    return normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)


def _account_row(db: sqlite3.Connection, owner_id: str, project_id: str, account_id: str):
    row = db.execute(
        "SELECT * FROM connected_accounts WHERE id = ? AND owner_id = ? AND project_id = ?",
        (account_id, owner_id, project_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "账号不存在或未授权")
    return row


def _account_public(row) -> dict:
    capabilities = {}
    try:
        capabilities = json.loads(row["capabilities"]) if row["capabilities"] else {}
    except (json.JSONDecodeError, TypeError):
        capabilities = {}
    return {
        "id": row["id"], "platform": row["platform"], "account_ref": row["account_ref"],
        "display_name": row["display_name"], "scopes": [item for item in (row["scopes"] or "").split(",") if item],
        "authorization_status": row["authorization_status"], "connected": row["disconnected_at"] is None,
        "capabilities": capabilities, "capabilities_checked_at": row["capabilities_checked_at"],
        "connected_at": row["connected_at"], "disconnected_at": row["disconnected_at"],
        "disconnect_reason": row["disconnect_reason"], "revision": row["revision"],
        "note": ("授权真实存在：能力来自平台查询" if row["capabilities_checked_at"]
                 else "账号能力尚未实时查询：预检会给出 WARNING，发布前必须刷新"),
    }


@app.get("/api/v1/publishing/connectors")
def list_publishing_connectors() -> dict:
    """四个默认原生连接器状态：未配置授权时如实 NOT_CONFIGURED（不假装可用）。"""
    return publishing_connectors.connector_overview()


@app.get("/api/v1/publishing/accounts")
def list_publishing_accounts(platform: str = "") -> list[dict]:
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    query = "SELECT * FROM connected_accounts WHERE owner_id = ? AND project_id = ?"
    params: list = [owner_id, project_id]
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    query += " ORDER BY created_at DESC LIMIT 200"
    with connect() as db:
        rows = db.execute(query, tuple(params)).fetchall()
    return [_account_public(row) for row in rows]


@app.post("/api/v1/publishing/accounts/connect")
def begin_publishing_authorization(request: PublishingConnectRequest) -> dict:
    """官方授权跳转信息：state + PKCE 由服务端生成并落库；未配置应用凭据时明确 BLOCKED。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    state = secrets.token_urlsafe(24)
    verifier, challenge = publishing_connectors.pkce_pair()
    redirect_uri = publishing_connectors.redirect_uri()
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO oauth_states(id, owner_id, project_id, platform, state, code_verifier, return_path, "
            "created_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (str(uuid.uuid4()), owner_id, project_id, request.platform, state, verifier,
             request.return_path, now),
        )
    credentials = _connector_credentials(request.platform)
    try:
        authorization = publishing_connectors.begin_authorization(
            request.platform, state=state, code_challenge=challenge, redirect_uri=redirect_uri,
            credentials=credentials,
        )
    except publishing.PublishError as exc:
        raise HTTPException(exc.status_code, exc.as_detail()) from exc
    authorization["state"] = state
    authorization["redirect_uri"] = redirect_uri
    authorization["note"] = ("state 与 PKCE 已落库；回调必须带回同一 state（防 CSRF）"
                            "；本产品不做浏览器自动化绕过平台授权")
    return authorization


@app.post("/api/v1/publishing/oauth/{platform}/callback")
def publishing_oauth_callback(platform: str, request: PublishingOAuthCallbackRequest) -> dict:
    """OAuth 回调：校验 state（一次性）与 PKCE，向上游换 token；未配置应用凭据时 BLOCKED。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM oauth_states WHERE state = ? AND owner_id = ? AND project_id = ? AND platform = ?",
            (request.state, owner_id, project_id, platform),
        ).fetchone()
        if not row:
            raise HTTPException(400, {"code": "unknown_state",
                                      "detail": "state 不存在或不属于本项目：拒绝回调（防 CSRF）"})
        if row["consumed_at"]:
            raise HTTPException(409, {"code": "state_already_used",
                                      "detail": f"state 已于 {row['consumed_at']} 使用：一次性校验"})
        db.execute("UPDATE oauth_states SET consumed_at = ? WHERE id = ?", (utc_now(), row["id"]))
    credentials = _connector_credentials(platform)
    try:
        exchanged = publishing_connectors.exchange_callback(
            platform, code=request.code, code_verifier=request.code_verifier or row["code_verifier"],
            redirect_uri=publishing_connectors.redirect_uri(), credentials=credentials,
        )
    except publishing.PublishError as exc:
        raise HTTPException(exc.status_code, exc.as_detail()) from exc
    if not exchanged.get("connected"):
        return exchanged
    now = utc_now()
    account_id = str(uuid.uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO connected_accounts(id, owner_id, project_id, platform, account_ref, display_name, "
            "credential_ref, scopes, capabilities, authorization_status, capabilities_checked_at, connected_at, "
            "disconnected_at, disconnect_reason, revision, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONNECTED', NULL, ?, NULL, '', 1, ?, ?)",
            (account_id, owner_id, project_id, platform, exchanged["account_ref"],
             exchanged.get("display_name") or exchanged["account_ref"], exchanged["credential_ref"],
             ",".join(exchanged.get("scopes") or []), json.dumps(exchanged.get("capabilities") or {},
                                                                 ensure_ascii=False),
             now, now, now),
        )
        account = _account_row(db, owner_id, project_id, account_id)
    return {**_account_public(account), "credential_note": "凭证只保存引用，不回传明文"}


@app.delete("/api/v1/publishing/accounts/{account_id}")
def disconnect_publishing_account(account_id: str, request: PublishingActionRequest) -> dict:
    """断开账号：撤销未来任务访问；已提交的发布不会被"取消"（上游语义见各平台文档）。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        begin_immediate(db)
        row = _account_row(db, owner_id, project_id, account_id)
        if row["disconnected_at"]:
            return {**_account_public(row), "disconnected": True, "note": "该账号早已断开（幂等）"}
        active_jobs = db.execute(
            "SELECT id, state FROM publish_jobs WHERE account_id = ? AND state IN "
            "('QUEUED', 'UPLOADING', 'PROCESSING', 'RECONCILING', 'CANCEL_REQUESTED')",
            (account_id,),
        ).fetchall()
        now = utc_now()
        db.execute(
            "UPDATE connected_accounts SET authorization_status = 'REVOKED', disconnected_at = ?, "
            "disconnect_reason = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
            (now, request.reason or "管理员断开", now, account_id),
        )
        db.execute(
            "UPDATE publish_jobs SET state = 'BLOCKED', last_error = ?, updated_at = ? WHERE account_id = ? "
            "AND state IN ('QUEUED', 'UPLOADING', 'RECONCILING')",
            ("账号已断开：需要重新授权后才能继续", now, account_id),
        )
        updated = _account_row(db, owner_id, project_id, account_id)
    return {**_account_public(updated), "disconnected": True,
            "blocked_active_jobs": [dict(item) for item in active_jobs],
            "note": "断开只撤销未来访问；已在平台侧提交的内容不会被本系统删除"}


@app.post("/api/v1/publishing/preflight")
def publishing_preflight(request: PublishingPreflightRequest) -> dict:
    """预检：逐包逐平台给出可执行/阻断项，并产出审批要绑定的快照哈希。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    results: list[dict] = []
    with connect() as db:
        accounts = db.execute(
            "SELECT * FROM connected_accounts WHERE owner_id = ? AND project_id = ? AND disconnected_at IS NULL",
            (owner_id, project_id),
        ).fetchall()
        if request.account_ids:
            accounts = [row for row in accounts if row["id"] in set(request.account_ids)]
        for package_id in request.package_ids:
            package_row = db.execute(
                "SELECT * FROM publish_packages WHERE id = ? AND owner_id = ? AND project_id = ?",
                (package_id, owner_id, project_id),
            ).fetchone()
            if not package_row:
                raise HTTPException(404, f"发布包不存在: {package_id}")
            package = _package_for_publishing(db, package_row)
            if not accounts:
                results.append({
                    "package_id": package_id, "platform": None, "executable": False,
                    "blocking": [{"code": "no_connected_account", "severity": "BLOCKING",
                                  "detail": "没有已授权账号：请先完成平台官方授权（未授权一律 BLOCKED）"}],
                    "warnings": [], "snapshot": None, "snapshot_hash": None,
                })
                continue
            for account in accounts:
                platform = account["platform"]
                connector = publishing_connectors.connector_status(platform, _connector_credentials(platform))
                account_view = {
                    "id": account["id"], "connected": account["disconnected_at"] is None,
                    "authorization_status": account["authorization_status"],
                    "capabilities_checked_at": account["capabilities_checked_at"],
                    "revision": account["revision"],
                }
                outcome = publishing.preflight(
                    platform=platform, package=package, account=account_view,
                    options=request.options, connector=connector,
                )
                # 连接器层的平台素材限制（官方确认的才阻断；未确认的只给 WARNING）
                try:
                    platform_rules = publishing_connectors.validate_package(
                        platform, package, account_view, request.options, _connector_credentials(platform))
                    outcome["blocking"] = list(outcome["blocking"]) + list(platform_rules["blocking"])
                    outcome["warnings"] = list(outcome["warnings"]) + list(platform_rules["warnings"])
                    outcome["limits_source"] = platform_rules["limits_source"]
                    outcome["executable"] = not outcome["blocking"]
                except publishing.PublishError as exc:
                    outcome.setdefault("warnings", []).append({"code": exc.code, "detail": exc.message})
                outcome["package_id"] = package_id
                outcome["account_id"] = account["id"]
                results.append(outcome)
        preflight_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO publish_preflights(id, owner_id, project_id, payload, payload_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (preflight_id, owner_id, project_id, _canonical_json_text({"results": results}),
             hashlib.sha256(_canonical_json_text({"results": results}).encode("utf-8")).hexdigest(), utc_now()),
        )
    return {
        "preflight_id": preflight_id,
        "results": results,
        "executable_count": sum(1 for item in results if item.get("executable")),
        "blocked_count": sum(1 for item in results if not item.get("executable")),
        "note": ("预检不产生任何平台动作；审批必须引用某一条结果的 snapshot_hash，"
                 "绑定字段变化即使审批失效"),
    }


def _package_for_publishing(db: sqlite3.Connection, row) -> dict:
    payload = json.loads(row["payload"])
    manifest = payload.get("manifest") or {}
    video = next((item for item in manifest.get("files", []) if item.get("role") == "final_video"), {})
    verification = payload.get("verification") or {}
    return {
        "id": row["id"], "version_id": f"{row['id']}:v{row['version']}", "status": row["status"],
        "content_hash": row["content_hash"], "stored_hash": row["content_hash"],
        "locale": row["locale"], "profile_id": row["profile_id"],
        "qa_passed": bool((manifest.get("qa") or {}).get("passed")),
        "approval_ref": manifest.get("approval_ref"),
        "duration_seconds": float((manifest.get("video") or {}).get("duration_seconds") or 0.0),
        "has_music": bool((manifest.get("audio") or {}).get("mixed", {}).get("enabled")),
        "music_license_ref": (manifest.get("audio") or {}).get("music_license_ref"),
        "unlicensed_font": not bool((manifest.get("subtitles") or {}).get("font_license")),
        "verification_failures": verification.get("failures") or [],
        "video_file": video.get("path"),
    }


@app.post("/api/v1/publishing/approvals", status_code=201)
def create_publishing_approval(request: PublishingApprovalRequest) -> dict:
    """创建审批：必须引用预检快照哈希，并显式确认包哈希/账号/可见性/声明。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM publish_preflights WHERE id = ? AND owner_id = ? AND project_id = ?",
            (request.preflight_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "预检记录不存在")
        stored = json.loads(row["payload"])["results"]
        candidates = [item for item in stored if item.get("executable")]
        if not candidates:
            raise HTTPException(409, {"code": "no_executable_preflight",
                                      "detail": "该预检没有任何可执行结果：不允许创建审批",
                                      "blocking": [item.get("blocking") for item in stored]})
        target = candidates[0]
        try:
            approval = publishing.build_approval(
                preflight_result=target, actor=current_actor_label(), expires_in_seconds=request.expires_in_seconds,
                confirmations=request.confirmations, scope=request.scope,
            )
        except publishing.PublishError as exc:
            raise HTTPException(exc.status_code, exc.as_detail()) from exc
        approval_id = str(uuid.uuid4())
        expires_at = time.time() + request.expires_in_seconds
        now = utc_now()
        db.execute(
            "INSERT INTO publish_approvals(id, owner_id, project_id, preflight_id, platform, account_id, "
            "package_id, snapshot_hash, scope, binding, actor, expires_at, revoked_at, payload, payload_sha256, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
            (approval_id, owner_id, project_id, request.preflight_id, target["platform"],
             target.get("account_id"), target["package_id"], approval["snapshot_hash"], request.scope,
             _canonical_json_text(approval["binding"]), current_actor_label(), expires_at,
             _canonical_json_text(approval), hashlib.sha256(_canonical_json_text(approval).encode()).hexdigest(),
             now),
        )
    return {"id": approval_id, "expires_at": expires_at, **approval,
            "note": "审批绑定包哈希/账号/平台/文案/可见性/声明与范围；任何绑定字段变化都会使审批失效"}


@app.post("/api/v1/publishing/jobs", status_code=202)
def create_publishing_job(request: PublishingJobRequest, response: Response, http_request: Request) -> dict:
    """创建发布任务：审批 + 预检快照必须仍然一致；防重键 = 包版本 + 账号 + 发布意图。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    gate = _idempotency_gate(http_request, "publishing.jobs.create", request.model_dump())
    replay = gate.replay()
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    with connect() as db:
        begin_immediate(db)
        approval_row = db.execute(
            "SELECT * FROM publish_approvals WHERE id = ? AND owner_id = ? AND project_id = ?",
            (request.approval_id, owner_id, project_id),
        ).fetchone()
        if not approval_row:
            raise HTTPException(404, "审批不存在")
        if approval_row["revoked_at"]:
            raise HTTPException(409, {"code": "approval_revoked", "detail": "审批已撤销"})
        if approval_row["expires_at"] is not None and float(approval_row["expires_at"]) <= time.time():
            raise HTTPException(409, {"code": "approval_expired", "detail": "审批已过期：请重新预检并审批"})
        package_row = db.execute(
            "SELECT * FROM publish_packages WHERE id = ? AND owner_id = ? AND project_id = ?",
            (request.package_id, owner_id, project_id),
        ).fetchone()
        if not package_row:
            raise HTTPException(404, "发布包不存在")
        account_row = _account_row(db, owner_id, project_id, request.account_id)
        package = _package_for_publishing(db, package_row)
        platform = account_row["platform"]
        connector = publishing_connectors.connector_status(platform, _connector_credentials(platform))
        current_snapshot = publishing.preflight(
            platform=platform, package=package,
            account={"id": account_row["id"], "connected": account_row["disconnected_at"] is None,
                     "authorization_status": account_row["authorization_status"],
                     "capabilities_checked_at": account_row["capabilities_checked_at"],
                     "revision": account_row["revision"]},
            options=request.options, connector=connector,
        )["snapshot"]
        validity = publishing.approval_still_valid(
            approval_binding=json.loads(approval_row["binding"]), current_snapshot=current_snapshot)
        key = publishing.dedupe_key(package_version_id=package["version_id"], account_id=account_row["id"],
                                    publish_intent_id=request.publish_intent_id)
        existing = db.execute("SELECT * FROM publish_jobs WHERE dedupe_key = ?", (key,)).fetchone()
        if existing:
            return {"job": publishing.job_public(existing), "reused": True, "created": False,
                    "note": "同一发布包版本 + 账号 + 意图已存在任务：用户需要再次发布时必须创建新的 intent"}
        plan = publishing.submit_plan(connector=connector, package=package, options=request.options,
                                      execution_key=key, approval=validity)
        job_id = str(uuid.uuid4())
        now = utc_now()
        payload = {
            "options": request.options, "visibility": current_snapshot["visibility"],
            "connector": connector, "history": [{"state": plan["state"], "note": plan.get("note") or plan.get("detail"),
                                                 "at": now}],
            "submission_unknown": False, "blocked_reason": None if plan["accepted"] else plan.get("reason"),
            "approval_snapshot_hash": approval_row["snapshot_hash"],
        }
        if not plan["accepted"]:
            payload["blocked_reason"] = plan.get("reason")
        blocked_detail = None if plan["accepted"] else str(plan.get("detail") or plan.get("reason") or "")
        # 显式转成数据库可绑定类型（sqlite/psycopg 对非标量类型会直接报错）
        values = tuple(
            None if value is None else (value if isinstance(value, (int, float, str, bytes)) else str(value))
            for value in (job_id, str(owner_id), str(project_id), str(platform), str(account_row["id"]),
                          str(package_row["id"]), str(package["version_id"]), str(request.publish_intent_id),
                          str(approval_row["id"]), str(key), str(plan["state"]), blocked_detail,
                          _canonical_json_text(payload),
                          hashlib.sha256(_canonical_json_text(payload).encode("utf-8")).hexdigest(), now, now)
        )
        db.execute(
            "INSERT INTO publish_jobs(id, owner_id, project_id, platform, account_id, package_id, package_version_id, "
            "publish_intent_id, approval_id, dedupe_key, state, external_publish_id, permalink_url, last_error, "
            "attempt_count, payload, payload_sha256, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 0, ?, ?, ?, ?)",
            values,
        )
        row = db.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        created = publishing.job_public(row, connector=connector)
    result = {"job": created, "created": True, "reused": False, "submit_plan": plan,
              "approval_valid": validity,
              "note": ("提交只表示进入上传流程；连接器未配置时任务为 BLOCKED（不伪造发布成功）"
                       if plan["accepted"] else "任务已创建但处于阻断状态：原因见 blocked_reason")}
    gate.store(result, 202)
    return result


@app.get("/api/v1/publishing/jobs")
def list_publishing_jobs(state: str = "", platform: str = "") -> list[dict]:
    owner_id, _, project_id = normalize_context_for_publishing()
    query = "SELECT * FROM publish_jobs WHERE owner_id = ? AND project_id = ?"
    params: list = [owner_id, project_id]
    if state:
        query += " AND state = ?"
        params.append(state)
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    query += " ORDER BY created_at DESC LIMIT 200"
    with connect() as db:
        rows = db.execute(query, tuple(params)).fetchall()
    return [publishing.job_public(row) for row in rows]


@app.get("/api/v1/publishing/jobs/{job_id}")
def get_publishing_job(job_id: str) -> dict:
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND owner_id = ? AND project_id = ?",
            (job_id, owner_id, project_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "发布任务不存在")
    connector = publishing_connectors.connector_status(row["platform"], _connector_credentials(row["platform"]))
    return publishing.job_public(row, connector=connector)


def _transition_publish_job(db, row, target: str, note: str, **updates) -> None:
    publishing.check_transition(row["state"], target)
    payload = json.loads(row["payload"])
    payload = publishing.append_history(payload, target, note, utc_now())
    payload["previous_state"] = row["state"]
    fields = {"state": target, "payload": _canonical_json_text(payload), "updated_at": utc_now()}
    fields.update(updates)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    db.execute(f"UPDATE publish_jobs SET {assignments} WHERE id = ?", (*fields.values(), row["id"]))


@app.post("/api/v1/publishing/jobs/{job_id}/reconcile")
def reconcile_publishing_job(job_id: str) -> dict:
    """对账：只查询既有提交，不发新帖；结果未知时保持 RECONCILING 而不是宣称成功。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND owner_id = ? AND project_id = ?",
            (job_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "发布任务不存在")
        connector = publishing_connectors.connector_status(row["platform"], _connector_credentials(row["platform"]))
        if connector["status"] != "READY":
            raise HTTPException(409, {"code": "connector_not_configured",
                                      "detail": f"连接器未配置（缺少：{', '.join(connector['missing_requirements'])}）："
                                                "无法查询上游状态"})
        queried = publishing_connectors.query_publish(row["platform"], row["external_publish_id"],
                                                     _connector_credentials(row["platform"]))
        outcome = publishing.interpret_query(operation_handle=queried, platform=row["platform"])
        _transition_publish_job(db, row, outcome["state"], outcome["note"],
                                external_publish_id=outcome.get("external_publish_id"),
                                permalink_url=outcome.get("permalink_url"))
        updated = db.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return {**publishing.job_public(updated), "query": outcome,
            "note": "对账只读取上游状态；没有公开链接时只保留平台 ID，不捏造 URL"}


@app.post("/api/v1/publishing/jobs/{job_id}/retry")
def retry_publishing_job(job_id: str, request: PublishingActionRequest) -> dict:
    """重试：只对确定可安全重试的失败执行；提交结果未知时先对账。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND owner_id = ? AND project_id = ?",
            (job_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "发布任务不存在")
        payload = json.loads(row["payload"])
        decision = publishing.retry_decision(
            state=row["state"], failure_code=payload.get("failure_code"),
            submission_unknown=bool(payload.get("submission_unknown")))
        if not decision["retry"]:
            raise HTTPException(409, {"code": decision["reason"], "detail": decision["note"]})
        _transition_publish_job(db, row, "QUEUED", f"重试：{decision['reason']}（{request.reason}）",
                                last_error=None, attempt_count=int(row["attempt_count"]) + 1)
        updated = db.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return {**publishing.job_public(updated), "retry_decision": decision}


@app.post("/api/v1/publishing/jobs/{job_id}/cancel")
def cancel_publishing_job(job_id: str, request: PublishingActionRequest) -> dict:
    """取消：显式返回上游支持程度；不支持时如实说明，不伪造取消成功。"""
    owner_id, _, project_id = normalize_context_for_publishing()
    with connect() as db:
        begin_immediate(db)
        row = db.execute(
            "SELECT * FROM publish_jobs WHERE id = ? AND owner_id = ? AND project_id = ?",
            (job_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "发布任务不存在")
        capability = publishing.cancel_capability(row["platform"], row["state"])
        if not capability["supported"]:
            return {**publishing.job_public(row), "cancel": capability, "cancelled": False,
                    "note": "上游不支持取消：任务状态保持不变（不伪造取消成功）"}
        try:
            outcome = publishing_connectors.cancel_publish(row["platform"], row["external_publish_id"],
                                                          _connector_credentials(row["platform"]))
        except publishing.PublishError as exc:
            raise HTTPException(exc.status_code, exc.as_detail()) from exc
        target = "CANCELLED" if outcome.get("cancelled") else "CANCEL_REQUESTED"
        _transition_publish_job(db, row, target, outcome.get("note") or "已请求取消")
        updated = db.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return {**publishing.job_public(updated), "cancel": capability, "cancelled": target == "CANCELLED",
            "reason": request.reason, "upstream": outcome}


# ---------------------------------------------------------------------------
# V6-16 成员与角色（Owner / Editor / Reviewer / Publisher）
# ---------------------------------------------------------------------------

class MemberCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: Literal["editor", "reviewer", "publisher"]


class MemberRevokeRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


@app.get("/api/v1/roles/matrix")
def role_matrix() -> dict:
    """权限矩阵（角色 → 权限、职责分离说明）。"""
    return roles.matrix()


@app.get("/api/v1/members")
def list_members() -> list[dict]:
    owner_id, _, _ = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        rows = db.execute("SELECT * FROM members WHERE owner_id = ? ORDER BY created_at DESC LIMIT 200",
                          (owner_id,)).fetchall()
    return [roles.member_public(row) for row in rows]


@app.post("/api/v1/members", status_code=201)
def create_member(request: MemberCreateRequest) -> dict:
    """创建成员（只允许 Owner）：令牌只显示一次，角色决定服务端权限。"""
    owner_id, _, _ = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    generated = roles.generate_member_token()
    member_row_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO members(id, member_id, owner_id, name, role, token_hash, token_hint, revoked_at, "
            "revoked_reason, last_used_at, call_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '', NULL, 0, ?, ?)",
            (member_row_id, generated["member_id"], owner_id, request.name, request.role,
             generated["token_hash"], generated["hint"], now, now),
        )
        row = db.execute("SELECT * FROM members WHERE id = ?", (member_row_id,)).fetchone()
    return {**roles.member_public(row, reveal=generated["plaintext"]),
            "matrix": roles.matrix()["separation_of_duties"]}


@app.delete("/api/v1/members/{member_row_id}")
def revoke_member(member_row_id: str, request: MemberRevokeRequest) -> dict:
    owner_id, _, _ = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    with connect() as db:
        begin_immediate(db)
        row = db.execute("SELECT * FROM members WHERE id = ? AND owner_id = ?",
                         (member_row_id, owner_id)).fetchone()
        if not row:
            raise HTTPException(404, "成员不存在")
        if row["revoked_at"]:
            return {**roles.member_public(row), "revoked": True, "note": "该成员早已撤销（幂等）"}
        now = utc_now()
        db.execute("UPDATE members SET revoked_at = ?, revoked_reason = ?, updated_at = ? WHERE id = ?",
                   (now, request.reason or "管理员撤销", now, member_row_id))
        updated = db.execute("SELECT * FROM members WHERE id = ?", (member_row_id,)).fetchone()
    return {**roles.member_public(updated), "revoked": True,
            "note": "撤销立即生效：该成员令牌的新调用返回 401"}


class ProductionShotInput(BaseModel):
    id: str = Field(default="", max_length=40)
    name: str = Field(default="", max_length=120)
    camera: Literal["dolly_in", "side_track", "hero_orbit", "static"] = "static"
    focal_length_mm: int = Field(default=35, ge=15, le=120)
    duration_frames: int = Field(ge=24, le=1440)
    camera_target_m: Vector3 | None = None
    camera_path: CameraPath | None = None
    caption_text: str = Field(default="", max_length=300)


class ProductionPlanRequest(BaseModel):
    """V6 生产计划：按 Profile 规格做多场景（2–8 镜头、3–60 秒），不受 V1 三镜头合同限制。"""

    product_asset_id: str
    profile_id: str = Field(min_length=1, max_length=80)
    intent: str = Field(min_length=1, max_length=4000)
    shots: list[ProductionShotInput] = Field(min_length=2, max_length=8)
    locale: str = Field(default="es-MX", max_length=20)
    voiceover_text: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=2000)
    # V6-16（复核 BUG-04）：模型外观未核验时，正式生产计划必须显式接受风险，否则拒绝创建。
    accept_unverified_appearance: bool = False
    render_mode: Literal["controlled", "h3_scenes"] = "controlled"


@app.post("/api/v1/plans/production", status_code=201)
def create_production_plan(request: ProductionPlanRequest) -> dict:
    """按 Profile 规格创建多场景生产计划（V6 loop：完整有声视频 → 批量 → 发布包）。"""
    owner_id, _, project_id = normalize_contract_context(DEFAULT_OWNER_ID, DEFAULT_PROJECT_ID)
    parsed_scenes = None
    if request.render_mode == "h3_scenes":
        try:
            parsed_scenes = scene_generation.parse_brief(request.intent)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        request.shots = [ProductionShotInput.model_validate(shot) for shot in parsed_scenes]
    with connect() as db:
        begin_immediate(db)
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.product_asset_id,)).fetchone()
        if not asset:
            raise HTTPException(404, "产品素材不存在")
        if asset["owner_id"] != owner_id:
            raise HTTPException(403, "越权使用产品素材")
        scene_reference = resolve_scene_reference(db, asset, owner_id) if parsed_scenes else None
        profile = _profile_for_request(db, request.profile_id, owner_id, project_id)
        if profile is None:
            raise HTTPException(404, "Profile 不存在")
        spec = profile["spec"]
        total_frames = sum(int(shot.duration_frames) for shot in request.shots)
        seconds = total_frames / DEFAULT_FPS
        low, high = spec.video.duration_min_seconds, spec.video.duration_max_seconds
        if not (low <= seconds <= high):
            raise HTTPException(422, {
                "code": "duration_out_of_profile_range",
                "detail": f"计划总时长 {seconds:.2f}s 不在 Profile {request.profile_id} 允许范围 {low}–{high}s 内",
            })
        # V6-16（复核 BUG-04）：结构校验之后再做外观门——外观未核验的模型不能悄悄进入正式广告流程
        appearance = None
        if asset["kind"] == "model" and not parsed_scenes:
            try:
                appearance = inspect_glb(resolve_asset_path(row_to_dict(asset)).read_bytes())
            except HTTPException:
                appearance = {"appearance_level": "APPEARANCE_UNVERIFIED",
                              "appearance_coverage": "UNREADABLE",
                              "appearance_note": "模型文件无法解析外观信息"}
            if appearance.get("appearance_level") != "VERIFIED_APPEARANCE" \
                    and not request.accept_unverified_appearance:
                raise HTTPException(409, {
                    "code": "appearance_unverified",
                    "detail": (f"该模型外观未核验（{appearance.get('appearance_coverage')}）："
                               f"{appearance.get('appearance_note')}。"
                               "要求真实外观的正式广告需要先完成材质/几何核验；"
                               "如只做几何预演，请显式传 accept_unverified_appearance=true"),
                    "asset_id": asset["id"],
                    "appearance": appearance,
                })
        shots = []
        for index, shot in enumerate(request.shots, start=1):
            payload = {
                "id": shot.id or f"shot_{index:02d}",
                "name": shot.name or f"场景 {index}",
                "camera": shot.camera,
                "focal_length_mm": shot.focal_length_mm,
                "duration_frames": shot.duration_frames,
            }
            if shot.camera_target_m is not None:
                payload["camera_target_m"] = list(shot.camera_target_m)
            if shot.camera_path is not None:
                payload["camera_path"] = shot.camera_path.model_dump()
            if shot.caption_text:
                payload["caption_text"] = shot.caption_text
            if parsed_scenes:
                payload.update({key: parsed_scenes[index-1][key] for key in
                                ("scene_prompt", "scene_description", "start_frame", "end_frame")})
            shots.append(payload)
        plan_id = str(uuid.uuid4())
        payload = {
            "schema_version": "1.0",
            "product_asset_id": asset["id"],
            "intent": request.intent,
            "output": {
                "width": spec.video.width, "height": spec.video.height, "fps": spec.video.fps,
                "duration_seconds": round(seconds, 3), "frame_count": total_frames,
            },
            "fidelity_mode": "STRICT_REQUESTED",
            "crop_anchor": "center",
            "product_pose": {"position_m": [0.0, 0.0, 0.0], "rotation_xyz_deg": [0.0, 0.0, 0.0], "scale": 1.0},
            "scene": {"template": "studio_product", "background_color": "#0A0A0C", "lighting_preset": "softbox"},
            "shots": shots,
            "production_plan": {
                "render_mode": request.render_mode,
                "profile_id": request.profile_id,
                "profile_version": profile["version"]["version"],
                "profile_payload_sha256": profile["version"]["payload_sha256"],
                "locale": request.locale,
                "voiceover_text": request.voiceover_text,
                "aspect_ratio": spec.composition.aspect_ratio,
            },
            # V6-16：外观状态写进冻结计划，后续打包/发布能据此判断是否可宣称外观保真
            "appearance": (
                {
                    "status": ("VERIFIED_APPEARANCE" if appearance.get("appearance_level") == "VERIFIED_APPEARANCE"
                               else "UNVERIFIED_ACCEPTED"),
                    "coverage": appearance.get("appearance_coverage"),
                    "level": appearance.get("appearance_level"),
                    "note": appearance.get("appearance_note"),
                    "accepted_by_operator": bool(request.accept_unverified_appearance),
                } if appearance is not None else
                {"status": "IMAGE_SOURCE", "coverage": "IMAGE_SOURCE", "level": "NOT_APPLICABLE",
                 "note": "图片素材：外观来自拍摄/渲染原图", "accepted_by_operator": False}
            ),
        }
        if parsed_scenes:
            if spec.video.fps != 24:
                raise HTTPException(422, "H3场景生成当前要求24fps的Profile")
            payload["scene_generation"] = {"version": 1, "reference": scene_reference,
                "mode": "H3_REFERENCE_SCENES", "visual_verification": "NOT_VERIFIED"}
            payload["fidelity_mode"] = "GENERATIVE"
            payload["appearance"] = {"status": "REFERENCE_GUIDED", "source_asset_id": scene_reference["asset_id"],
                "note": "使用原始彩色产品图生成，不使用灰模；生成结果仍需视觉审核，不保证严格保真"}
        validate_production_plan_snapshot(payload, spec)
        _, payload_sha256 = plan_contract_payload_hash(payload)
        product_version_id = create_product_version(asset["id"], owner_id, project_id, payload_sha256, db)
        db.execute("INSERT INTO plans VALUES (?, ?, ?, ?, 0, ?)",
                   (plan_id, asset["id"], payload["intent"],
                    json.dumps(payload, ensure_ascii=False), utc_now()))
        contract = upsert_plan_contract(plan_id, product_version_id, payload, db=db)
    return {
        "id": plan_id, "approved": False, "created_at": utc_now(),
        "contract_id": contract["contract_id"], "product_version_id": product_version_id,
        "intent": payload["intent"], "output": payload["output"], "shots": shots,
        "production_plan": payload["production_plan"],
        "scene_generation": payload.get("scene_generation"),
        "duration_seconds": round(seconds, 3), "total_frames": total_frames,
        "note": "V6 生产计划：按 Profile 规格的多场景计划；批准后即可创建 Run 真实渲染",
    }


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
