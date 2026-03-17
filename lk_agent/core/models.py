from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class VaultConfig:
    name: str
    path: str
    enabled: bool = True

    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve()


@dataclass(slots=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    default_model: str | None = None


@dataclass(slots=True)
class TelegramConfig:
    bot_token: str | None = None
    bot_token_provider: str | None = None
    bot_token_ref: str | None = None
    allowed_chat_ids: list[int] = field(default_factory=list)
    inbox_vault: str | None = None
    inbox_dir: str = "Inbox"


@dataclass(slots=True)
class SchedulerConfig:
    timezone: str = "Europe/Kiev"
    poll_seconds: int = 60


@dataclass(slots=True)
class AppConfig:
    data_dir: str = "data"
    db_path: str = "data/app.db"
    vaults: list[VaultConfig] = field(default_factory=list)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)

    def resolved_data_dir(self, root: Path) -> Path:
        return (root / self.data_dir).resolve()

    def resolved_db_path(self, root: Path) -> Path:
        return (root / self.db_path).resolve()


@dataclass(slots=True)
class ParsedTask:
    text: str
    status: str
    line_no: int


@dataclass(slots=True)
class ParsedNote:
    title: str
    raw_content: str
    body: str
    frontmatter: dict[str, object]
    tags: list[str]
    links: list[str]
    tasks: list[ParsedTask]
    word_count: int
