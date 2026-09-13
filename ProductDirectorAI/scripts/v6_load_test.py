"""V6-09 负载/故障恢复与事件延迟实测脚本（需在部署主机上执行）。

用法（云端）：
    export PRODUCTDIRECTOR_DATABASE_URL=...   # Worker 必须与 API 用同一数据库
    export V1_TOKEN=...                        # Owner 令牌
    export WORKER_TOKEN=...                    # Worker 令牌
    export PYTHONPATH=<repo>/apps/api
    python scripts/v6_load_test.py             # 100 项 / 2 Worker / 注入 10 项故障
    python scripts/v6_latency_probe.py         # 干净端点的事件投递延迟（SQL 计算 p50/p95）

诚实边界见 docs/reports/V609_CONSOLE_LOAD_2026-09-13.md 第 5 节。
"""
"""V6-09 负载与故障恢复实测：100 项批次、2 个真实 Worker、10 项故障恢复、提交与事件延迟实测。

诚实边界：
- 渲染规格取真实可用的 V1 计划（540×960 / 3 镜头 / 5s），**不是** 1080p 吞吐结论。
- 所有延迟都是本机实测墙钟时间；事件延迟是 Outbox 写入到成功投递的实测差。
- 测试中途删除 10 项的 run_jobs 队列行，模拟产线已出现的「队列行缺失」故障，验证对账补建能否真实恢复。
"""
import http.cookiejar
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "http://127.0.0.1:8000"
BASE = f"{HOST}/api/v1"
ORIGIN = "http://127.0.0.1:4173"
REPO = "/home/ubuntu/AI/ProductDirectorAI"
PYTHON = f"{REPO}/.venv/bin/python"
ITEMS = int(os.environ.get("LOAD_ITEMS", "100"))
WORKERS = int(os.environ.get("LOAD_WORKERS", "2"))
BROKEN_ITEMS = int(os.environ.get("LOAD_BROKEN", "10"))
SUBMIT_SAMPLES = int(os.environ.get("LOAD_SUBMIT_SAMPLES", "20"))
token = os.environ["V1_TOKEN"].strip()
worker_token = os.environ["WORKER_TOKEN"].strip()
csrf = {"token": ""}
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
received: list[dict] = []
lock = threading.Lock()
report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "items": ITEMS, "workers": WORKERS}


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        with lock:
            received.append({"received_at": time.time(), "body": body,
                             "event_id": self.headers.get("X-PDA-Event-Id", "")})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return


def call(method, path, body=None, *, worker=False, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    target = f"{HOST}{path}" if path.startswith("/internal/") else BASE + path
    request = urllib.request.Request(target, data=data, method=method)
    if not worker:
        request.add_header("Origin", ORIGIN)
    if worker:
        request.add_header("Authorization", f"Bearer {worker_token}")
    elif method not in ("GET", "HEAD"):
        request.add_header("X-CSRF-Token", csrf["token"])
    if data is not None:
        request.add_header("Content-Type", "application/json")
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None), (time.monotonic() - started)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        return exc.code, payload, (time.monotonic() - started)


def get(path, **kwargs):
    return call("GET", path, **kwargs)


def percentile(values: list[float], ratio: float) -> float | None:
    """百分位（输入为秒，输出毫秒）。"""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * ratio))] * 1000.0, 2)


def psql(sql: str) -> str:
    command = ("set -a; . /etc/productdirector/pg.env; set +a; "
               f"psql \"$PRODUCTDIRECTOR_DATABASE_URL\" -c \"{sql}\"")
    result = subprocess.run(["sudo", "bash", "-c", command], capture_output=True, text=True)
    return (result.stdout or "") + (result.stderr or "")


def main() -> int:
    status, login, _ = call("POST", "/session", {"token": token})
    if status != 200:
        print("登录失败", status)
        return 1
    csrf["token"] = login["csrf_token"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    receiver_url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    # 清理历史端点：不可达目标会拖慢投递（V6-09 已修公平性，但测量要干净）
    status, existing, _ = call("GET", "/webhooks")
    for item in existing or []:
        if item.get("paused_at") is None:
            call("POST", f"/webhooks/{item['id']}/pause", {"reason": "V6-09 负载测量前清理历史端点"})
    print("paused stale endpoints:", len(existing or []))
    status, endpoint, _ = call("POST", "/webhooks", {
        "name": f"V6-09 负载接收端 {int(time.time())}", "url": receiver_url,
        "event_types": ["batch.started", "batch.completed", "item.completed", "item.failed"]})
    print("webhook endpoint:", status, endpoint.get("id"))

    status, plans, _ = call("GET", "/plans")
    approved = [plan for plan in plans if plan.get("approved") and
                (plan.get("payload") or {}).get("output", {}).get("width") == 540]
    if not approved:
        print("没有 540×960 的已批准计划")
        return 1
    plan = approved[0]
    output = plan["payload"]["output"]
    report["plan"] = {"id": plan["id"], "output": output}
    print("plan:", plan["id"], output)

    preview_latencies = []
    for _ in range(SUBMIT_SAMPLES):
        status, body, elapsed = call("POST", "/batches/preview", {
            "items": [{"plan_id": plan["id"], "profile_id": "tiktok-mx-9x16-esmx"}]})
        if status == 200:
            preview_latencies.append(elapsed)
    report["preview_latency_ms"] = {
        "samples": len(preview_latencies),
        "p50": percentile(preview_latencies, 0.5),
        "p95": percentile(preview_latencies, 0.95),
        "max": round(max(preview_latencies) * 1000.0, 2) if preview_latencies else None,
        "unit": "milliseconds",
    }
    print("preview latency:", report["preview_latency_ms"])

    items = [{"plan_id": plan["id"], "profile_id": "tiktok-mx-9x16-esmx",
              "request_item_key": f"load-{index:03d}"} for index in range(ITEMS)]
    status, created, elapsed = call("POST", "/batches", {
        "name": f"V6-09 负载 {ITEMS} 项", "items": items, "max_concurrent": WORKERS,
        "idempotency_key": f"load-{int(time.time())}"}, timeout=300)
    report["batch_submit"] = {"status": status, "seconds": round(elapsed, 3),
                              "summary": (created or {}).get("batch", {}).get("summary")}
    print("batch submit:", report["batch_submit"])
    if status != 202:
        print(json.dumps(created, ensure_ascii=False)[:500])
        return 1
    batch_id = created["batch"]["id"]
    report["batch_id"] = batch_id

    workers = []
    for index in range(WORKERS):
        log = open(f"/tmp/v609_worker_{index}.log", "w")
        workers.append(subprocess.Popen(
            [PYTHON, "-m", "productdirector_api.main", "worker", "--worker-id", f"load-worker-{index+1}",
             "--poll-seconds", "1"], cwd=f"{REPO}/apps/api", stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": f"{REPO}/apps/api"}))
    print("workers started:", [item.pid for item in workers])

    # 投递 Worker 循环（等价于部署里的 systemd timer / 常驻 Worker）：每 2 秒派发一次
    dispatcher = {"stop": False, "calls": 0, "attempted": 0}
    # 调度 tick（等价于部署里的调度 Worker）：每 3 秒推进批次，Worker 从 run_jobs 领取
    scheduler = {"stop": False, "calls": 0, "scheduled": 0}

    def dispatch_loop():
        while not dispatcher["stop"]:
            try:
                status, body, _ = call("POST", "/internal/v1/webhooks/dispatch?limit=50", worker=True, timeout=30)
                if status == 200:
                    dispatcher["calls"] += 1
                    dispatcher["attempted"] += int(body.get("attempted") or 0)
            except Exception:
                pass
            time.sleep(2.0)

    def schedule_loop():
        while not scheduler["stop"]:
            try:
                status, body, _ = call("POST", "/internal/v1/batches/advance?limit=50", worker=True, timeout=60)
                if status == 200:
                    scheduler["calls"] += 1
                    scheduler["scheduled"] += sum(int(item.get("scheduled") or 0) for item in body["results"])
            except Exception:
                pass
            time.sleep(3.0)

    dispatch_thread = threading.Thread(target=dispatch_loop, daemon=True)
    schedule_thread = threading.Thread(target=schedule_loop, daemon=True)
    dispatch_thread.start()
    schedule_thread.start()

    samples = []
    broken_at = None
    broken_item_ids: list[str] = []
    started = time.monotonic()
    deadline = started + float(os.environ.get("LOAD_TIMEOUT", "5400"))
    while time.monotonic() < deadline:
        status, detail, _ = call("GET", f"/batches/{batch_id}")
        status2, items_page, _ = call("GET", f"/batches/{batch_id}/items?limit=200")
        summary = (detail or {}).get("summary") or {}
        sample = {"t": round(time.monotonic() - started, 1), "status": (detail or {}).get("status"), **summary}
        samples.append(sample)
        if broken_at is None and sample.get("succeeded", 0) >= 5 and BROKEN_ITEMS:
            broken_at = round(time.monotonic() - started, 1)
        if BROKEN_ITEMS and len(broken_item_ids) < BROKEN_ITEMS:
            # 持续注入直到达到目标条数：并发上限只有 2，一次只能看到少量 QUEUED 项
            candidates = [row for row in items_page["items"]
                          if row.get("job_id") and row["id"] not in broken_item_ids
                          and row["status"] in ("QUEUED", "RUNNING")]
            for row in candidates[:BROKEN_ITEMS - len(broken_item_ids)]:
                listing = f"'{row['job_id']}'"
                print(psql(f"DELETE FROM run_jobs WHERE job_id IN ({listing});").strip()[:200])
                broken_item_ids.append(row["id"])
            if broken_item_ids and "failure_injection" not in report:
                report["failure_injection"] = {"method": "删除 run_jobs 队列行（复现产线故障）",
                                               "at_seconds": broken_at or round(time.monotonic() - started, 1)}
            if "failure_injection" in report:
                report["failure_injection"]["items"] = len(broken_item_ids)
        if sample.get("pending", 0) == 0 and sample.get("queued", 0) == 0 and sample.get("running", 0) == 0:
            if sample.get("total") and (sample.get("succeeded", 0) + sample.get("failed", 0)
                                        + sample.get("cancelled", 0)) >= ITEMS:
                break
        time.sleep(10)

    drain_seconds = round(time.monotonic() - started, 1)
    dispatcher["stop"] = True
    scheduler["stop"] = True
    dispatch_thread.join(timeout=10)
    schedule_thread.join(timeout=10)
    for worker in workers:
        worker.terminate()
    for worker in workers:
        try:
            worker.wait(timeout=60)
        except subprocess.TimeoutExpired:
            worker.kill()

    status, detail, _ = call("GET", f"/batches/{batch_id}")
    status2, items_page, _ = call("GET", f"/batches/{batch_id}/items?limit=200")
    summary = (detail or {}).get("summary") or {}
    status3, overview, _ = call("GET", "/console/overview?window_minutes=10")
    status5, overview_wide, _ = call("GET", "/console/overview?window_minutes=180")
    status4, dispatch, _ = call("POST", "/internal/v1/webhooks/dispatch?limit=200", worker=True)

    delivered_events = [json.loads(item["body"]) for item in received]
    latencies = []
    with lock:
        snapshot = list(received)
    for item in snapshot:
        try:
            envelope = json.loads(item["body"])
            occurred = datetime.fromisoformat(envelope["occurred_at"].replace("Z", "+00:00")).timestamp()
            latencies.append(max(0.0, (item["received_at"] - occurred) * 1000.0))
        except Exception:
            continue

    report.update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "drain_seconds": drain_seconds,
        "final_summary": summary,
        "items_final": {item["status"]: sum(1 for row in items_page["items"] if row["status"] == item["status"])
                        for item in items_page["items"]},
        "recovery": {"injected": len(broken_item_ids), "at_seconds": broken_at,
                     "recovered": sum(1 for row in items_page["items"]
                                      if row["id"] in broken_item_ids and row["status"] == "SUCCEEDED"),
                     "failed": sum(1 for row in items_page["items"]
                                   if row["id"] in broken_item_ids and row["status"] == "FAILED")},
        "throughput_items_per_minute": round(len(items_page["items"]) / max(1e-6, drain_seconds / 60.0), 2),
        "events": {"delivered_to_receiver": len(delivered_events),
                   "types": {kind: sum(1 for event in delivered_events if event["event_type"] == kind)
                             for kind in {"batch.started", "batch.completed", "item.completed", "item.failed"}},
                   "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95),
                                  "max": round(max(latencies), 1) if latencies else None,
                                  "samples": len(latencies)}},
        "queue_samples": samples[::max(1, len(samples) // 20)],
        "console_overview": {
            "jobs_by_status": (overview_wide or {}).get("jobs", {}).get("by_status"),
            "success_rate": (overview_wide or {}).get("jobs", {}).get("success_rate"),
            "queue": (overview_wide or {}).get("queue"),
            "batches": (overview_wide or {}).get("batches"),
            "webhooks": (overview_wide or {}).get("webhooks", {}).get("delivery_latency_ms"),
            "events": {key: value for key, value in ((overview_wide or {}).get("events") or {}).items()
                       if key != "latest"},
        },
        "console_overview_10min": {
            "jobs_by_status": (overview or {}).get("jobs", {}).get("by_status"),
            "success_rate": (overview or {}).get("jobs", {}).get("success_rate"),
            "webhook_latency_window": (overview or {}).get("webhooks", {}).get("delivery_latency_ms"),
            "webhook_counts": {key: value for key, value in ((overview or {}).get("webhooks") or {}).items()
                               if key != "delivery_latency_ms"},
            "events_window": (overview or {}).get("events", {}).get("window"),
            "events_undelivered": (overview or {}).get("events", {}).get("undelivered"),
        },
        "dispatch": {key: value for key, value in (dispatch or {}).items() if key != "results"},
        "dispatcher_loop": {"calls": dispatcher["calls"], "attempted": dispatcher["attempted"],
                            "interval_seconds": 2.0,
                            "note": "等价于部署中的 systemd timer/常驻投递 Worker；本产品当前没有常驻进程"},
        "scheduler_loop": {"calls": scheduler["calls"], "scheduled": scheduler["scheduled"],
                           "interval_seconds": 3.0,
                           "note": "调度 tick：只创建 Run 与队列行（execute_inline=false），Worker 领取后执行"},
    })
    with open("/home/ubuntu/v609_load_report.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
