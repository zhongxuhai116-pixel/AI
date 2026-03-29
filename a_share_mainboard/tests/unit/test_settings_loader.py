from pathlib import Path

from infra.config.loader import load_settings


def test_load_settings_smoke():
    project_root = Path(__file__).resolve().parents[2]
    settings = load_settings(project_root / "config")
    assert settings.app.project_name == "a_share_mainboard"
    assert settings.data.market_provider == "akshare"
    assert settings.strategy.horizons == [5, 10]
    assert settings.strategy.primary_horizon == 10
    assert settings.strategy.auxiliary_horizons == [5]
    assert settings.strategy.execution_horizons() == [10, 5]
    assert settings.policy.enabled is True
    assert settings.policy.min_theme_match_count == 2
    assert settings.policy.fresh_event_days == 10
    assert len(settings.policy.themes[0].industry_aliases) >= 1
    assert len(settings.policy.themes) >= 1
