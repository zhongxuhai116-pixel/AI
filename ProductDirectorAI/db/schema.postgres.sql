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
