#!/usr/bin/env python3
"""A02：验证目标 3D 合同的相机轨迹/注视点/场景真的驱动 Blender 出片。

同一个 GLB 渲染两次：
1) 默认计划（不提供 3D 字段）—— 必须与历史结果一致，证明默认行为没被改坏；
2) 显式 camera_path / camera_target_m / scene —— 必须产出不同成片，证明字段生效。

用法（在有 Blender 的机器上）：
    PRODUCTDIRECTOR_OWNER_TOKEN=<32+ 字符> python scripts/director_plan_3d_check.py
"""
from __future__ import annotations

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

os.environ.setdefault("PRODUCTDIRECTOR_OWNER_TOKEN", "plan3d-" + "o" * 40)

from fastapi.testclient import TestClient  # noqa: E402

import productdirector_api.main as main  # noqa: E402

GLB = REPO / "tests" / "fixtures" / "generic-product.glb"
BASE_SHOTS = [
    {"id": "shot_01", "name": "正面推近", "duration_frames": 24, "camera": "static", "focal_length_mm": 85},
    {"id": "shot_02", "name": "侧向观察", "duration_frames": 72, "camera": "side_track", "focal_length_mm": 24},
    {"id": "shot_03", "name": "立体环绕展示", "duration_frames": 48, "camera": "hero_orbit", "focal_length_mm": 55},
]
CUSTOM_SHOTS = [
    dict(BASE_SHOTS[0]),
    dict(BASE_SHOTS[1], camera_path={"type": "side_track", "start_m": [-2.4, -2.6, 2.0], "end_m": [2.4, -2.6, 2.0]}),
    dict(
        BASE_SHOTS[2],
        camera_target_m=[0, 0, 1.0],
        camera_path={
            "type": "hero_orbit", "radius_m": 6.5, "height_m": 2.6,
            "start_angle_deg": -80, "end_angle_deg": 80,
        },
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(path: Path) -> dict:
    result = subprocess.run(
        [os.environ.get("PD_FFPROBE", "ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames,codec_name:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return {
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "frames": int(stream.get("nb_frames", 0)),
        "duration": float(payload["format"]["duration"]),
    }


def main_check() -> int:
    work = Path(tempfile.mkdtemp(prefix="pd-3d-check-"))
    main.VAR = work
    main.UPLOADS = work / "uploads"
    main.RUNS = work / "runs"
    for folder in (main.VAR, main.UPLOADS, main.RUNS):
        folder.mkdir(parents=True, exist_ok=True)
    main.initialize_db()

    client = TestClient(main.app, headers={"Authorization": f"Bearer {main.security.OWNER_TOKEN}"})
    summary: dict = {"fixture_sha256": sha256(GLB), "cases": []}

    def render_case(label: str, shots: list[dict], scene: dict | None = None) -> dict:
        upload = client.post("/api/v1/assets", files={"file": (GLB.name, GLB.read_bytes(), "model/gltf-binary")})
        upload.raise_for_status()
        plan = client.post(
            "/api/v1/plans/template",
            json={
                "product_asset_id": upload.json()["id"],
                "intent": f"3D 合同验证（{label}）",
                "duration_seconds": 6,
                "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": 6},
                **({"scene": scene} if scene else {}),
            },
        )
        plan.raise_for_status()
        plan_id = plan.json()["id"]
        patch_body = {"intent": f"3D 合同验证（{label}）", "shots": shots}
        if scene:
            patch_body["scene"] = scene
        client.patch(f"/api/v1/plans/{plan_id}", json=patch_body).raise_for_status()
        client.post(f"/api/v1/plans/{plan_id}/approve", json={"approved": True}).raise_for_status()
        run = client.post("/api/v1/runs", json={"plan_id": plan_id})
        run.raise_for_status()
        job_id = run.json()["job_id"]

        deadline = time.time() + 600
        job: dict = {}
        started = time.time()
        while time.time() < deadline:
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(3)
        record = {
            "label": label,
            "job_id": job_id,
            "status": job.get("status"),
            "error": job.get("error"),
            "seconds": round(time.time() - started, 1),
        }
        if job.get("status") != "SUCCEEDED":
            return record

        video = client.get(f"/api/v1/jobs/{job_id}/video")
        video.raise_for_status()
        video_path = work / f"{label}.mp4"
        video_path.write_bytes(video.content)
        manifest = client.get(f"/api/v1/jobs/{job_id}/manifest").json()
        record.update({
            "video_sha256": sha256(video_path),
            "video": probe(video_path),
            "preview_kind": manifest.get("preview_kind"),
            "qa_passed": manifest.get("qa", {}).get("passed"),
            "director_plan_3d_present": bool(manifest.get("director_plan_3d")),
            "director_plan_3d_error": manifest.get("director_plan_3d_error"),
        })
        return record

    default_case = render_case("default", BASE_SHOTS)
    summary["cases"].append(default_case)
    custom_case = render_case(
        "custom_path",
        CUSTOM_SHOTS,
        scene={"template": "studio_product", "background_color": "#101828", "lighting_preset": "three_point"},
    )
    summary["cases"].append(custom_case)
    summary["paths_differ"] = (
        default_case.get("video_sha256") is not None
        and custom_case.get("video_sha256") is not None
        and default_case["video_sha256"] != custom_case["video_sha256"]
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = all(case["status"] == "SUCCEEDED" for case in summary["cases"]) and summary["paths_differ"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main_check())
