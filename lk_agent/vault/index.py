from __future__ import annotations

import sqlite3

from lk_agent.core.models import VaultConfig
from lk_agent.vault.parser import parse_markdown
from lk_agent.vault.storage import delete_missing_notes, sync_vault_record, upsert_note


def rebuild_vault(connection: sqlite3.Connection, vault: VaultConfig) -> dict[str, int]:
    root = vault.resolved_path()
    if not root.exists():
        raise FileNotFoundError(f"vault path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"vault path is not a directory: {root}")

    vault_id = sync_vault_record(connection, vault)
    indexed = 0
    existing_paths: set[str] = set()

    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        parsed = parse_markdown(path)
        relative_path = path.relative_to(root).as_posix()
        upsert_note(connection, vault_id, path, relative_path, parsed)
        existing_paths.add(str(path.resolve()))
        indexed += 1

    deleted = delete_missing_notes(connection, vault_id, existing_paths)
    connection.commit()
    return {"indexed": indexed, "deleted": deleted}


def rebuild_all(connection: sqlite3.Connection, vaults: list[VaultConfig]) -> list[tuple[str, dict[str, int]]]:
    return [(vault.name, rebuild_vault(connection, vault)) for vault in vaults if vault.enabled]


def _tokenize_search_text(query: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for char in query:
        if char.isalnum() or char == "_":
            current.append(char)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def normalize_search_query(query: str) -> str | None:
    tokens = _tokenize_search_text(query)
    if not tokens:
        return None
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def search_notes(connection: sqlite3.Connection, query: str, limit: int = 10) -> list[sqlite3.Row]:
    normalized = normalize_search_query(query)
    if not normalized:
        return []
    try:
        return connection.execute(
            """
            SELECT absolute_path, title, snippet(note_fts, 2, '[', ']', '...', 10) AS snippet
            FROM note_fts
            WHERE note_fts MATCH ?
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
    except sqlite3.Error:
        return []
