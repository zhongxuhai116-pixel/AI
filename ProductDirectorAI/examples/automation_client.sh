#!/usr/bin/env bash
# V6 示例包：外部客户端调用 ProductDirectorAI Automation API 的最小可运行脚本。
#
# 前置：
#   export PDA_BASE=http://127.0.0.1:8000        # API 地址
#   export PDA_KEY=pda_xxxxx_yyyyy               # Automation Key（Owner 在控制台「自动化接入」创建）
# 说明：
#   - 创建任务/发布类调用必须带 Idempotency-Key；同键同体重放返回首次结果。
#   - 本脚本不包含任何真实凭据；不要把 Key 写进 Git、日志或前端。
set -euo pipefail

BASE="${PDA_BASE:-http://127.0.0.1:8000}/api/v1"
KEY="${PDA_KEY:?请先导出 PDA_KEY（Automation Key）}"
PROFILE_ID="${PDA_PROFILE_ID:-tiktok-mx-9x16-esmx}"
PLAN_ID="${PDA_PLAN_ID:?请提供已批准的计划 ID（PDA_PLAN_ID）}"

auth=(-H "Authorization: Bearer ${KEY}")
json=(-H "Content-Type: application/json")

echo "== 1. 调用面说明（scope 与限额） =="
curl -sS "${auth[@]}" "$BASE/automation/openapi" | head -c 400; echo

echo "== 2. 批次预检（无副作用，返回展开项数） =="
curl -sS "${auth[@]}" "${json[@]}" -X POST "$BASE/batches/preview" \
  -d "{\"items\":[{\"plan_id\":\"${PLAN_ID}\",\"profile_id\":\"${PROFILE_ID}\"}]}" | head -c 400; echo

echo "== 3. 创建 1 项批次（幂等；同键重放不会重复建批次） =="
IDEMPOTENCY_KEY="example-$(date +%s)"
curl -sS "${auth[@]}" "${json[@]}" -H "Idempotency-Key: ${IDEMPOTENCY_KEY}" \
  -X POST "$BASE/automation/generate" \
  -d "{\"name\":\"示例批次\",\"max_concurrent\":1,\"items\":[{\"plan_id\":\"${PLAN_ID}\",\"profile_id\":\"${PROFILE_ID}\"}]}" \
  | head -c 600; echo

echo "== 4. 幂等重放（同键同体：应返回同一批次并带 Idempotency-Replayed: true） =="
curl -sS -D - "${auth[@]}" "${json[@]}" -H "Idempotency-Key: ${IDEMPOTENCY_KEY}" \
  -X POST "$BASE/automation/generate" \
  -d "{\"name\":\"示例批次\",\"max_concurrent\":1,\"items\":[{\"plan_id\":\"${PLAN_ID}\",\"profile_id\":\"${PROFILE_ID}\"}]}" \
  | grep -iE 'idempotency-replayed|"id"' | head -3

echo "== 5. 用量与成本（Automation Key 需要 projects:read） =="
curl -sS "${auth[@]}" "$BASE/usage?limit=5" | head -c 300; echo

echo "== 6. 发布面（需要 publish:write；未授权平台一律 BLOCKED） =="
curl -sS "${auth[@]}" "$BASE/publishing/connectors" | head -c 400; echo

echo
echo "提示：前端控制台 http://127.0.0.1:4173 提供同样的能力（总控台/批次/发布包/成本/自动化/事件/发布）。"
