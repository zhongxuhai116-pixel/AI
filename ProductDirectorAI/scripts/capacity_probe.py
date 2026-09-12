#!/usr/bin/env python3
"""V2 产能探针：连续提交 N 个 H3 任务，采样 GPU、队列与磁盘增量。

输出可用于排期的数字：单条平均耗时、峰值显存/利用率、每条磁盘增量，
以及按单卡串行推算的日产能（含排队等待）。

用法（在装有 ComfyUI/H3 的节点上，API 与 PG 同源）：
    PRODUCTDIRECTOR_OWNER_TOKEN=<32+> python scripts/capacity_probe.py \
        --image /home/ubuntu/pd-user-assets/user-product-02.png --jobs 3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx


def dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--image", required=True)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--crop", nargs=4, type=int, default=[170, 10, 350, 500])
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()

    token = os.environ["PRODUCTDIRECTOR_OWNER_TOKEN"]
    client = httpx.Client(base_url=args.api, timeout=120.0, headers={"Authorization": f"Bearer {token}"})
    samples: list[tuple[float, int, int]] = []
    stop = threading.Event()

    def sample_gpu() -> None:
        while not stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=15,
                )
                util, mem = (int(part.strip()) for part in out.stdout.strip().split(","))
                samples.append((time.time(), util, mem))
            except Exception:
                pass
            stop.wait(2)

    comfy_output = Path(os.environ.get("PD_COMFY_OUTPUT", "/home/ubuntu/comfy-h3/output"))
    before_disk = dir_size(comfy_output)
    started = time.time()
    sampler = threading.Thread(target=sample_gpu, daemon=True)
    sampler.start()

    image = Path(args.image)
    jobs: list[dict] = []
    for index in range(1, args.jobs + 1):
        asset = client.post(
            "/api/v1/assets", files={"file": (f"capacity-{index}.png", image.read_bytes(), "image/png")}
        ).json()
        submit = client.post(
            "/api/v1/providers/h3/reconstruct",
            json={"product_asset_id": asset["id"], "crop": args.crop, "steps": args.steps, "cfg": 5.0, "seed": 20260912},
        )
        submit.raise_for_status()
        jobs.append({
            "index": index,
            "provider_job_id": submit.json()["provider_job_id"],
            "submitted_at": time.time(),
            "queue_at_submit": client.get("/api/v1/providers/h3/queue").json()["queue_depth"],
        })
        print(f"submitted job {index}: queue_depth={jobs[-1]['queue_at_submit']}", flush=True)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        pending = [job for job in jobs if "finished_at" not in job]
        if not pending:
            break
        for job in pending:
            body = client.get(f"/api/v1/providers/h3/jobs/{job['provider_job_id']}").json()
            if body["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                job.update({
                    "finished_at": time.time(),
                    "status": body["status"],
                    "stage": body["stage"],
                    "artifact_sha256": body.get("artifact_sha256"),
                })
                print(f"  job {job['index']} {body['status']} in {round(job['finished_at']-job['submitted_at'],1)}s", flush=True)
        time.sleep(5)
    stop.set()
    sampler.join(timeout=10)

    after_disk = dir_size(comfy_output)
    finished = [job for job in jobs if job.get("finished_at")]
    durations = [job["finished_at"] - job["submitted_at"] for job in finished]
    wall = time.time() - started
    summary = {
        "jobs_submitted": args.jobs,
        "jobs_finished": len(finished),
        "all_succeeded": all(job["status"] == "SUCCEEDED" for job in finished),
        "wall_seconds": round(wall, 1),
        "per_job_seconds": [round(value, 1) for value in durations],
        "per_job_average_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "peak_gpu_utilization": max((item[1] for item in samples), default=None),
        "peak_memory_mib": max((item[2] for item in samples), default=None),
        "gpu_samples": len(samples),
        "disk_delta_bytes": after_disk - before_disk,
        "disk_per_job_bytes": (after_disk - before_disk) // max(1, len(finished)),
        "queue_depths_at_submit": [job["queue_at_submit"] for job in jobs],
        "jobs": jobs,
    }
    if durations:
        # 单卡串行：按平均单条耗时（含排队）估算日产能
        summary["jobs_per_hour"] = round(3600 / summary["per_job_average_seconds"], 1)
        summary["jobs_per_day"] = round(86400 / summary["per_job_average_seconds"], 1)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_succeeded"] and len(finished) == args.jobs else 1


if __name__ == "__main__":
    raise SystemExit(main())
