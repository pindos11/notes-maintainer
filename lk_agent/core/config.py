from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from lk_agent.core.models import AppConfig, OllamaConfig, SchedulerConfig, TelegramConfig, VaultConfig


CONFIG_FILE = "config.json"


def default_root() -> Path:
    return Path.cwd()


def config_path(root: Path | None = None) -> Path:
    return ((root or default_root()) / CONFIG_FILE).resolve()


def load_config(root: Path | None = None) -> AppConfig:
    path = config_path(root)
    if not path.exists():
        return AppConfig()

    data = json.loads(path.read_text(encoding="utf-8"))
    telegram_data = dict(data.get("telegram", {}))
    return AppConfig(
        data_dir=data.get("data_dir", "data"),
        db_path=data.get("db_path", "data/app.db"),
        vaults=[VaultConfig(**item) for item in data.get("vaults", [])],
        ollama=OllamaConfig(**data.get("ollama", {})),
        telegram=TelegramConfig(**telegram_data),
        scheduler=SchedulerConfig(**data.get("scheduler", {})),
    )


def save_config(config: AppConfig, root: Path | None = None) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    data["telegram"]["bot_token"] = None
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def ensure_runtime_dirs(config: AppConfig, root: Path | None = None) -> None:
    base = root or default_root()
    data_dir = config.resolved_data_dir(base)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "memory").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
