-- ProductDirectorAI V1 schema translated from the SQLite prototype.
-- Column names, nullability and keys are kept 1:1 with the SQLite schema so a
-- migration drill can compare both sides mechanically. Types follow the
-- storage the runtime actually uses (ISO-8601 strings stay TEXT) rather than
-- reinterpreting the data during the move.

CREATE TABLE IF NOT EXISTS owners (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES owners(id),
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES owners(id),
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  mime TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  owner_id TEXT NOT NULL DEFAULT 'owner-default'
);

CREATE TABLE IF NOT EXISTS product_versions (
  id TEXT PRIMARY KEY,
  product_asset_id TEXT NOT NULL REFERENCES assets(id),
  owner_id TEXT NOT NULL REFERENCES owners(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  schema_version TEXT NOT NULL DEFAULT '1.0',
  version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TEXT NOT NULL,
  approved_at TEXT,
  snapshot_sha256 TEXT,
  UNIQUE (product_asset_id, owner_id, project_id, version)
);

CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  product_asset_id TEXT NOT NULL REFERENCES assets(id),
  intent TEXT NOT NULL,
  payload TEXT NOT NULL,
  approved INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_contracts (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  product_version_id TEXT NOT NULL REFERENCES product_versions(id),
  version INTEGER NOT NULL,
  schema_version TEXT NOT NULL,
  contract_status TEXT NOT NULL DEFAULT 'DRAFT',
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress INTEGER NOT NULL,
  output_path TEXT,
  manifest_path TEXT,
  error TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  plan_contract_id TEXT NOT NULL REFERENCES plan_contracts(id),
  owner_id TEXT NOT NULL REFERENCES owners(id),
  request_hash TEXT NOT NULL,
  idempotency_key TEXT,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress INTEGER NOT NULL,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  attempt_count INTEGER NOT NULL DEFAULT 1,
  product_review_id TEXT,
  product_review_sha256 TEXT,
  fidelity_policy_id TEXT,
  fidelity_policy_version INTEGER,
  fidelity_policy_sha256 TEXT,
  fidelity_snapshot_json TEXT,
  strict_source_manifest_json TEXT,
  strict_source_manifest_sha256 TEXT,
  controlled_render_evidence_json TEXT,
  controlled_render_evidence_sha256 TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_jobs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  job_id TEXT NOT NULL REFERENCES jobs(id),
  attempt INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  error TEXT,
  lease_owner TEXT,
  lease_expires_at TEXT,
  lease_epoch INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_attempts (
  id TEXT PRIMARY KEY,
  run_job_id TEXT NOT NULL REFERENCES run_jobs(id),
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error TEXT,
  metadata TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress INTEGER NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (job_id, sequence)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  csrf_token TEXT NOT NULL,
  expires_at DOUBLE PRECISION NOT NULL,
  key_fingerprint TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_credentials (
  provider TEXT PRIMARY KEY,
  encrypted_key BYTEA NOT NULL,
  base_url TEXT NOT NULL,
  last_status TEXT,
  last_message TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_jobs (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  operation TEXT NOT NULL,
  external_id TEXT,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  request_payload TEXT NOT NULL,
  artifact_asset_id TEXT,
  artifact_path TEXT,
  artifact_sha256 TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_contracts_plan_version ON plan_contracts (plan_id, version);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_plan_id_idempotency ON runs (plan_id, idempotency_key);

CREATE TABLE IF NOT EXISTS fidelity_policies (
  id TEXT PRIMARY KEY,
  product_version_id TEXT NOT NULL REFERENCES product_versions(id),
  version INTEGER NOT NULL,
  mode TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (product_version_id, version)
);

CREATE TABLE IF NOT EXISTS product_reviews (
  id TEXT PRIMARY KEY,
  product_version_id TEXT NOT NULL REFERENCES product_versions(id),
  decision TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_threshold_sets (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  product_version_id TEXT REFERENCES product_versions(id),
  threshold_set_version TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (owner_id, project_id, product_version_id, threshold_set_version)
);

CREATE TABLE IF NOT EXISTS qa_reports (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  job_id TEXT NOT NULL REFERENCES jobs(id),
  threshold_set_id TEXT NOT NULL REFERENCES qa_threshold_sets(id),
  threshold_set_version TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  manifest_sha256 TEXT,
  binding_sha256 TEXT,
  decision TEXT,
  decision_notes TEXT,
  decision_manifest_sha256 TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interaction_anchors (
  id TEXT PRIMARY KEY,
  product_version_id TEXT NOT NULL REFERENCES product_versions(id),
  revision INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (product_version_id, revision)
);

CREATE TABLE IF NOT EXISTS anchor_sets (
  id TEXT PRIMARY KEY,
  product_version_id TEXT NOT NULL REFERENCES product_versions(id),
  revision INTEGER NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (product_version_id, revision)
);

CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES owners(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interaction_plans (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  product_version_id TEXT NOT NULL REFERENCES product_versions(id),
  anchor_set_id TEXT NOT NULL REFERENCES anchor_sets(id),
  character_id TEXT NOT NULL REFERENCES characters(id),
  version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (plan_id, version)
);

CREATE TABLE IF NOT EXISTS reference_assets (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES owners(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_kind TEXT NOT NULL,
  uploaded_asset_id TEXT,
  source_url TEXT,
  status TEXT NOT NULL DEFAULT 'INGEST',
  error TEXT,
  source_hash TEXT,
  proxy_hash TEXT,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_analyses (
  id TEXT PRIMARY KEY,
  reference_id TEXT NOT NULL REFERENCES reference_assets(id),
  revision INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  edited_from_revision INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (reference_id, revision)
);

CREATE TABLE IF NOT EXISTS reference_mappings (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  analysis_id TEXT NOT NULL REFERENCES reference_analyses(id),
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- V6-01：Platform Profile 与后期模板（不可变版本快照）
CREATE TABLE IF NOT EXISTS platform_profiles (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  profile_key TEXT NOT NULL,
  name TEXT NOT NULL,
  platform TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (owner_id, project_id, profile_key)
);

CREATE TABLE IF NOT EXISTS platform_profile_versions (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES platform_profiles(id),
  version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (profile_id, version)
);

CREATE TABLE IF NOT EXISTS postproduction_presets (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  preset_key TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (owner_id, project_id, preset_key)
);

CREATE TABLE IF NOT EXISTS postproduction_preset_versions (
  id TEXT PRIMARY KEY,
  preset_id TEXT NOT NULL REFERENCES postproduction_presets(id),
  version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (preset_id, version)
);

-- V6-02：本地化文案、字幕轨与配音
CREATE TABLE IF NOT EXISTS localizations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  profile_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS localization_revisions (
  id TEXT PRIMARY KEY,
  localization_id TEXT NOT NULL REFERENCES localizations(id),
  revision INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  edited_from_revision INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (localization_id, revision)
);

CREATE TABLE IF NOT EXISTS audio_previews (
  id TEXT PRIMARY KEY,
  localization_id TEXT,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  engine TEXT NOT NULL,
  voice TEXT NOT NULL,
  text TEXT NOT NULL,
  path TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voiceovers (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  license_ref TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subtitle_tracks (
  id TEXT PRIMARY KEY,
  localization_id TEXT NOT NULL REFERENCES localizations(id),
  revision INTEGER NOT NULL,
  locale TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- V6-03：BGM 素材、混音、时长适配、视频来源与输出成片
CREATE TABLE IF NOT EXISTS music_assets (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  license_ref TEXT NOT NULL,
  commercial_use_allowed INTEGER NOT NULL DEFAULT 0,
  path TEXT NOT NULL,
  duration_s DOUBLE PRECISION NOT NULL DEFAULT 0,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_mixes (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  voiceover_id TEXT,
  music_asset_id TEXT,
  path TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duration_adaptations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  policy TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_sources (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  path TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS output_renditions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  profile_id TEXT,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- V6-04：批次与批次项（矩阵展开 / 并发调度 / 暂停取消重试）
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  revision INTEGER NOT NULL DEFAULT 1,
  paused INTEGER NOT NULL DEFAULT 0,
  cancelled INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_items (
  id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES batches(id),
  item_index INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  run_id TEXT,
  job_id TEXT,
  cache_key TEXT NOT NULL,
  error TEXT,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (batch_id, item_index)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_idempotency ON batches (owner_id, idempotency_key);

-- V6-05：发布包（build → verify → approve，审批后不可变）
CREATE TABLE IF NOT EXISTS publish_packages (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  batch_id TEXT,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  profile_id TEXT,
  locale TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  content_hash TEXT NOT NULL DEFAULT '',
  directory TEXT NOT NULL,
  zip_path TEXT NOT NULL DEFAULT '',
  zip_sha256 TEXT NOT NULL DEFAULT '',
  approved_at TEXT,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- V6-06：资源与成本账本（用量事件去重 / 预算预留与对账 / 四类金额）
CREATE TABLE IF NOT EXISTS budgets (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  currency TEXT NOT NULL,
  limit_amount DOUBLE PRECISION,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  billing_item TEXT NOT NULL,
  event_id TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  unit TEXT NOT NULL,
  quantity DOUBLE PRECISION NOT NULL,
  run_id TEXT,
  batch_id TEXT,
  capability TEXT,
  internal_estimate INTEGER NOT NULL DEFAULT 0,
  amount DOUBLE PRECISION,
  amount_source TEXT,
  currency TEXT,
  occurred_at TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (dedupe_key)
);

CREATE TABLE IF NOT EXISTS cost_ledger (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  budget_id TEXT,
  kind TEXT NOT NULL,
  amount DOUBLE PRECISION NOT NULL,
  currency TEXT NOT NULL,
  run_id TEXT,
  batch_id TEXT,
  usage_event_id TEXT,
  note TEXT NOT NULL DEFAULT '',
  settled_at TEXT,
  settled_entry_id TEXT,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 已有安装补齐 V6-06 预留对账列（幂等：防止同一条预留被重复结算）
ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS settled_at TEXT;
ALTER TABLE cost_ledger ADD COLUMN IF NOT EXISTS settled_entry_id TEXT;