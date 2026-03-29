from __future__ import annotations

import tomllib
from pathlib import Path

from infra.config.settings import (
    AISettings,
    AppSettings,
    DataSettings,
    FeishuSettings,
    PolicySettings,
    Settings,
    StrategySettings,
    UniverseSettings,
    ValidationSettings,
)
from infra.exceptions import ConfigError


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_settings(config_dir: str | Path) -> Settings:
    config_dir = Path(config_dir)
    return Settings(
        app=AppSettings(**_read_toml(config_dir / "app.toml")),
        data=DataSettings(**_read_toml(config_dir / "data.toml")),
        universe=UniverseSettings(**_read_toml(config_dir / "universe.toml")),
        strategy=StrategySettings(**_read_toml(config_dir / "strategy.toml")),
        validation=ValidationSettings(**_read_toml(config_dir / "validation.toml")),
        ai=AISettings(**_read_toml(config_dir / "ai.toml")),
        feishu=FeishuSettings(**_read_toml(config_dir / "feishu.toml")),
        policy=PolicySettings(**_read_toml(config_dir / "policy.toml")),
    )
