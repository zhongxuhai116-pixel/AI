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
"""V6-09 事件延迟专项测量：干净端点 + 20 项批次，延迟用数据库时间源计算。"""
import http.cookiejar
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "http://127.0.0.1:8000"
BASE = f"{HOST}/api/v1"
ORIGIN = "http://127.0.0.1:4173"
REPO = "/home/ubuntu/AI/ProductDirectorAI"
PYTHON = f"{REPO}/.venv/bin/python"
token = os.environ["V1_TOKEN"].strip()
worker_token = os.environ["WORKER_TOKEN"].strip()
csrf = {"token": ""}
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
received = {"count": 0}
lock = threading.Lock()


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        with lock:
            received["count"] += 1
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
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, None


def psql(sql):
    command = ("set -a; . /etc/productdirector/pg.env; set +a; "
               f"psql -t -A \"$PRODUCTDIRECTOR_DATABASE_URL\" -c \"{sql}\"")
    return subprocess.run(["sudo", "bash", "-c", command], capture_output=True, text=True).stdout.strip()


status, login = call("POST", "/session", {"token": token})
csrf["token"] = login["csrf_token"]

server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
threading.Thread(target=server.serve_forever, daemon=True).start()
status, endpoint = call("POST", "/webhooks", {
    "name": f"V6-09 延迟测量 {int(time.time())}", "url": f"http://127.0.0.1:{server.server_address[1]}/hook",
    "event_types": ["item.completed", "item.failed", "batch.completed", "batch.started"]})
print("endpoint:", status, endpoint.get("id"))

status, plans = call("GET", "/plans")
plan = next(item for item in plans if item.get("approved")
            and (item.get("payload") or {}).get("output", {}).get("width") == 540)
status, created = call("POST", "/batches", {
    "name": f"V6-09 延迟批次 {int(time.time())}", "max_concurrent": 2,
    "items": [{"plan_id": plan["id"], "profile_id": "tiktok-mx-9x16-esmx", "request_item_key": f"lat-{index}"}
              for index in range(20)],
    "idempotency_key": f"lat-{int(time.time())}"})
batch_id = created["batch"]["id"]
print("batch:", batch_id, created["batch"]["summary"]["total"])

workers = [subprocess.Popen([PYTHON, "-m", "productdirector_api.main", "worker", "--worker-id", f"lat-worker-{i}",
                             "--poll-seconds", "1"], cwd=f"{REPO}/apps/api",
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=os.environ)
           for i in range(2)]
stop = {"now": False}


def loops():
    while not stop["now"]:
        try:
            call("POST", "/internal/v1/batches/advance?limit=20", worker=True, timeout=30)
        except Exception:
            pass
        try:
            call("POST", "/internal/v1/webhooks/dispatch?limit=20", worker=True, timeout=30)
        except Exception:
            pass
        time.sleep(2.0)


threading.Thread(target=loops, daemon=True).start()
started = time.monotonic()
for _ in range(90):
    status, detail = call("GET", f"/batches/{batch_id}")
    if detail and detail["summary"]["production_complete"]:
        break
    time.sleep(5)
drain = round(time.monotonic() - started, 1)
time.sleep(5)
stop["now"] = True
for worker in workers:
    worker.terminate()

status, detail = call("GET", f"/batches/{batch_id}")
sql = (
    "SELECT count(*) FILTER (WHERE a.status = 'DELIVERED') AS delivered, "
    "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (a.created_at::timestamptz - e.created_at::timestamptz)) * 1000)::numeric, 1) AS p50_ms, "
    "round(percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (a.created_at::timestamptz - e.created_at::timestamptz)) * 1000)::numeric, 1) AS p95_ms, "
    "round(max(EXTRACT(EPOCH FROM (a.created_at::timestamptz - e.created_at::timestamptz)) * 1000)::numeric, 1) AS max_ms "
    f"FROM delivery_attempts a JOIN outbox_events e ON e.event_id = a.event_id WHERE a.endpoint_id = '{endpoint['id']}'"
)
print("drain_seconds:", drain, "summary:", json.dumps(detail["summary"], ensure_ascii=False))
print("receiver_received:", received["count"])
print("latency(same db clock):", psql(sql))
report = {"endpoint_id": endpoint["id"], "batch_id": batch_id, "drain_seconds": drain,
          "items": detail["summary"], "receiver_received": received["count"], "latency_query": psql(sql)}
with open("/home/ubuntu/v609_latency_report.json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, ensure_ascii=False, indent=2)
server.shutdown()
