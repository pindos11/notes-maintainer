from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lk_agent.core.models import VaultConfig
from lk_agent.vault.parser import parse_markdown
from lk_agent.vault.storage import sync_vault_record, upsert_note


def serialize_frontmatter_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def default_capture_filename(timestamp: datetime, source: str) -> str:
    return f"{timestamp.strftime('%H%M%S-%f')}-{source}.md"


def build_capture_note_body(
    *,
    source: str,
    title: str,
    text: str,
    metadata: dict[str, object] | None = None,
    trailing_lines: list[str] | None = None,
) -> str:
    lines = ["---", f"source: {source}"]
    for key, value in (metadata or {}).items():
        lines.append(f"{key}: {serialize_frontmatter_value(value)}")
    lines.extend(["---", "", f"# {title}", ""])
    cleaned = text.strip()
    if cleaned:
        lines.append(cleaned)
        lines.append("")
    for line in trailing_lines or []:
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def write_capture_note(
    *,
    vault: VaultConfig,
    inbox_dir: str,
    timestamp: datetime,
    filename: str,
    body: str,
) -> Path:
    root = vault.resolved_path()
    target_dir = root / inbox_dir / timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(body, encoding="utf-8")
    return path


def index_capture_note(connection, vault: VaultConfig, note_path: Path) -> None:
    vault_id = sync_vault_record(connection, vault)
    parsed = parse_markdown(note_path)
    upsert_note(connection, vault_id, note_path, note_path.relative_to(vault.resolved_path()).as_posix(), parsed)


def capture_text_to_inbox(
    connection,
    *,
    vault: VaultConfig,
    inbox_dir: str,
    source: str,
    title: str,
    text: str,
    metadata: dict[str, object] | None = None,
    trailing_lines: list[str] | None = None,
    timestamp: datetime | None = None,
    filename: str | None = None,
) -> Path:
    capture_time = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    note_body = build_capture_note_body(
        source=source,
        title=title,
        text=text,
        metadata=metadata,
        trailing_lines=trailing_lines,
    )
    note_path = write_capture_note(
        vault=vault,
        inbox_dir=inbox_dir,
        timestamp=capture_time,
        filename=filename or default_capture_filename(capture_time, source),
        body=note_body,
    )
    index_capture_note(connection, vault, note_path)
    return note_path


TEXT_IMPORT_SUFFIXES = {".md", ".txt", ".rst", ".log", ".csv"}


def read_import_text(path: Path) -> str | None:
    if path.suffix.casefold() not in TEXT_IMPORT_SUFFIXES:
        return None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def default_import_title(source_path: Path) -> str:
    return f"Imported File: {source_path.name}"


def default_import_filename(timestamp: datetime, source_path: Path) -> str:
    safe_stem = "".join(ch if ch.isalnum() else "-" for ch in source_path.stem).strip("-").lower() or "imported"
    return f"{timestamp.strftime('%H%M%S-%f')}-import-{safe_stem}.md"


def import_file_to_inbox(
    connection,
    *,
    vault: VaultConfig,
    inbox_dir: str,
    source_path: Path,
    title: str | None = None,
    timestamp: datetime | None = None,
) -> Path:
    capture_time = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_path = source_path.expanduser().resolve()
    imported_text = read_import_text(source_path)
    metadata = {
        "imported_from": str(source_path),
        "imported_name": source_path.name,
        "imported_at": capture_time.isoformat(),
    }
    trailing_lines: list[str] = []
    if imported_text is None:
        body_text = f"Imported file placeholder for {source_path.name}."
        trailing_lines.extend(
            [
                f"Original file: `{source_path}`",
                f"File size: {source_path.stat().st_size} bytes",
                "Review this file manually.",
                "",
            ]
        )
    else:
        body_text = imported_text.strip()
        trailing_lines.extend([f"Original file: `{source_path}`", ""])
    return capture_text_to_inbox(
        connection,
        vault=vault,
        inbox_dir=inbox_dir,
        source="import",
        title=title or default_import_title(source_path),
        text=body_text,
        metadata=metadata,
        trailing_lines=trailing_lines,
        timestamp=capture_time,
        filename=default_import_filename(capture_time, source_path),
    )
