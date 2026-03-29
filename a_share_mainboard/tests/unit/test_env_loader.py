from __future__ import annotations

import os

from infra.config.env_loader import load_project_env


def test_load_project_env_reads_dotenv_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("A_SHARE_TEST_TOKEN=loaded\n", encoding="utf-8")
    monkeypatch.delenv("A_SHARE_TEST_TOKEN", raising=False)

    assert load_project_env(tmp_path) is True
    assert os.getenv("A_SHARE_TEST_TOKEN") == "loaded"
