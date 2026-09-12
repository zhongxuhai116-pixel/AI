#!/usr/bin/env python3
"""A04 真实中断恢复演练：渲染途中杀掉 Worker，验证任务能被重新领取并完成。

流程：
1. 通过真实 API 建素材/计划/作业（GLB，真实 Blender 渲染）；
2. 启动独立 Worker 进程（`python -m productdirector_api.main worker --once`）领取任务；
3. 渲染进行中 `SIGKILL` 掉该进程（模拟机器/进程崩溃，不给它任何收尾机会）；
4. 等待租约过期，观察任务状态；
5. 启动第二个 Worker：应当重新领取（epoch 变大）并完成到 SUCCEEDED；
6. 输出时间线与事件，供验收记录使用。

用法（在装有 Blender 的机器、且 API 服务同库运行时）：
    PRODUCTDIRECTOR_OWNER_TOKEN=<32+> PD_API=http://127.0.0.1:8000 \
      python scripts/kill_worker_drill.py --glb tests/fixtures/generic-product.glb
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.environ.get("PD_API", ""), help="留空则自建一个禁用了内联执行器的 API 进程")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--glb", required=True)
    parser.add_argument("--kill-after", type=float, default=15.0, help="领取后多少秒杀掉 Worker")
    parser.add_argument("--lease-wait", type=float, default=75.0, help="等待租约过期")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    token = os.environ["PRODUCTDIRECTOR_OWNER_TOKEN"]
    api_process = None
    if not args.api:
        # 自建一个"不进程内执行"的 API：任务必须由独立 Worker 领取，才能演练杀进程。
        api_env = {
            **os.environ,
            "PRODUCTDIRECTOR_DISABLE_INLINE_EXECUTOR": "1",
            "PYTHONPATH": os.path.join(args.repo, "apps", "api"),
        }
        api_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "productdirector_api.main:app",
             "--host", "127.0.0.1", "--port", str(args.port)],
            cwd=args.repo, env=api_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        args.api = f"http://127.0.0.1:{args.port}"
        for _ in range(60):
            time.sleep(1)
            try:
                probe = httpx.get(f"{args.api}/openapi.json", timeout=5)
                if probe.status_code in (200, 401):
                    break
            except httpx.HTTPError:
                continue
    client = httpx.Client(base_url=args.api, timeout=120.0, headers={"Authorization": f"Bearer {token}"})
    glb = Path(args.glb)
    timeline: list[dict] = []

    def note(event: str, **extra) -> None:
        entry = {"t": round(time.time() - started, 1), "event": event, **extra}
        timeline.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    started = time.time()
    asset = client.post("/api/v1/assets", files={"file": (glb.name, glb.read_bytes(), "model/gltf-binary")}).json()
    plan = client.post(
        "/api/v1/plans/template",
        json={
            "product_asset_id": asset["id"],
            "intent": "A04 杀 Worker 恢复演练",
            "duration_seconds": 6,
            "output": {"width": 1080, "height": 1920, "fps": 24, "duration_seconds": 6},
        },
    ).json()
    client.post(f"/api/v1/plans/{plan['id']}/approve", json={"approved": True}).raise_for_status()
    run = client.post("/api/v1/runs", json={"plan_id": plan["id"]}).json()
    job_id = run["job_id"]
    note("job.created", job_id=job_id)

    # 让 API 自己的后台执行器先别抢任务：直接把任务留给独立 Worker。
    worker_env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "apps" / "api"))}
    first = subprocess.Popen(
        [sys.executable, "-m", "productdirector_api.main", "worker", "--once", "--worker-id", "drill-worker-1"],
        env=worker_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    claimed_at = None
    deadline = time.time() + 120
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] == "RUNNING":
            claimed_at = time.time()
            note("job.claimed", status=job["status"], stage=job["stage"], worker_pid=first.pid)
            break
        time.sleep(2)
    if claimed_at is None:
        first.kill()
        note("drill.aborted", reason="Worker 未在 120 秒内领取任务", status=job.get("status"))
        print(json.dumps({"ok": False, "timeline": timeline}, ensure_ascii=False))
        return 1

    time.sleep(args.kill_after)
    before_kill = client.get(f"/api/v1/jobs/{job_id}").json()
    os.kill(first.pid, signal.SIGKILL)
    first.wait(timeout=30)
    note("worker.sigkill", pid=first.pid, stage_before_kill=before_kill["stage"], progress_before_kill=before_kill["progress"])

    note("waiting for lease expiry", seconds=args.lease_wait)
    time.sleep(args.lease_wait)
    after_expiry = client.get(f"/api/v1/jobs/{job_id}").json()
    note("job.after_lease_expiry", status=after_expiry["status"], stage=after_expiry["stage"])

    second = subprocess.run(
        [sys.executable, "-m", "productdirector_api.main", "worker", "--once", "--worker-id", "drill-worker-2"],
        env=worker_env, capture_output=True, text=True, timeout=args.timeout,
    )
    note("worker_2.finished", returncode=second.returncode, output=second.stdout.strip()[:200])

    final = client.get(f"/api/v1/jobs/{job_id}").json()
    note("job.final", status=final["status"], stage=final["stage"], error=(final.get("error") or "")[:200])

    events = client.get(f"/api/v1/jobs/{job_id}/events").text
    summary = {
        "ok": final["status"] == "SUCCEEDED",
        "job_id": job_id,
        "killed_worker": "drill-worker-1",
        "second_worker": "drill-worker-2",
        "status_after_expiry": after_expiry["status"],
        "final_status": final["status"],
        "has_second_claim": "worker.claimed" in events,
        "seconds": round(time.time() - started, 1),
        "timeline": timeline,
    }
    if final["status"] == "SUCCEEDED":
        video = client.get(f"/api/v1/jobs/{job_id}/video")
        summary["artifact_bytes"] = len(video.content)
    if api_process is not None and api_process.poll() is None:
        api_process.terminate()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
