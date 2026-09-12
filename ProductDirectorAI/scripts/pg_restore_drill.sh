#!/usr/bin/env bash
# A03 PostgreSQL 迁移与恢复演练（隔离库，仅回环）
#
# 步骤：SQLite 一致快照 -> 迁移到 PostgreSQL -> pg_dump 备份 -> 恢复到新库 -> 逐表核对计数与内容摘要
#
# 用法：sudo ./scripts/pg_restore_drill.sh /home/ubuntu/AI/ProductDirectorAI/var/productdirector.db
# 需要的环境文件（root 可读，不进 Git）：
#   PG_DSN_SRC   目标演练库 DSN
#   PG_DSN_DST   恢复演练库 DSN
#   PG_DSN_ADMIN 指向 postgres 维护库的 DSN（用于 drop/create database）
set -euo pipefail

SQLITE_SOURCE="${1:-}"
ENV_FILE="${PD_PG_ENV:-/etc/productdirector/pg-drill.env}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="${PD_VENV_PY:-$REPO_DIR/.venv/bin/python}"
WORK_DIR="${PD_DRILL_DIR:-/home/ubuntu/pd-pg-drill}"
SRC_DB="${PD_SRC_DB:-productdirector_drill}"
DST_DB="productdirector_drill_restore"

TABLES=(owners workspaces projects assets product_versions plans plan_contracts jobs runs run_jobs job_attempts job_events auth_sessions provider_credentials provider_jobs)

if [ -z "$SQLITE_SOURCE" ]; then
  echo "用法: $0 <sqlite 源库路径>" >&2
  exit 2
fi
if [ ! -r "$SQLITE_SOURCE" ]; then
  echo "无法读取 SQLite 源库: $SQLITE_SOURCE" >&2
  exit 2
fi
if [ ! -r "$ENV_FILE" ]; then
  echo "缺少环境文件 $ENV_FILE（需包含 PG_DSN_SRC / PG_DSN_DST / PG_DSN_ADMIN）" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

mkdir -p "$WORK_DIR"
STAMP="$(date +%Y%m%dT%H%M%SZ)"
SNAPSHOT="$WORK_DIR/productdirector-$STAMP.db"
DUMP="$WORK_DIR/productdirector-drill-$STAMP.dump"

echo "== 0. 取 SQLite 一致快照（只读源库，不修改线上数据） =="
"$VENV_PY" - "$SQLITE_SOURCE" "$SNAPSHOT" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
dst = sqlite3.connect(target)
with dst:
    src.backup(dst)
src.close()
dst.close()
print("snapshot:", target)
PY
sha256sum "$SNAPSHOT"

echo
echo "== 1. 迁移到 PostgreSQL =="
psql "$PG_DSN_ADMIN" -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS $SRC_DB"
psql "$PG_DSN_ADMIN" -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE $SRC_DB"
"$VENV_PY" "$REPO_DIR/scripts/pg_migrate.py" --sqlite "$SNAPSHOT" --dsn "$PG_DSN_SRC" \
  | tee "$WORK_DIR/migrate-$STAMP.json"

echo
echo "== 1b. 重复迁移一次（幂等性：不应产生重复行） =="
"$VENV_PY" "$REPO_DIR/scripts/pg_migrate.py" --sqlite "$SNAPSHOT" --dsn "$PG_DSN_SRC" \
  | tee "$WORK_DIR/migrate-second-pass-$STAMP.json" | grep -E '"ok"|"copied"|"target_rows"' | head -8

echo
echo "== 2. 备份（pg_dump） =="
pg_dump --dbname="$PG_DSN_SRC" --format=custom --file="$DUMP"
ls -l "$DUMP"
sha256sum "$DUMP"

echo
echo "== 3. 恢复到新库 =="
psql "$PG_DSN_ADMIN" -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS $DST_DB"
psql "$PG_DSN_ADMIN" -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE $DST_DB"
pg_restore --dbname="$PG_DSN_DST" --no-owner --exit-on-error "$DUMP"

echo
echo "== 4. 逐表核对（行数 + 内容摘要） =="
fail=0
printf "%-22s %10s %10s  %s\n" "table" "src" "restore" "content"
for table in "${TABLES[@]}"; do
  src_count="$(psql "$PG_DSN_SRC" -tAc "SELECT COUNT(*) FROM $table")"
  dst_count="$(psql "$PG_DSN_DST" -tAc "SELECT COUNT(*) FROM $table")"
  src_md5="$(psql "$PG_DSN_SRC" -tAc "SELECT COALESCE(md5(string_agg(t::text, '|' ORDER BY t::text)), 'empty') FROM $table t")"
  dst_md5="$(psql "$PG_DSN_DST" -tAc "SELECT COALESCE(md5(string_agg(t::text, '|' ORDER BY t::text)), 'empty') FROM $table t")"
  if [ "$src_count" = "$dst_count" ] && [ "$src_md5" = "$dst_md5" ]; then
    printf "%-22s %10s %10s  %s\n" "$table" "$src_count" "$dst_count" "match"
  else
    printf "%-22s %10s %10s  %s\n" "$table" "$src_count" "$dst_count" "MISMATCH($src_md5/$dst_md5)"
    fail=1
  fi
done

echo
if [ "$fail" -eq 0 ]; then
  echo "结果：PASS（迁移与恢复后逐表行数和内容摘要一致）"
else
  echo "结果：FAIL（存在不一致，见上表）"
fi
echo "快照：$SNAPSHOT"
echo "备份：$DUMP"
exit "$fail"
