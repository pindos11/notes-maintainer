from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from lk_agent.core.models import VaultConfig
from lk_agent.vault.parser import parse_markdown
from lk_agent.vault.storage import sync_vault_record, upsert_note


TASK_SOURCE_FILTER = "AND COALESCE(m.source_type, 'note') <> 'summary'"


def build_digest_lines(connection: sqlite3.Connection, vault: VaultConfig) -> list[str]:
    notes = connection.execute(
        """
        SELECT title, absolute_path, updated_at
        FROM notes
        WHERE vault_id = (SELECT id FROM vaults WHERE name = ?)
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (vault.name,),
    ).fetchall()
    tasks = connection.execute(
        f"""
        SELECT COUNT(*) AS open_count
        FROM tasks AS t
        JOIN notes AS n ON n.id = t.note_id
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE t.status = 'open'
          AND n.vault_id = (SELECT id FROM vaults WHERE name = ?)
          {TASK_SOURCE_FILTER}
        """,
        (vault.name,),
    ).fetchone()
    lines = [
        "# Vault Digest",
        "",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Open tasks: {int(tasks['open_count']) if tasks else 0}",
        "",
        "## Recent Notes",
        "",
    ]
    if not notes:
        lines.append("No indexed notes.")
    else:
        for row in notes:
            lines.append(f"- {row['title']} ({row['absolute_path']})")
    return lines


def build_digest_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    return "\n".join(build_digest_lines(connection, vault)).strip() + "\n"


def write_digest(connection: sqlite3.Connection, vault: VaultConfig, output_path: str) -> Path:
    root = vault.resolved_path()
    target = root / output_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_digest_text(connection, vault), encoding="utf-8")
    index_generated_note(connection, vault, target)
    return target


def index_generated_note(connection: sqlite3.Connection, vault: VaultConfig, target: Path) -> None:
    vault_id = sync_vault_record(connection, vault)
    parsed = parse_markdown(target)
    upsert_note(connection, vault_id, target, target.relative_to(vault.resolved_path()).as_posix(), parsed)
