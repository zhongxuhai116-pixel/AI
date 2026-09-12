#!/usr/bin/env python3
"""A03：让真实 API 运行在 PostgreSQL 上，并完成一次真实出片。

与迁移演练不同，本脚本验证的是**应用层**：`PRODUCTDIRECTOR_DATABASE_URL`
指向 PostgreSQL 后，FastAPI 用 PostgreSQL 存任务状态，同时仍然用真实的
FFmpeg 产出视频。运行时数据（素材/成片）写到临时目录，不污染线上 var/。

用法：
    PD_PG_SMOKE_DSN="postgresql://user:pw@127.0.0.1:5432/db" \
      python scripts/pg_app_smoke.py

依赖：psycopg（apps/api/requirements-migrate.txt）、Pillow、httpx。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "api"))

DSN = os.environ.get("PD_PG_SMOKE_DSN", "")
if not DSN:
    print("需要设置 PD_PG_SMOKE_DSN", file=sys.stderr)
    raise SystemExit(2)

os.environ["PRODUCTDIRECTOR_DATABASE_URL"] = DSN
os.environ.setdefault("PRODUCTDIRECTOR_OWNER_TOKEN", "pg-smoke-" + "o" * 40)

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import productdirector_api.main as main  # noqa: E402


def make_fixture(path: Path) -> None:
    width, height = 1200, 1600
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line([(0, y), (width, y)], fill=(20 + y * 30 // height, 24 + y * 20 // height, 40 + y * 40 // height))
    draw.rectangle([180, 380, 1020, 1180], outline=(255, 138, 42), width=12)
    draw.ellipse([520, 700, 680, 860], fill=(255, 138, 42))
    draw.text((200, 1250), "PD POSTGRES SMOKE FIXTURE", fill=(240, 240, 246))
    image.save(path, format="PNG")


def main_smoke() -> int:
    work = Path(tempfile.mkdtemp(prefix="pd-pg-smoke-"))
    main.VAR = work
    main.UPLOADS = work / "uploads"
    main.RUNS = work / "runs"
    for folder in (main.VAR, main.UPLOADS, main.RUNS):
        folder.mkdir(parents=True, exist_ok=True)
    main.initialize_db()

    fixture = work / "fixture.png"
    make_fixture(fixture)

    client = TestClient(main.app, headers={"Authorization": f"Bearer {main.security.OWNER_TOKEN}"})
    summary: dict = {
        "backend": "postgresql",
        "work_dir": str(work),
        "health": client.get("/api/v1/health").json(),
    }

    upload = client.post(
        "/api/v1/assets", files={"file": ("fixture.png", fixture.read_bytes(), "image/png")}
    )
    upload.raise_for_status()
    asset = upload.json()
    summary["asset_id"] = asset["id"]

    plan = client.post(
        "/api/v1/plans/template",
        json={
            "product_asset_id": asset["id"],
            "intent": "PostgreSQL 冒烟：真实 FFmpeg 出片",
            "duration_seconds": 6,
            "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": 6},
            "crop_anchor": "center",
        },
    )
    plan.raise_for_status()
    plan_id = plan.json()["id"]
    shots = [
        {"id": "shot_01", "name": "正面推近", "duration_frames": 24, "camera": "static", "focal_length_mm": 85},
        {"id": "shot_02", "name": "侧向观察", "duration_frames": 72, "camera": "side_track", "focal_length_mm": 24},
        {"id": "shot_03", "name": "细节定格", "duration_frames": 48, "camera": "hero_orbit", "focal_length_mm": 55},
    ]
    client.patch(f"/api/v1/plans/{plan_id}", json={"intent": "PostgreSQL 冒烟", "shots": shots}).raise_for_status()
    client.post(f"/api/v1/plans/{plan_id}/approve", json={"approved": True}).raise_for_status()

    run = client.post("/api/v1/runs", json={"plan_id": plan_id, "idempotency_key": "pg-smoke"})
    run.raise_for_status()
    job_id = run.json()["job_id"]
    summary["job_id"] = job_id

    # PostgreSQL 上的幂等：同键再来一次必须复用同一个 run。
    again = client.post("/api/v1/runs", json={"plan_id": plan_id, "idempotency_key": "pg-smoke"})
    again.raise_for_status()
    summary["idempotent_reuse"] = (
        again.json()["run_id"] == run.json()["run_id"] and not again.json()["created"]
    )

    deadline = time.time() + 300
    job: dict = {}
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(2)
    summary["job"] = {"status": job.get("status"), "stage": job.get("stage"), "error": job.get("error")}
    if job.get("status") != "SUCCEEDED":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    video = client.get(f"/api/v1/jobs/{job_id}/video")
    video.raise_for_status()
    video_path = work / "preview.mp4"
    video_path.write_bytes(video.content)
    probe = subprocess.run(
        [os.environ.get("PD_FFPROBE", "ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,codec_name:format=duration",
         "-of", "json", str(video_path)],
        capture_output=True, text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    summary["video"] = {
        "bytes": video_path.stat().st_size,
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "frames": int(stream.get("nb_frames", 0)),
        "duration": float(json.loads(probe.stdout)["format"]["duration"]),
    }
    manifest = client.get(f"/api/v1/jobs/{job_id}/manifest").json()
    summary["manifest"] = {
        "qa_passed": manifest.get("qa", {}).get("passed"),
        "plan_snapshot_sha256": manifest.get("director_plan_snapshot_sha256"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_smoke())
