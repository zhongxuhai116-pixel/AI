from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.request
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator


ROOT = Path(__file__).resolve().parents[3]
VAR = ROOT / "var"
UPLOADS = VAR / "uploads"
RUNS = VAR / "runs"
DB_PATH = VAR / "productdirector.db"
BLENDER_SCRIPT = ROOT / "blender" / "scripts" / "render_product.py"
for folder in (VAR, UPLOADS, RUNS):
    folder.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db() -> None:
    with connect() as db:
        db.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS plans (
              id TEXT PRIMARY KEY,
              product_asset_id TEXT NOT NULL,
              intent TEXT NOT NULL,
              payload TEXT NOT NULL,
              approved INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY(product_asset_id) REFERENCES assets(id)
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
            CREATE TABLE IF NOT EXISTS provider_credentials (
              provider TEXT PRIMARY KEY,
              encrypted_key BLOB NOT NULL,
              base_url TEXT NOT NULL,
              last_status TEXT,
              last_message TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )


initialize_db()
processes: dict[str, subprocess.Popen] = {}
process_lock = threading.Lock()


DEFAULT_OUTPUT_WIDTH = 540
DEFAULT_OUTPUT_HEIGHT = 960
DEFAULT_FPS = 24
DEFAULT_DURATION_SECONDS = 6


class Shot(BaseModel):
    id: str
    name: str
    duration_frames: int = Field(ge=24, le=144)
    camera: Literal["dolly_in", "side_track", "hero_orbit", "static"]
    focal_length_mm: int = Field(ge=15, le=120)


class OutputSpec(BaseModel):
    width: Literal[540, 1080] = DEFAULT_OUTPUT_WIDTH
    height: Literal[960, 1920] = DEFAULT_OUTPUT_HEIGHT
    fps: Literal[24] = DEFAULT_FPS
    duration_seconds: Literal[6] = DEFAULT_DURATION_SECONDS

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
    duration_seconds: Literal[6] = 6
    output: OutputSpec = Field(default_factory=OutputSpec)

    @model_validator(mode="after")
    def validate_output_duration(self):
        if self.duration_seconds != self.output.duration_seconds:
            raise ValueError("duration_seconds 与 output.duration_seconds 必须保持一致")
        return self


class PlanApproval(BaseModel):
    approved: bool = True


class PlanUpdate(BaseModel):
    intent: str = Field(min_length=1, max_length=4000)
    shots: list[Shot] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_total_duration(self):
        if sum(shot.duration_frames for shot in self.shots) != 144:
            raise ValueError("三个镜头的总时长必须恰好为 144 帧（6 秒）")
        return self


class RunRequest(BaseModel):
    plan_id: str


class ProviderCredentialInput(BaseModel):
    api_key: str = Field(min_length=20, max_length=512)


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def protect_secret(value: str) -> bytes:
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


def minimax_call(api_key: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"https://api.minimaxi.com{path}",
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
        "base_url": "https://api.minimaxi.com/v1",
        "configured": bool(row),
        "last_status": row["last_status"] if row else "NOT_CONFIGURED",
        "last_message": row["last_message"] if row else "尚未配置 MiniMax API Key",
        "updated_at": row["updated_at"] if row else None,
    }


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def update_job(job_id: str, **values) -> None:
    values["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        db.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            [*values.values(), job_id],
        )


def get_job(job_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    return row_to_dict(row)


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
        {"intent": plan_snapshot.get("intent"), "shots": plan_snapshot.get("shots")}
    )
    sources = "".join(f"[source_{index}]" for index in range(3))
    preview_width = int(round(output.width * 64 / 27))
    preview_height = int(round(output.height * 64 / 27))
    filters = [
        f"[0:v]scale={preview_width}:{preview_height}:force_original_aspect_ratio=increase,"
        f"crop={preview_width}:{preview_height}:(iw-ow)/2:(ih-oh)/2,split=3" + sources,
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
        "--frames", "144",
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
            rendered = min(144, rendered + 1)
            if rendered % 4 == 0:
                update_job(job_id, progress=12 + int(rendered / 144 * 70))
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
def execute_job(job_id: str) -> None:
    try:
        job = get_job(job_id)
        with connect() as db:
            asset_row = db.execute("SELECT * FROM assets WHERE id = ?", (job["asset_id"],)).fetchone()
            plan_row = db.execute("SELECT * FROM plans WHERE id = ?", (job["plan_id"],)).fetchone()
        if not asset_row or not plan_row:
            raise RuntimeError("任务输入不存在")
        asset = row_to_dict(asset_row)
        run_dir = RUNS / job_id
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
        if not 5.8 <= duration <= 6.2:
            raise RuntimeError(f"视频技术检查失败: 时长不满足 6 秒: {duration:.3f}s")
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
            "director_plan": plan_snapshot,
            "director_plan_snapshot_sha256": hashlib.sha256(plan_snapshot_bytes).hexdigest(),
            "blender": BLENDER,
            "ffmpeg": FFMPEG,
            "created_at": utc_now(),
        }
        manifest_path = run_dir / "metadata.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        update_job(
            job_id,
            status="SUCCEEDED",
            stage="ARTIFACT",
            progress=100,
            output_path=str(output),
            manifest_path=str(manifest_path),
            error=None,
        )
    except Exception as exc:
        if get_job(job_id)["status"] != "CANCELLED":
            update_job(job_id, status="FAILED", stage="FAILED", error=str(exc)[-4000:])


app = FastAPI(title="ProductDirectorAI V1", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
def save_minimax_credentials(request: ProviderCredentialInput) -> dict:
    status, response = minimax_call(request.api_key, "/v1/models")
    if status != 200 or not response.get("data"):
        message = response.get("error", {}).get("message", "密钥验证失败")
        raise HTTPException(status, message)
    encrypted = protect_secret(request.api_key)
    now = utc_now()
    message = f"认证通过，可访问 {len(response['data'])} 个模型"
    with connect() as db:
        db.execute(
            """INSERT INTO provider_credentials(provider, encrypted_key, base_url, last_status, last_message, updated_at)
               VALUES('minimax', ?, ?, 'AUTHENTICATED', ?, ?)
               ON CONFLICT(provider) DO UPDATE SET encrypted_key=excluded.encrypted_key,
               base_url=excluded.base_url, last_status=excluded.last_status,
               last_message=excluded.last_message, updated_at=excluded.updated_at""",
            (encrypted, "https://api.minimaxi.com/v1", message, now),
        )
    return provider_public_status()


@app.post("/api/v1/providers/minimax/test")
def test_minimax_generation() -> dict:
    row = provider_row()
    if not row:
        raise HTTPException(409, "请先保存 MiniMax API Key")
    api_key = unprotect_secret(row["encrypted_key"])
    status, response = minimax_call(
        api_key,
        "/v1/chat/completions",
        {
            "model": "MiniMax-M2.7",
            "messages": [{"role": "user", "content": "只回复：连接成功"}],
            "max_completion_tokens": 64,
            "temperature": 0.1,
        },
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
    mime = mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    created = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, file.filename or stored.name, kind, mime, len(data), digest, str(stored), created),
        )
    return {
        "id": asset_id,
        "name": file.filename,
        "kind": kind,
        "mime": mime,
        "size_bytes": len(data),
        "sha256": digest,
        "created_at": created,
    }


@app.get("/api/v1/assets")
def list_assets() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM assets ORDER BY created_at DESC").fetchall()
    return [row_to_dict(row) for row in rows]


@app.get("/api/v1/assets/{asset_id}/content")
def asset_content(asset_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "素材不存在")
    return FileResponse(row["path"], media_type=row["mime"], filename=row["name"])


@app.post("/api/v1/plans/template", status_code=201)
def create_plan(request: PlanRequest) -> dict:
    with connect() as db:
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (request.product_asset_id,)).fetchone()
    if not asset:
        raise HTTPException(404, "产品素材不存在")
    cameras = ["dolly_in", "side_track", "hero_orbit" if asset["kind"] == "model" else "static"]
    names = ["正面推近", "侧向观察", "立体环绕展示" if asset["kind"] == "model" else "细节定格"]
    shots = [
        Shot(id=f"shot_0{index + 1}", name=names[index], duration_frames=48, camera=cameras[index], focal_length_mm=35)
        for index in range(3)
    ]
    payload = {
        "schema_version": "1.0",
        "product_asset_id": request.product_asset_id,
        "intent": request.intent,
        "output": request.output.model_dump(),
        "fidelity_mode": "STRICT_REQUESTED",
        "shots": [shot.model_dump() for shot in shots],
    }
    plan_id = str(uuid.uuid4())
    created = utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, 0, ?)",
            (plan_id, request.product_asset_id, request.intent, json.dumps(payload, ensure_ascii=False), created),
        )
    return {"id": plan_id, "approved": False, "created_at": created, **payload}


@app.post("/api/v1/plans/{plan_id}/approve")
def approve_plan(plan_id: str, request: PlanApproval) -> dict:
    with connect() as db:
        current = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not current:
            raise HTTPException(404, "计划不存在")
        db.execute("UPDATE plans SET approved = ? WHERE id = ?", (int(request.approved), plan_id))
    return {"id": plan_id, "approved": request.approved}


@app.patch("/api/v1/plans/{plan_id}")
def update_plan(plan_id: str, request: PlanUpdate) -> dict:
    with connect() as db:
        current = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not current:
            raise HTTPException(404, "计划不存在")
        payload = json.loads(current["payload"])
        payload["intent"] = request.intent
        payload["shots"] = [shot.model_dump() for shot in request.shots]
        db.execute(
            "UPDATE plans SET intent = ?, payload = ?, approved = 0 WHERE id = ?",
            (request.intent, json.dumps(payload, ensure_ascii=False), plan_id),
        )
    return {"id": plan_id, "approved": False, "created_at": current["created_at"], **payload}


@app.post("/api/v1/runs", status_code=202)
def create_run(request: RunRequest, background: BackgroundTasks) -> dict:
    with connect() as db:
        plan = db.execute("SELECT * FROM plans WHERE id = ?", (request.plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "计划不存在")
        if not plan["approved"]:
            raise HTTPException(409, "请先确认分镜计划")
        asset = db.execute("SELECT * FROM assets WHERE id = ?", (plan["product_asset_id"],)).fetchone()
        if not asset:
            raise HTTPException(409, "计划绑定的产品素材不存在")
        try:
            plan_snapshot = json.loads(plan["payload"])
            PlanUpdate.model_validate({"intent": plan_snapshot["intent"], "shots": plan_snapshot["shots"]})
        except (KeyError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(409, f"计划快照无效，无法启动任务: {exc}") from exc
        job_id = str(uuid.uuid4())
        created = utc_now()
        run_dir = RUNS / job_id
        run_dir.mkdir(parents=True, exist_ok=False)
        plan_path = run_dir / "director_plan.json"
        plan_path.write_text(json.dumps(plan_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        db.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, 'QUEUED', 'PREPARE', 0, NULL, NULL, NULL, 0, ?, ?)",
            (job_id, request.plan_id, asset["id"], asset["kind"], created, created),
        )
    background.add_task(execute_job, job_id)
    return {"job_id": job_id, "status": "QUEUED", "status_url": f"/api/v1/jobs/{job_id}"}


@app.get("/api/v1/jobs")
def list_jobs() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [row_to_dict(row) for row in rows]


@app.get("/api/v1/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    return get_job(job_id)


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = get_job(job_id)
    if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        return job
    update_job(job_id, cancel_requested=1, status="CANCEL_REQUESTED")
    with process_lock:
        process = processes.get(job_id)
        if process and process.poll() is None:
            process.terminate()
    return get_job(job_id)


@app.get("/api/v1/jobs/{job_id}/video")
def job_video(job_id: str):
    job = get_job(job_id)
    if job["status"] != "SUCCEEDED" or not job["output_path"]:
        raise HTTPException(409, "视频尚未准备完成")
    return FileResponse(job["output_path"], media_type="video/mp4", filename=f"product-preview-{job_id}.mp4")


@app.get("/api/v1/jobs/{job_id}/manifest")
def job_manifest(job_id: str):
    job = get_job(job_id)
    if job["status"] != "SUCCEEDED" or not job["manifest_path"]:
        raise HTTPException(409, "清单尚未准备完成")
    return FileResponse(job["manifest_path"], media_type="application/json", filename=f"metadata-{job_id}.json")
