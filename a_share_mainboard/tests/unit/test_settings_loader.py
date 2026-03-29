from pathlib import Path

from infra.config.loader import load_settings


def test_load_settings_smoke():
    project_root = Path(__file__).resolve().parents[2]
    settings = load_settings(project_root / "config")
    assert settings.app.project_name == "a_share_mainboard"
    assert settings.data.market_provider == "akshare"
    assert settings.strategy.horizons == [5, 10]

