# Runtime, Logging, Desktop, Packaging

## 1) Runtime Safety
- Single-run lock file: `data/logs/runtime.lock`
- Lock stale timeout is configurable in `config/app.toml`:
  - `run_lock_stale_seconds`
- If a run is already active, the new run exits with `RuntimeBusyError`.

## 2) Logging Layout
- Global run stream: `data/logs/pipeline_runs.jsonl`
- Global event stream: `data/logs/pipeline_events.jsonl`
- Per-run metadata: `data/logs/runs/<run_id>.json`
- Per-run events: `data/logs/runs/<run_id>.events.jsonl`
- Retention cleanup days is configurable in `config/app.toml`:
  - `log_retention_days`

## 3) Feishu Phase Setting
- Feishu is deferred by default (phase-later mode).
- `config/feishu.toml` uses:
  - `enabled = false`
- Daily workflow marks Feishu as `SKIPPED` when disabled and continues core flow.

## 4) Desktop Launcher
- Run directly from source:

```powershell
python scripts/run_desktop.py
```

- Capabilities:
  - Run daily workflow
  - Run single validation
  - Run rolling validation
  - Run historical rebuild
  - Show live runtime events in the desktop log panel

## 5) Windows Packaging
- Build desktop package:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
```

- Output:
  - `dist/AShareTradingAgentsLite/`
- The packaging script bundles:
  - `config/`
  - `src/ai/prompts/`
  - base `data/` directories
  - `.env.example`
