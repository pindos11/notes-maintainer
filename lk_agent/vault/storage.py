from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from lk_agent.core.models import ParsedNote, VaultConfig


SUMMARY_NAME_HINTS = {"summary", "digest", "brief", "report", "agent"}


def sync_vault_record(connection: sqlite3.Connection, vault: VaultConfig) -> int:
    connection.execute(
        """
        INSERT INTO vaults (name, path, enabled)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            path = excluded.path,
            enabled = excluded.enabled,
            updated_at = CURRENT_TIMESTAMP
        """,
        (vault.name, str(vault.resolved_path()), 1 if vault.enabled else 0),
    )
    row = connection.execute("SELECT id FROM vaults WHERE name = ?", (vault.name,)).fetchone()
    if row is None:
        raise RuntimeError(f"failed to resolve vault id for {vault.name}")
    return int(row["id"])


def upsert_note(
    connection: sqlite3.Connection,
    vault_id: int,
    path: Path,
    relative_path: str,
    parsed: ParsedNote,
) -> None:
    stat = path.stat()
    absolute_path = str(path.resolve())
    content_hash = hashlib.sha256(parsed.raw_content.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO notes (
            vault_id, relative_path, absolute_path, title, content_hash, last_seen_mtime
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(absolute_path) DO UPDATE SET
            vault_id = excluded.vault_id,
            relative_path = excluded.relative_path,
            title = excluded.title,
            content_hash = excluded.content_hash,
            last_seen_mtime = excluded.last_seen_mtime,
            indexed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        """,
        (vault_id, relative_path, absolute_path, parsed.title, content_hash, stat.st_mtime),
    )
    row = connection.execute("SELECT id FROM notes WHERE absolute_path = ?", (absolute_path,)).fetchone()
    if row is None:
        raise RuntimeError(f"failed to resolve note id for {path}")
    note_id = int(row["id"])

    connection.execute("DELETE FROM note_metadata WHERE note_id = ?", (note_id,))
    connection.execute("DELETE FROM tasks WHERE note_id = ?", (note_id,))
    connection.execute("DELETE FROM note_fts WHERE absolute_path = ?", (absolute_path,))
    connection.execute(
        """
        INSERT INTO note_metadata (
            note_id, frontmatter_json, tags_json, links_json, tasks_json, word_count, source_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            json.dumps(parsed.frontmatter),
            json.dumps(parsed.tags),
            json.dumps(parsed.links),
            json.dumps([asdict(task) for task in parsed.tasks]),
            parsed.word_count,
            infer_source_type(relative_path),
        ),
    )

    for task in parsed.tasks:
        connection.execute(
            "INSERT INTO tasks (note_id, text, status, line_ref) VALUES (?, ?, ?, ?)",
            (note_id, task.text, task.status, task.line_no),
        )

    connection.execute(
        "INSERT INTO note_fts (absolute_path, title, body) VALUES (?, ?, ?)",
        (absolute_path, parsed.title, parsed.body),
    )


def delete_missing_notes(connection: sqlite3.Connection, vault_id: int, existing_paths: set[str]) -> int:
    rows = connection.execute("SELECT absolute_path FROM notes WHERE vault_id = ?", (vault_id,)).fetchall()
    deleted = 0
    for row in rows:
        absolute_path = row["absolute_path"]
        if absolute_path in existing_paths:
            continue
        connection.execute("DELETE FROM notes WHERE absolute_path = ?", (absolute_path,))
        connection.execute("DELETE FROM note_fts WHERE absolute_path = ?", (absolute_path,))
        deleted += 1
    return deleted


def infer_source_type(relative_path: str) -> str:
    normalized = Path(relative_path.replace("\\", "/"))
    parts = {part.lower() for part in normalized.parts}
    stem_words = {word.lower() for word in normalized.stem.replace("_", "-").split("-") if word}
    if "inbox" in parts:
        return "inbox"
    if parts.intersection({"reports", "report", "summaries"}) or stem_words.intersection(SUMMARY_NAME_HINTS):
        return "summary"
    return "note"
