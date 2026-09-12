#!/usr/bin/env python3
"""对同一个 GLB 连续跑 N 次真实 3D 作业，记录耗时、哈希、质量门与分镜语义。

用于 A07 的"同一 GLB 三次真实作业"出口门，也可用于回归对比。

用法：
    PRODUCTDIRECTOR_OWNER_TOKEN=<32+ 字符> \
      python scripts/glb_repeat_check.py --glb /home/ubuntu/pd-recon/robot-front-v2.glb --runs 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "api"))

os.environ.setdefault("PRODUCTDIRECTOR_OWNER_TOKEN", "repeat-" + "o" * 40)

from fastapi.testclient import TestClient  # noqa: E402

import productdirector_api.main as main  # noqa: E402

SHOTS = [
    {"id": "shot_01", "name": "正面推近", "duration_frames": 24, "camera": "static", "focal_length_mm": 85},
    {"id": "shot_02", "name": "侧向观察", "duration_frames": 72, "camera": "side_track", "focal_length_mm": 24},
    {"id": "shot_03", "name": "立体环绕展示", "duration_frames": 48, "camera": "hero_orbit", "focal_length_mm": 55},
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main_check() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glb", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--resolution", default="1080x1920")
    args = parser.parse_args()

    glb = Path(args.glb)
    width, height = (int(part) for part in args.resolution.split("x"))
    work = Path(tempfile.mkdtemp(prefix="pd-glb-repeat-"))
    main.VAR = work
    main.UPLOADS = work / "uploads"
    main.RUNS = work / "runs"
    for folder in (main.VAR, main.UPLOADS, main.RUNS):
        folder.mkdir(parents=True, exist_ok=True)
    main.initialize_db()

    client = TestClient(main.app, headers={"Authorization": f"Bearer {main.security.OWNER_TOKEN}"})
    summary: dict = {"glb": str(glb), "glb_sha256": sha256(glb), "glb_bytes": glb.stat().st_size, "runs": []}

    for index in range(1, args.runs + 1):
        upload = client.post("/api/v1/assets", files={"file": (glb.name, glb.read_bytes(), "model/gltf-binary")})
        record: dict = {"run": index, "upload_status": upload.status_code}
        if upload.status_code != 201:
            record["error"] = upload.text[:400]
            summary["runs"].append(record)
            continue
        asset = upload.json()
        record["glb_structure"] = asset.get("glb")
        plan = client.post(
            "/api/v1/plans/template",
            json={
                "product_asset_id": asset["id"],
                "intent": f"同一 GLB 第 {index} 次真实作业",
                "duration_seconds": 6,
                "output": {"width": width, "height": height, "fps": 24, "duration_seconds": 6},
            },
        )
        plan.raise_for_status()
        plan_id = plan.json()["id"]
        client.patch(f"/api/v1/plans/{plan_id}", json={"intent": f"同一 GLB 第 {index} 次", "shots": SHOTS}).raise_for_status()
        client.post(f"/api/v1/plans/{plan_id}/approve", json={"approved": True}).raise_for_status()

        started = time.time()
        run = client.post("/api/v1/runs", json={"plan_id": plan_id})
        run.raise_for_status()
        job_id = run.json()["job_id"]
        record["job_id"] = job_id
        record["seconds"] = round(time.time() - started, 1)

        job = client.get(f"/api/v1/jobs/{job_id}").json()
        record["status"] = job["status"]
        record["error"] = job.get("error")
        if job["status"] == "SUCCEEDED":
            video = client.get(f"/api/v1/jobs/{job_id}/video")
            video.raise_for_status()
            video_path = work / f"run{index}.mp4"
            video_path.write_bytes(video.content)
            manifest = client.get(f"/api/v1/jobs/{job_id}/manifest").json()
            probe = subprocess.run(
                [os.environ.get("PD_FFPROBE", "ffprobe"), "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,nb_frames,codec_name:format=duration",
                 "-of", "json", str(video_path)],
                capture_output=True, text=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            record["video"] = {
                "bytes": video_path.stat().st_size,
                "sha256": sha256(video_path),
                "codec": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "frames": int(stream.get("nb_frames", 0)),
                "duration": float(json.loads(probe.stdout)["format"]["duration"]),
            }
            record["qa_passed"] = manifest.get("qa", {}).get("passed")
            record["plan_snapshot_sha256"] = manifest.get("director_plan_snapshot_sha256")
            record["director_plan_3d_present"] = bool(manifest.get("director_plan_3d"))
        summary["runs"].append(record)

    hashes = [item.get("video", {}).get("sha256") for item in summary["runs"] if item.get("video")]
    summary["all_succeeded"] = all(item.get("status") == "SUCCEEDED" for item in summary["runs"])
    summary["identical_outputs"] = len(set(hashes)) == 1 and len(hashes) == len(summary["runs"])
    summary["distinct_hashes"] = len(set(hashes))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main_check())
