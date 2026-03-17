from __future__ import annotations

import asyncio
import html
import json
import re
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from lk_agent.actions.digest import build_digest_text
from lk_agent.agents.memory import read_shared_memory, read_shared_memory_bundle
from lk_agent.core.models import AppConfig, VaultConfig
from lk_agent.core.secrets import ENV_TELEGRAM_TOKEN, resolve_telegram_token
from lk_agent.vault.index import search_notes
from lk_agent.vault.parser import parse_markdown
from lk_agent.vault.storage import sync_vault_record, upsert_note

try:
    from telegram import Bot
except ImportError:
    Bot = None


class TelegramConfigError(RuntimeError):
    """Raised when Telegram integration is misconfigured."""


MAX_REPLY_CHARS = 3500
COMMAND_PREFIXES = (
    "/help",
    "/capture",
    "/search",
    "/summarize",
    "/dailybrief",
    "/tasks",
    "/recent",
    "/triage",
    "/followups",
    "/taskrollup",
    "/stale",
    "/untagged",
    "/brokenlinks",
)
LEGACY_COMMAND_ALIASES = {
    "/daily-brief": "dailybrief",
    "/task-rollup": "taskrollup",
}
MEMORY_LABELS = {
    "inbox-state": "Inbox",
    "digest-state": "Digest",
    "maintenance-state": "Maintenance",
}
ABSOLUTE_PATH_RE = re.compile(r"(?P<path>(?:[A-Za-z]:[\\/]|/)[^\s<>]+)")
ABSOLUTE_PATH_LINE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/).+$")
TASK_SOURCE_FILTER = "AND COALESCE(m.source_type, 'note') <> 'summary'"


def dependency_available() -> bool:
    return Bot is not None


def require_dependency() -> None:
    if Bot is None:
        raise TelegramConfigError(
            "python-telegram-bot is not installed. Add the dependency before using Telegram commands."
        )


def ensure_ready(config: AppConfig) -> str:
    require_dependency()
    token = resolve_telegram_token(config.telegram, config)
    if not token:
        raise TelegramConfigError("telegram bot token is not configured")
    if not config.telegram.inbox_vault:
        raise TelegramConfigError("telegram inbox vault is not configured")
    return token


async def get_bot_identity(config: AppConfig) -> dict[str, object]:
    token = ensure_ready(config)
    bot = Bot(token=token)
    me = await bot.get_me()
    return {
        "id": me.id,
        "username": me.username,
        "full_name": me.full_name,
        "can_join_groups": getattr(me, "can_join_groups", None),
        "can_read_all_group_messages": getattr(me, "can_read_all_group_messages", None),
        "supports_inline_queries": getattr(me, "supports_inline_queries", None),
    }


async def fetch_updates(config: AppConfig, offset: int | None, limit: int) -> list[object]:
    token = ensure_ready(config)
    bot = Bot(token=token)
    return await bot.get_updates(offset=offset, timeout=0, limit=limit, allowed_updates=["message"])


def render_plain_segment_html(text: str) -> str:
    escaped = html.escape(text)
    return ABSOLUTE_PATH_RE.sub(lambda match: f"<code>{html.escape(match.group('path'))}</code>", escaped)


def render_inline_html(text: str) -> str:
    parts = text.split("`")
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            rendered.append(f"<code>{html.escape(part)}</code>")
        else:
            rendered.append(render_plain_segment_html(part))
    return "".join(rendered)


def render_telegram_html(text: str) -> str:
    lines = text.strip().splitlines()
    rendered_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            rendered_lines.append("")
            continue
        if stripped.startswith("## "):
            rendered_lines.append(f"<b>{render_inline_html(stripped[3:])}</b>")
            continue
        if stripped.startswith("# "):
            rendered_lines.append(f"<b>{render_inline_html(stripped[2:])}</b>")
            continue
        if stripped.startswith("- "):
            rendered_lines.append(f"• {render_inline_html(stripped[2:])}")
            continue
        if line.startswith("  ") or line.startswith("\t"):
            if ABSOLUTE_PATH_LINE_RE.match(stripped):
                rendered_lines.append(f"&nbsp;&nbsp;<code>{html.escape(stripped)}</code>")
            else:
                rendered_lines.append(f"&nbsp;&nbsp;{render_inline_html(stripped)}")
            continue
        rendered_lines.append(render_inline_html(stripped))
    rendered = "\n".join(rendered_lines).strip()
    if len(rendered) <= MAX_REPLY_CHARS:
        return rendered
    fallback = html.escape(text[: max(0, MAX_REPLY_CHARS - 3)]).strip()
    return f"{fallback}..."


async def send_text(config: AppConfig, chat_id: int, text: str) -> None:
    token = ensure_ready(config)
    bot = Bot(token=token)
    await bot.send_message(chat_id=chat_id, text=render_telegram_html(text), parse_mode="HTML")


def poll_once(connection: sqlite3.Connection, config: AppConfig, limit: int = 20) -> list[dict[str, object]]:
    offset = load_offset(connection)
    updates = asyncio.run(fetch_updates(config, offset, limit))
    if not updates:
        return []

    vault = resolve_vault(config, config.telegram.inbox_vault)
    results: list[dict[str, object]] = []
    max_update_id = offset - 1 if offset is not None else -1
    for update in updates:
        if update.update_id > max_update_id:
            max_update_id = update.update_id
        message = update.effective_message
        if message is None:
            continue
        chat_id = int(message.chat_id)
        if config.telegram.allowed_chat_ids and chat_id not in config.telegram.allowed_chat_ids:
            continue
        result = ingest_message(connection, vault, config, message)
        if result is not None:
            results.append(result)

    save_offset(connection, max_update_id + 1)
    connection.commit()
    return results


def ingest_message(connection: sqlite3.Connection, vault: VaultConfig, config: AppConfig, message: object) -> dict[str, object] | None:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    file_ref = extract_file_ref(message)
    received_at = message.date.astimezone(timezone.utc).isoformat()
    command_name = extract_command_name(text)
    note_path: Path | None = None
    if should_capture_to_note(text):
        note_path = write_inbox_note(vault, config.telegram.inbox_dir, message, text, file_ref)
        index_inbox_note(connection, vault, note_path)
    reply_text = handle_command(connection, config, vault, command_name, text, note_path) if command_name else None
    connection.execute(
        """
        INSERT OR IGNORE INTO telegram_messages (
            telegram_message_id, chat_id, sender_ref, message_type, text_excerpt,
            file_ref, received_at, mapped_note_path, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(message.message_id),
            int(message.chat_id),
            str(message.from_user.username if message.from_user else message.chat_id),
            classify_message(message),
            text[:500],
            file_ref,
            received_at,
            str(note_path) if note_path else None,
            json.dumps(message.to_dict(), ensure_ascii=False),
        ),
    )
    if reply_text:
        asyncio.run(send_text(config, int(message.chat_id), reply_text))
    return {
        "chat_id": int(message.chat_id),
        "message_id": int(message.message_id),
        "message_type": classify_message(message),
        "note_path": str(note_path) if note_path else None,
        "command": command_name,
    }


def handle_command(
    connection: sqlite3.Connection,
    config: AppConfig,
    vault: VaultConfig,
    command_name: str,
    text: str,
    note_path: Path | None,
) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    if command_name == "help":
        return build_help_text()
    if command_name == "capture":
        if note_path is None:
            return "Capture failed"
        return f"Captured to {note_path}"
    if command_name == "search":
        query = stripped.split(maxsplit=1)[1].strip() if len(stripped.split(maxsplit=1)) > 1 else ""
        if not query:
            return "Usage: /search <query>"
        rows = search_notes(connection, query, limit=5)
        if not rows:
            return f"No matches for: {query}"
        lines = [f"Search results for: {query}", ""]
        for row in rows:
            lines.append(f"- {row['title']}")
            lines.append(f"  {row['absolute_path']}")
        return "\n".join(lines)
    if command_name == "summarize":
        return build_brief_text(connection, vault, title="Summary", memory_keys=["digest-state", "maintenance-state"])
    if command_name == "dailybrief":
        return build_brief_text(connection, vault, title="Daily Brief", memory_keys=["inbox-state", "digest-state", "maintenance-state"])
    if command_name == "tasks":
        return build_tasks_text(connection, vault)
    if command_name == "recent":
        return build_recent_text(connection, vault)
    if command_name == "triage":
        return build_triage_text(connection, vault)
    if command_name == "followups":
        return build_followups_text(connection, vault)
    if command_name == "taskrollup":
        return build_taskrollup_text(connection, vault)
    if command_name == "stale":
        return build_stale_text(connection, vault)
    if command_name == "untagged":
        return build_untagged_text(connection, vault)
    if command_name == "brokenlinks":
        return build_brokenlinks_text(connection, vault)
    return None


def build_help_text() -> str:
    return "\n".join(
        [
            "# Telegram Commands",
            "",
            "Use slash commands only. Everything stays local to your vault.",
            "",
            "## Capture",
            "",
            "- /capture <text> - store a quick inbox note",
            "",
            "## Search And Briefs",
            "",
            "- /search <query> - search indexed notes",
            "- /summarize - read digest and maintenance memory",
            "- /dailybrief - read inbox, digest, and maintenance memory",
            "",
            "## Inbox Review",
            "",
            "- /tasks - show current open tasks",
            "- /recent - show recently indexed notes",
            "- /triage - show the latest inbox triage note",
            "- /followups - show the inbox follow-up queue",
            "- /taskrollup - show the inbox task rollup",
            "",
            "## Maintenance",
            "",
            "- /stale - show stale-note review",
            "- /untagged - show untagged-note review",
            "- /brokenlinks - show broken internal links",
            "",
            "## Utility",
            "",
            "- /help - show this command list",
        ]
    ).strip()


def build_brief_text(connection: sqlite3.Connection, vault: VaultConfig, title: str, memory_keys: list[str]) -> str:
    sections = read_shared_memory_bundle(connection, vault.name, memory_keys)
    if sections:
        lines = [f"# {title}", "", f"Vault: {vault.name}", ""]
        for key, body in sections:
            label = MEMORY_LABELS.get(key, key)
            lines.append(f"## {label}")
            lines.append("")
            lines.extend(body.splitlines())
            lines.append("")
        return "\n".join(lines[:40]).strip()
    digest = build_digest_text(connection, vault)
    lines = digest.splitlines()
    lines[0] = f"# {title}"
    return "\n".join(lines[:14]).strip()


def build_tasks_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    rows = connection.execute(
        f"""
        SELECT t.text, n.title, n.absolute_path, n.updated_at
        FROM tasks AS t
        JOIN notes AS n ON n.id = t.note_id
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE t.status = 'open'
          AND n.vault_id = (SELECT id FROM vaults WHERE name = ?)
          {TASK_SOURCE_FILTER}
        ORDER BY n.updated_at DESC, t.id DESC
        LIMIT 10
        """,
        (vault.name,),
    ).fetchall()
    if not rows:
        return f"No open tasks in vault: {vault.name}"
    lines = ["# Open Tasks", "", f"Vault: {vault.name}", ""]
    for row in rows:
        lines.append(f"- {row['text']} [{row['title']}]")
    return "\n".join(lines).strip()


def build_recent_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    rows = connection.execute(
        """
        SELECT n.title, n.absolute_path, n.updated_at, m.source_type
        FROM notes AS n
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE n.vault_id = (SELECT id FROM vaults WHERE name = ?)
        ORDER BY n.updated_at DESC
        LIMIT 8
        """,
        (vault.name,),
    ).fetchall()
    if not rows:
        return f"No indexed notes in vault: {vault.name}"
    lines = ["# Recent Notes", "", f"Vault: {vault.name}", ""]
    for row in rows:
        lines.append(f"- {row['title']} ({row['source_type']})")
    return "\n".join(lines).strip()


def build_triage_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    relative_path = f"Reports/{vault.name}-inbox-agent-triage.md"
    note_text = read_generated_report_text(connection, vault, relative_path)
    if note_text:
        return note_text
    inbox_state = read_shared_memory(connection, vault.name, "inbox-state")
    if inbox_state:
        return "\n".join(["# Inbox Triage", "", f"Vault: {vault.name}", "", inbox_state]).strip()
    return (
        f"No inbox triage note is available for vault: {vault.name}\n\n"
        f"Run the inbox agent first to build triage output."
    )


def build_followups_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    relative_path = f"Reports/{vault.name}-inbox-agent-followups.md"
    note_text = read_generated_report_text(connection, vault, relative_path)
    if note_text:
        return note_text
    inbox_state = read_shared_memory(connection, vault.name, "inbox-state")
    if inbox_state:
        return "\n".join(["# Inbox Follow-Ups", "", f"Vault: {vault.name}", "", inbox_state]).strip()
    return (
        f"No inbox follow-up queue is available for vault: {vault.name}\n\n"
        f"Run the inbox agent first to build follow-up output."
    )


def build_taskrollup_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    relative_path = f"Reports/{vault.name}-inbox-agent-tasks.md"
    note_text = read_generated_report_text(connection, vault, relative_path)
    if note_text:
        return note_text
    return build_tasks_text(connection, vault)


def build_stale_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    note_text = read_generated_report_text(connection, vault, "Reports/Stale.md")
    if note_text:
        return note_text
    maintenance_state = read_shared_memory(connection, vault.name, "maintenance-state")
    if maintenance_state:
        return "\n".join(["# Stale", "", f"Vault: {vault.name}", "", maintenance_state]).strip()
    return f"No stale-note report is available for vault: {vault.name}\n\nRun the maintenance agent first."


def build_untagged_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    note_text = read_generated_report_text(connection, vault, "Reports/Untagged.md")
    if note_text:
        return note_text
    maintenance_state = read_shared_memory(connection, vault.name, "maintenance-state")
    if maintenance_state:
        return "\n".join(["# Untagged", "", f"Vault: {vault.name}", "", maintenance_state]).strip()
    return f"No untagged-note report is available for vault: {vault.name}\n\nRun the maintenance agent first."


def build_brokenlinks_text(connection: sqlite3.Connection, vault: VaultConfig) -> str:
    note_text = read_generated_report_text(connection, vault, "Reports/BrokenLinks.md")
    if note_text:
        return note_text
    maintenance_state = read_shared_memory(connection, vault.name, "maintenance-state")
    if maintenance_state:
        return "\n".join(["# Broken Links", "", f"Vault: {vault.name}", "", maintenance_state]).strip()
    return f"No broken-link report is available for vault: {vault.name}\n\nRun the maintenance agent first."


def read_generated_report_text(connection: sqlite3.Connection, vault: VaultConfig, relative_path: str) -> str | None:
    row = connection.execute(
        "SELECT absolute_path FROM notes WHERE vault_id = (SELECT id FROM vaults WHERE name = ?) AND relative_path = ?",
        (vault.name, relative_path),
    ).fetchone()
    if row is None:
        return None
    path = Path(row["absolute_path"])
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return "\n".join(text.splitlines()[:40]).strip()


def extract_command_name(text: str) -> str | None:
    stripped = text.strip()
    for prefix in COMMAND_PREFIXES:
        if stripped.startswith(prefix):
            return prefix[1:]
    for legacy_prefix, normalized_name in LEGACY_COMMAND_ALIASES.items():
        if stripped.startswith(legacy_prefix):
            return normalized_name
    return None


def should_capture_to_note(text: str) -> bool:
    command_name = extract_command_name(text)
    return command_name is None or command_name == "capture"


def index_inbox_note(connection: sqlite3.Connection, vault: VaultConfig, note_path: Path) -> None:
    vault_id = sync_vault_record(connection, vault)
    parsed = parse_markdown(note_path)
    upsert_note(connection, vault_id, note_path, note_path.relative_to(vault.resolved_path()).as_posix(), parsed)


def classify_message(message: object) -> str:
    if getattr(message, "text", None):
        return "text"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "voice", None):
        return "voice"
    return "other"


def extract_file_ref(message: object) -> str | None:
    document = getattr(message, "document", None)
    if document is not None:
        return getattr(document, "file_name", None) or getattr(document, "file_id", None)
    voice = getattr(message, "voice", None)
    if voice is not None:
        return getattr(voice, "file_id", None)
    photo = getattr(message, "photo", None)
    if photo:
        return getattr(photo[-1], "file_id", None)
    return None


def write_inbox_note(vault: VaultConfig, inbox_dir: str, message: object, text: str, file_ref: str | None) -> Path:
    root = vault.resolved_path()
    timestamp = message.date.astimezone(timezone.utc)
    target_dir = root / inbox_dir / timestamp.strftime("%Y-%m-%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{timestamp.strftime('%H%M%S')}-chat-{int(message.chat_id)}-msg-{int(message.message_id)}.md"
    path = target_dir / filename
    sender = "unknown"
    if getattr(message, "from_user", None) is not None:
        sender = message.from_user.username or message.from_user.full_name
    body = build_note_body(timestamp, int(message.chat_id), int(message.message_id), sender, text, file_ref)
    path.write_text(body, encoding="utf-8")
    return path


def build_note_body(timestamp: datetime, chat_id: int, message_id: int, sender: str, text: str, file_ref: str | None) -> str:
    lines = [
        "---",
        "source: telegram",
        f"chat_id: {chat_id}",
        f"message_id: {message_id}",
        f"sender: {json.dumps(sender, ensure_ascii=False)}",
        f"received_at: {timestamp.isoformat()}",
    ]
    if file_ref:
        lines.append(f"file_ref: {json.dumps(file_ref, ensure_ascii=False)}")
    lines.extend(["---", "", "# Telegram Inbox Capture", ""])
    if text:
        lines.append(text.strip())
        lines.append("")
    if file_ref:
        lines.append(f"Attachment reference: `{file_ref}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def resolve_vault(config: AppConfig, name: str | None) -> VaultConfig:
    if not name:
        raise TelegramConfigError("telegram inbox vault is not configured")
    for vault in config.vaults:
        if vault.name == name:
            return vault
    raise TelegramConfigError(f"configured telegram inbox vault does not exist: {name}")


def load_offset(connection: sqlite3.Connection) -> int | None:
    row = connection.execute("SELECT value FROM app_state WHERE key = 'telegram_update_offset'").fetchone()
    if row is None:
        return None
    return int(row["value"])


def save_offset(connection: sqlite3.Connection, offset: int) -> None:
    connection.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES ('telegram_update_offset', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (str(offset),),
    )


def config_summary(config: AppConfig) -> dict[str, object]:
    telegram = asdict(config.telegram)
    telegram["bot_token"] = "<legacy-plain-text>" if config.telegram.bot_token else None
    telegram["token_available"] = bool(resolve_telegram_token(config.telegram, config))
    telegram["env_override_name"] = ENV_TELEGRAM_TOKEN
    return telegram
