from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lk_agent.core.models import AppConfig


def memory_root(config: AppConfig, root: Path | None = None) -> Path:
    base = (root or Path.cwd()).resolve()
    return (config.resolved_data_dir(base) / "memory").resolve()


def write_shared_memory(
    connection: sqlite3.Connection,
    config: AppConfig,
    vault_name: str,
    memory_key: str,
    title: str,
    body: str,
    source_note_count: int,
    root: Path | None = None,
) -> Path:
    target = memory_root(config, root) / "shared" / vault_name / f"{memory_key}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = f"# {title}\n\n{body.strip()}\n"
    target.write_text(text, encoding="utf-8")
    connection.execute(
        """
        INSERT INTO shared_memory (memory_key, vault_name, scope_type, summary_text, source_note_count, backing_path, updated_at)
        VALUES (?, ?, 'vault', ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(memory_key) DO UPDATE SET
            vault_name = excluded.vault_name,
            summary_text = excluded.summary_text,
            source_note_count = excluded.source_note_count,
            backing_path = excluded.backing_path,
            updated_at = CURRENT_TIMESTAMP
        """,
        (memory_key, vault_name, body.strip(), source_note_count, str(target)),
    )
    return target


def read_shared_memory(connection: sqlite3.Connection, vault_name: str, memory_key: str) -> str | None:
    row = connection.execute(
        "SELECT summary_text FROM shared_memory WHERE vault_name = ? AND memory_key = ?",
        (vault_name, memory_key),
    ).fetchone()
    if row is None:
        return None
    text = (row["summary_text"] or "").strip()
    return text or None


def read_shared_memory_bundle(connection: sqlite3.Connection, vault_name: str, memory_keys: list[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for key in memory_keys:
        text = read_shared_memory(connection, vault_name, key)
        if text:
            result.append((key, text))
    return result


def write_agent_memory(
    connection: sqlite3.Connection,
    config: AppConfig,
    agent_name: str,
    memory_key: str,
    title: str,
    body: str,
    state: dict[str, object],
    root: Path | None = None,
) -> Path:
    target = memory_root(config, root) / "agents" / agent_name / f"{memory_key}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", body.strip(), "", "## State", "", "```json", json.dumps(state, indent=2, ensure_ascii=False), "```", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    connection.execute(
        """
        INSERT INTO agent_memory (agent_name, memory_key, summary_text, state_json, backing_path, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(agent_name, memory_key) DO UPDATE SET
            summary_text = excluded.summary_text,
            state_json = excluded.state_json,
            backing_path = excluded.backing_path,
            updated_at = CURRENT_TIMESTAMP
        """,
        (agent_name, memory_key, body.strip(), json.dumps(state, ensure_ascii=False), str(target)),
    )
    return target
