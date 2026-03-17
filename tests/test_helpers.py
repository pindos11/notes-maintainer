from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from lk_agent.core.db import initialize
from lk_agent.core.models import AppConfig, VaultConfig
from lk_agent.vault.index import rebuild_vault


class TempApp:
    def __init__(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault_path = self.root / "vault"
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.config = AppConfig(
            data_dir="data",
            db_path="data/app.db",
            vaults=[VaultConfig(name="main", path=str(self.vault_path))],
        )

    def close(self) -> None:
        self._tmp.cleanup()

    def db_connection(self):
        return initialize(self.config.resolved_db_path(self.root))

    def write_note(self, relative_path: str, text: str) -> Path:
        path = self.vault_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def rebuild(self) -> dict[str, int]:
        connection = self.db_connection()
        try:
            return rebuild_vault(connection, self.config.vaults[0])
        finally:
            connection.close()

