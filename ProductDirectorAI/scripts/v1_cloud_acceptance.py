#!/usr/bin/env python3
"""V1 云端真实验收：图片链路 + GLB/Blender 链路。

不做 mock：真实 HTTP 会话与 CSRF、真实素材上传、真实后台作业、真实 FFmpeg /
Blender 出片、真实产物下载，并用 ffprobe 与哈希校验产物。

用法（在服务器上，本机访问回环服务）：

    sudo -n bash -c 'set -a; . /etc/productdirector/v1.env; set +a; \
        PD_OWNER_TOKEN="$PRODUCTDIRECTOR_OWNER_TOKEN" \
        /home/ubuntu/AI/ProductDirectorAI/.venv/bin/python \
        /home/ubuntu/AI/ProductDirectorAI/scripts/v1_cloud_acceptance.py'

环境变量：
    PD_BASE_URL   默认 http://127.0.0.1:8000
    PD_ORIGIN     默认 http://127.0.0.1:4173（必须在服务端允许列表内）
    PD_OWNER_TOKEN  必填，服务端 Owner 访问密钥
    PD_OUT_DIR    默认 <repo>/var/acceptance/<UTC 时间戳>
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get("PD_BASE_URL", "http://127.0.0.1:8000")
ORIGIN = os.environ.get("PD_ORIGIN", "http://127.0.0.1:4173")
FFMPEG = os.environ.get("PD_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("PD_FFPROBE", "ffprobe")
GLB_FIXTURE = REPO / "tests" / "fixtures" / "generic-product.glb"
JOB_TIMEOUT = int(os.environ.get("PD_JOB_TIMEOUT", "1200"))
# 可选：用用户提供的真实产品图替换程序生成的夹具图（该文件不进入 Git）。
IMAGE_OVERRIDE = os.environ.get("PD_IMAGE_PATH", "")
CROP_ANCHOR = os.environ.get("PD_CROP_ANCHOR", "center")

SHOTS = [
    {"id": "shot_01", "name": "正面推近", "duration_frames": 24, "camera": "static", "focal_length_mm": 85},
    {"id": "shot_02", "name": "侧向观察", "duration_frames": 72, "camera": "side_track", "focal_length_mm": 24},
    {"id": "shot_03", "name": "立体环绕展示", "duration_frames": 48, "camera": "hero_orbit", "focal_length_mm": 55},
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_png_fixture(path: Path) -> None:
    width, height = 1200, 1600
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line([(0, y), (width, y)], fill=(14 + y * 40 // height, 18 + y * 26 // height, 34 + y * 60 // height))
    draw.rectangle([160, 360, 1040, 1180], outline=(255, 138, 42), width=14)
    draw.rectangle([300, 520, 900, 1020], fill=(230, 230, 236), outline=(90, 96, 110), width=6)
    draw.ellipse([520, 700, 680, 860], fill=(255, 138, 42))
    draw.text((180, 1240), "PD V1 CLOUD ACCEPTANCE FIXTURE", fill=(238, 240, 246))
    draw.text((180, 1290), "generated / non-product material", fill=(150, 156, 170))
    image.save(path, format="PNG")


def probe(path: Path) -> dict:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames,codec_name:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return {
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "r_frame_rate": stream.get("r_frame_rate"),
        "nb_frames": int(stream.get("nb_frames", 0)),
        "duration": float(payload["format"]["duration"]),
    }


def boundary_frames(video: Path, out_dir: Path, prefix: str) -> dict:
    """抽取三段镜头边界帧；文件名带路径前缀，避免图片/GLB 两条链路互相覆盖。"""
    frames: dict[str, str] = {}
    for label, frame in [("f001", 1), ("f024", 24), ("f025", 25), ("f096", 96), ("f097", 97), ("f144", 144)]:
        target = out_dir / f"{prefix}-{label}.png"
        result = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{frame - 1})",
             "-frames:v", "1", str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-2000:])
        frames[label] = sha256(target)
    return frames


class Acceptance:
    def __init__(self, token: str, out_dir: Path) -> None:
        self.client = httpx.Client(base_url=BASE, timeout=180.0)
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        session = self.client.post("/api/v1/session", json={"token": token}, headers={"Origin": ORIGIN})
        session.raise_for_status()
        self.headers = {"Origin": ORIGIN, "X-CSRF-Token": session.json()["csrf_token"]}

    def run_path(self, label: str, filename: str, payload: bytes, mime: str, expect_kind: str) -> dict:
        upload = self.client.post(
            "/api/v1/assets", headers=self.headers, files={"file": (filename, payload, mime)}
        )
        upload.raise_for_status()
        asset = upload.json()
        plan = self.client.post(
            "/api/v1/plans/template",
            headers=self.headers,
            json={
                "product_asset_id": asset["id"],
                "intent": f"V1 云端验收（{label}）",
                "duration_seconds": 6,
                "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": 6},
                "crop_anchor": CROP_ANCHOR,
            },
        )
        plan.raise_for_status()
        plan_id = plan.json()["id"]
        shots = [dict(shot) for shot in SHOTS]
        if expect_kind != "model":
            # 图片路径没有物理 3D 环绕，第三镜改名，避免过度声明。
            shots[2] = dict(shots[2], name="细节定格")
        edited = self.client.patch(
            f"/api/v1/plans/{plan_id}",
            headers=self.headers,
            json={
                "intent": f"V1 云端验收（{label}，三段分镜已编辑）",
                "shots": shots,
                "crop_anchor": CROP_ANCHOR,
            },
        )
        edited.raise_for_status()
        self.client.post(f"/api/v1/plans/{plan_id}/approve", headers=self.headers, json={"approved": True}).raise_for_status()
        run = self.client.post("/api/v1/runs", headers=self.headers, json={"plan_id": plan_id})
        run.raise_for_status()
        job_id = run.json()["job_id"]

        started = time.time()
        job: dict = {}
        while time.time() - started < JOB_TIMEOUT:
            job = self.client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(3)
        elapsed = round(time.time() - started, 1)

        record = {
            "label": label,
            "asset": {"id": asset["id"], "kind": asset["kind"], "sha256": asset["sha256"]},
            "plan_id": plan_id,
            "job_id": job_id,
            "job": {"status": job.get("status"), "stage": job.get("stage"), "progress": job.get("progress"), "error": job.get("error")},
            "seconds_observed": elapsed,
        }
        if job.get("status") != "SUCCEEDED":
            return record

        video = self.client.get(f"/api/v1/jobs/{job_id}/video")
        video.raise_for_status()
        video_path = self.out_dir / f"{label}.mp4"
        video_path.write_bytes(video.content)
        manifest = self.client.get(f"/api/v1/jobs/{job_id}/manifest")
        manifest.raise_for_status()
        manifest_path = self.out_dir / f"{label}-manifest.json"
        manifest_path.write_bytes(manifest.content)
        manifest_body = json.loads(manifest_path.read_text(encoding="utf-8"))

        frames = boundary_frames(video_path, self.out_dir, label)
        record.update({
            "video": {"bytes": video_path.stat().st_size, "sha256": sha256(video_path), **probe(video_path)},
            "manifest": {
                "sha256": sha256(manifest_path),
                "preview_kind": manifest_body.get("preview_kind"),
                "plan_snapshot_sha256": manifest_body.get("director_plan_snapshot_sha256"),
                "shots": manifest_body.get("director_plan", {}).get("shots"),
            },
            "boundary_frame_sha256": frames,
            "distinct_boundary_frames": len(set(frames.values())),
        })
        return record


def main() -> int:
    token = os.environ.get("PD_OWNER_TOKEN", "")
    if not token:
        print("PD_OWNER_TOKEN 未设置", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(os.environ.get("PD_OUT_DIR", REPO / "var" / "acceptance" / stamp))
    out_dir.mkdir(parents=True, exist_ok=True)
    if IMAGE_OVERRIDE:
        source = Path(IMAGE_OVERRIDE)
        if not source.is_file():
            print(f"PD_IMAGE_PATH 不存在: {source}", file=sys.stderr)
            return 2
        image_path = out_dir / f"product{source.suffix.lower()}"
        image_path.write_bytes(source.read_bytes())
        image_mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        image_label = source.name
    else:
        image_path = out_dir / "fixture-product.png"
        make_png_fixture(image_path)
        image_mime = "image/png"
        image_label = "fixture-product.png"

    acceptance = Acceptance(token, out_dir)
    summary: dict = {
        "generated_at": stamp,
        "base_url": BASE,
        "out_dir": str(out_dir),
        "health": acceptance.client.get("/api/v1/health").json(),
        "inputs": {
            "image": {
                "label": image_label,
                "source": IMAGE_OVERRIDE or "generated-fixture",
                "sha256": sha256(image_path),
                "bytes": image_path.stat().st_size,
            },
            "glb": {"label": GLB_FIXTURE.name, "sha256": sha256(GLB_FIXTURE)},
        },
        "crop_anchor": CROP_ANCHOR,
        "paths": [],
    }
    summary["paths"].append(
        acceptance.run_path("image", image_label, image_path.read_bytes(), image_mime, "image")
    )
    summary["paths"].append(
        acceptance.run_path("glb", GLB_FIXTURE.name, GLB_FIXTURE.read_bytes(), "model/gltf-binary", "model")
    )

    result_file = out_dir / "acceptance.json"
    result_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nacceptance.json: {result_file}")
    ok = all(path["job"]["status"] == "SUCCEEDED" for path in summary["paths"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
