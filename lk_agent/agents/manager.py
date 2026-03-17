from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lk_agent.actions.digest import build_digest_text, index_generated_note
from lk_agent.agents.memory import write_agent_memory, write_shared_memory
from lk_agent.agents.triage import classify_inbox_item, llm_assist_candidates_for_item, summarize_decisions
from lk_agent.core.models import AppConfig, VaultConfig
from lk_agent.vault.parser import parse_markdown


TASK_SOURCE_FILTER = "AND COALESCE(m.source_type, 'note') <> 'summary'"
NOTE_SOURCE_FILTER = "AND COALESCE(m.source_type, 'note') <> 'summary'"
MAX_INBOX_NOTES = 25
MAX_OPEN_TASKS = 20
MAX_CANONICAL_QUESTIONS = 15
MAX_CANONICAL_FOLLOWUPS = 20
MAX_CANONICAL_TASKS = 25
MAX_CANONICAL_STALE = 25
MAX_CANONICAL_UNTAGGED = 25
MAX_CANONICAL_BROKEN = 25
MAX_CANONICAL_ORPHANS = 25
MAX_CANONICAL_DUPLICATES = 25
MAX_REPORT_ITEMS = 10
MANAGED_START = "<!-- lk_agent:generated start -->"
MANAGED_END = "<!-- lk_agent:generated end -->"


@dataclass(slots=True)
class AgentRecord:
    name: str
    kind: str
    vault_name: str
    output_path: str | None
    config: dict[str, object]
    prompt: str
    enabled: bool
    last_run_at: str | None
    last_status: str | None
    last_error: str | None


def bootstrap_default_agents(connection: sqlite3.Connection, vault_name: str) -> list[str]:
    specs = [
        (f"{vault_name}-inbox", "inbox", f"Reports/{vault_name}-inbox-agent.md"),
        (f"{vault_name}-digest", "digest", f"Reports/{vault_name}-digest-agent.md"),
        (f"{vault_name}-maintenance", "maintenance", f"Reports/{vault_name}-maintenance-agent.md"),
    ]
    for name, kind, output_path in specs:
        connection.execute(
            """
            INSERT INTO agents (name, kind, vault_name, output_path, config_json, prompt, enabled)
            VALUES (?, ?, ?, ?, '{}', '', 1)
            ON CONFLICT(name) DO UPDATE SET
                kind = excluded.kind,
                vault_name = excluded.vault_name,
                output_path = excluded.output_path,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, kind, vault_name, output_path),
        )
    connection.commit()
    return [name for name, _, _ in specs]


def add_or_update_agent(
    connection: sqlite3.Connection,
    name: str,
    kind: str,
    vault_name: str,
    output_path: str | None,
    prompt: str = "",
    config: dict[str, object] | None = None,
    enabled: bool = True,
) -> None:
    connection.execute(
        """
        INSERT INTO agents (name, kind, vault_name, output_path, config_json, prompt, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            kind = excluded.kind,
            vault_name = excluded.vault_name,
            output_path = excluded.output_path,
            config_json = excluded.config_json,
            prompt = excluded.prompt,
            enabled = excluded.enabled,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, kind, vault_name, output_path, json.dumps(config or {}, ensure_ascii=False), prompt, 1 if enabled else 0),
    )
    connection.commit()


def list_agents(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT name, kind, vault_name, output_path, enabled, last_run_at, last_status, last_error
        FROM agents
        ORDER BY name
        """
    ).fetchall()


def list_agent_status(connection: sqlite3.Connection, name: str | None = None) -> list[dict[str, object]]:
    rows = list_agents(connection)
    result: list[dict[str, object]] = []
    for row in rows:
        if name and row["name"] != name:
            continue
        memory_row = connection.execute(
            """
            SELECT memory_key, state_json, backing_path, updated_at
            FROM agent_memory
            WHERE agent_name = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (row["name"],),
        ).fetchone()
        state: dict[str, object] = {}
        memory_path = None
        memory_updated_at = None
        managed_paths: dict[str, object] = {}
        if memory_row is not None:
            state = json.loads(memory_row["state_json"])
            memory_path = memory_row["backing_path"]
            memory_updated_at = memory_row["updated_at"]
            managed_paths = {
                key: value
                for key, value in state.items()
                if key.endswith("_path")
            }
        result.append(
            {
                "name": row["name"],
                "kind": row["kind"],
                "vault_name": row["vault_name"],
                "output_path": row["output_path"],
                "enabled": bool(row["enabled"]),
                "last_run_at": row["last_run_at"],
                "last_status": row["last_status"],
                "last_error": row["last_error"],
                "memory_path": memory_path,
                "memory_updated_at": memory_updated_at,
                "managed_paths": managed_paths,
            }
        )
    return result



def run_agent(connection: sqlite3.Connection, config: AppConfig, name: str, root: Path | None = None) -> Path:
    row = connection.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"unknown agent: {name}")
    return execute_agent(connection, config, row_to_agent(row), root=root)


def execute_agent(
    connection: sqlite3.Connection,
    config: AppConfig,
    agent: AgentRecord,
    root: Path | None = None,
) -> Path:
    try:
        vault = resolve_vault(config, agent.vault_name)
        if agent.kind == "inbox":
            result = run_inbox_agent(connection, config, vault, agent, root=root)
        elif agent.kind == "digest":
            result = run_digest_agent(connection, config, vault, agent, root=root)
        elif agent.kind == "maintenance":
            result = run_maintenance_agent(connection, config, vault, agent, root=root)
        else:
            raise RuntimeError(f"unsupported agent kind: {agent.kind}")
        mark_agent(connection, agent.name, "ok", None)
        connection.commit()
        return result
    except Exception as exc:
        mark_agent(connection, agent.name, "error", str(exc))
        connection.commit()
        raise


def mark_agent(connection: sqlite3.Connection, name: str, status: str, error: str | None) -> None:
    connection.execute(
        """
        UPDATE agents
        SET last_run_at = ?, last_status = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
        WHERE name = ?
        """,
        (datetime.now(timezone.utc).isoformat(), status, error, name),
    )


def row_to_agent(row: sqlite3.Row) -> AgentRecord:
    return AgentRecord(
        name=row["name"],
        kind=row["kind"],
        vault_name=row["vault_name"],
        output_path=row["output_path"],
        config=json.loads(row["config_json"]),
        prompt=row["prompt"],
        enabled=bool(row["enabled"]),
        last_run_at=row["last_run_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
    )


def run_inbox_agent(
    connection: sqlite3.Connection,
    config: AppConfig,
    vault: VaultConfig,
    agent: AgentRecord,
    root: Path | None = None,
) -> Path:
    notes = connection.execute(
        f"""
        SELECT n.title, n.relative_path, n.absolute_path, n.updated_at
        FROM notes AS n
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE n.vault_id = (SELECT id FROM vaults WHERE name = ?)
          AND m.source_type = 'inbox'
        ORDER BY n.updated_at DESC
        LIMIT {MAX_INBOX_NOTES}
        """,
        (vault.name,),
    ).fetchall()
    tasks = connection.execute(
        f"""
        SELECT t.text, n.title, n.relative_path, n.absolute_path, n.updated_at, m.source_type
        FROM tasks AS t
        JOIN notes AS n ON n.id = t.note_id
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE t.status = 'open'
          AND n.vault_id = (SELECT id FROM vaults WHERE name = ?)
          {TASK_SOURCE_FILTER}
        ORDER BY n.updated_at DESC
        LIMIT {MAX_OPEN_TASKS}
        """,
        (vault.name,),
    ).fetchall()

    reviewed_notes: list[dict[str, object]] = []
    attachment_count = 0
    question_count = 0
    for row in notes:
        preview = ""
        sender = None
        file_ref = None
        frontmatter: dict[str, object] = {}
        path = Path(row["absolute_path"])
        if path.exists():
            parsed = parse_markdown(path)
            frontmatter = parsed.frontmatter
            sender = parsed.frontmatter.get("sender")
            file_ref = parsed.frontmatter.get("file_ref")
            preview = summarize_body(parsed.body)
        if should_skip_inbox_note(frontmatter):
            continue
        attachment_count += 1 if file_ref else 0
        question_count += 1 if "?" in preview and not is_answered_frontmatter(frontmatter) else 0
        decisions = classify_inbox_item(
            {
                "title": row["title"],
                "absolute_path": row["absolute_path"],
                "sender": sender,
                "file_ref": file_ref,
                "preview": preview,
                "frontmatter": frontmatter,
            },
            has_open_tasks=bool(tasks),
        )
        reviewed_notes.append(
            {
                "title": row["title"],
                "relative_path": row["relative_path"],
                "absolute_path": row["absolute_path"],
                "updated_at": row["updated_at"],
                "sender": sender,
                "file_ref": file_ref,
                "preview": preview,
                "frontmatter": frontmatter,
                "decisions": decisions,
                "llm_assist_candidates": llm_assist_candidates_for_item(
                    {
                        "title": row["title"],
                        "absolute_path": row["absolute_path"],
                        "sender": sender,
                        "file_ref": file_ref,
                        "preview": preview,
                "frontmatter": frontmatter,
                    }
                ),
            }
        )

    active_tasks: list[sqlite3.Row] = []
    for row in tasks:
        if str(row["source_type"] or "") != "inbox":
            active_tasks.append(row)
            continue
        task_path = Path(str(row["absolute_path"] or ""))
        if not task_path.exists():
            active_tasks.append(row)
            continue
        if not should_skip_inbox_note(parse_markdown(task_path).frontmatter):
            active_tasks.append(row)

    selected_questions = select_question_notes(reviewed_notes, MAX_CANONICAL_QUESTIONS)
    selected_followups = select_followup_notes(reviewed_notes, MAX_CANONICAL_FOLLOWUPS)
    selected_tasks = select_open_tasks(active_tasks, MAX_CANONICAL_TASKS)

    suggested_actions = build_inbox_actions(reviewed_notes, selected_tasks)
    report_body = build_inbox_report_body(vault, reviewed_notes, selected_tasks, attachment_count, question_count, suggested_actions)
    shared_body = build_inbox_shared_body(reviewed_notes, selected_tasks, attachment_count, question_count, suggested_actions)
    triage_body = build_inbox_triage_body(reviewed_notes, selected_tasks, suggested_actions)
    followups_body = build_inbox_followups_body(selected_followups)
    task_rollup_body = build_inbox_task_rollup_body(selected_tasks)
    questions_body = build_canonical_questions_body(vault, selected_questions)
    canonical_followups_body = build_canonical_followups_body(vault, selected_followups)
    canonical_tasks_body = build_canonical_tasks_body(vault, selected_tasks)

    triage_path = derive_sidecar_output_path(agent.output_path, "-triage.md")
    followups_path = derive_sidecar_output_path(agent.output_path, "-followups.md")
    tasks_path = derive_sidecar_output_path(agent.output_path, "-tasks.md")
    questions_path = "Reports/Questions.md"
    canonical_followups_path = "Reports/Followups.md"
    canonical_tasks_path = "Reports/Tasks.md"

    write_shared_memory(connection, config, vault.name, "inbox-state", f"{vault.name} Inbox State", shared_body, len(reviewed_notes), root=root)
    write_agent_output(connection, vault, triage_path, "Inbox Triage Summary", triage_body)
    write_agent_output(connection, vault, followups_path, "Inbox Follow-Up Queue", followups_body)
    write_agent_output(connection, vault, tasks_path, "Inbox Task Rollup", task_rollup_body)
    write_managed_output(connection, vault, questions_path, "Questions", questions_body)
    write_managed_output(connection, vault, canonical_followups_path, "Followups", canonical_followups_body)
    write_managed_output(connection, vault, canonical_tasks_path, "Tasks", canonical_tasks_body)
    write_agent_memory(
        connection,
        config,
        agent.name,
        "latest",
        f"{agent.name} Memory",
        report_body,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "recent_inbox_notes": len(reviewed_notes),
            "open_tasks": len(selected_tasks),
            "attachments_pending_review": attachment_count,
            "questions_detected": len(selected_questions),
            "suggested_actions": suggested_actions,
            "triage_path": triage_path,
            "followups_path": followups_path,
            "tasks_path": tasks_path,
            "questions_path": questions_path,
            "canonical_followups_path": canonical_followups_path,
            "canonical_tasks_path": canonical_tasks_path,
            "deterministic_decisions": [item["decisions"] for item in reviewed_notes],
        },
        root=root,
    )
    return write_agent_output(connection, vault, agent.output_path, "Inbox Agent Report", report_body)


def run_digest_agent(
    connection: sqlite3.Connection,
    config: AppConfig,
    vault: VaultConfig,
    agent: AgentRecord,
    root: Path | None = None,
) -> Path:
    text = build_digest_text(connection, vault).strip()
    write_shared_memory(connection, config, vault.name, "digest-state", f"{vault.name} Digest State", text, 0, root=root)
    write_agent_memory(
        connection,
        config,
        agent.name,
        "latest",
        f"{agent.name} Memory",
        text,
        {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "digest"},
        root=root,
    )
    return write_agent_output(connection, vault, agent.output_path, "Digest Agent Report", text)


def run_maintenance_agent(
    connection: sqlite3.Connection,
    config: AppConfig,
    vault: VaultConfig,
    agent: AgentRecord,
    root: Path | None = None,
) -> Path:
    threshold = datetime.now(timezone.utc) - timedelta(days=30)
    rows = connection.execute(
        f"""
        SELECT n.id AS note_id, n.title, n.relative_path, n.absolute_path, n.last_seen_mtime, n.updated_at, m.frontmatter_json, m.tags_json, m.links_json, m.source_type
        FROM notes AS n
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE n.vault_id = (SELECT id FROM vaults WHERE name = ?)
          {NOTE_SOURCE_FILTER}
        ORDER BY n.updated_at DESC
        LIMIT 150
        """,
        (vault.name,),
    ).fetchall()
    stale: list[sqlite3.Row] = []
    untagged: list[sqlite3.Row] = []
    known_relative_paths = {str(row["relative_path"] or "") for row in rows}
    for row in rows:
        frontmatter = load_frontmatter(row)
        if should_include_stale(row, frontmatter, threshold):
            stale.append(row)
        tags = json.loads(row["tags_json"])
        if should_include_untagged(row, frontmatter, tags):
            untagged.append(row)
    broken_links = select_broken_links(rows, known_relative_paths, MAX_CANONICAL_BROKEN)
    orphan_notes = select_orphan_notes(rows, known_relative_paths, MAX_CANONICAL_ORPHANS)
    duplicate_clusters = select_duplicate_clusters(rows, MAX_CANONICAL_DUPLICATES)
    open_tasks = connection.execute(
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
    task_count_rows = connection.execute(
        f"""
        SELECT t.note_id, COUNT(*) AS open_count
        FROM tasks AS t
        JOIN notes AS n ON n.id = t.note_id
        JOIN note_metadata AS m ON m.note_id = n.id
        WHERE t.status = 'open'
          AND n.vault_id = (SELECT id FROM vaults WHERE name = ?)
          {TASK_SOURCE_FILTER}
        GROUP BY t.note_id
        """,
        (vault.name,),
    ).fetchall()
    task_counts = {int(row["note_id"]): int(row["open_count"]) for row in task_count_rows}

    selected_stale = select_stale_notes(stale, task_counts, MAX_CANONICAL_STALE)
    selected_untagged = select_untagged_notes(untagged, MAX_CANONICAL_UNTAGGED)

    body = build_maintenance_report_body(
        vault,
        selected_stale,
        selected_untagged,
        broken_links,
        orphan_notes,
        duplicate_clusters,
        int(open_tasks["open_count"]) if open_tasks else 0,
    )
    stale_body = build_canonical_stale_body(vault, selected_stale)
    untagged_body = build_canonical_untagged_body(vault, selected_untagged)
    broken_links_body = build_canonical_broken_links_body(vault, broken_links)
    orphan_body = build_canonical_orphans_body(vault, orphan_notes)
    duplicates_body = build_canonical_duplicates_body(vault, duplicate_clusters)
    stale_path = "Reports/Stale.md"
    untagged_path = "Reports/Untagged.md"
    broken_links_path = "Reports/BrokenLinks.md"
    orphan_path = "Reports/Orphans.md"
    duplicates_path = "Reports/Duplicates.md"

    write_shared_memory(connection, config, vault.name, "maintenance-state", f"{vault.name} Maintenance State", body, len(rows), root=root)
    write_managed_output(connection, vault, stale_path, "Stale", stale_body)
    write_managed_output(connection, vault, untagged_path, "Untagged", untagged_body)
    write_managed_output(connection, vault, broken_links_path, "BrokenLinks", broken_links_body)
    write_managed_output(connection, vault, orphan_path, "Orphans", orphan_body)
    write_managed_output(connection, vault, duplicates_path, "Duplicates", duplicates_body)
    write_agent_memory(
        connection,
        config,
        agent.name,
        "latest",
        f"{agent.name} Memory",
        body,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sampled_notes": len(rows),
            "stale_notes": len(selected_stale),
            "untagged_notes": len(selected_untagged),
            "broken_links": len(broken_links),
            "orphan_notes": len(orphan_notes),
            "duplicate_clusters": len(duplicate_clusters),
            "stale_path": stale_path,
            "untagged_path": untagged_path,
            "broken_links_path": broken_links_path,
            "orphan_path": orphan_path,
            "duplicates_path": duplicates_path,
        },
        root=root,
    )
    return write_agent_output(connection, vault, agent.output_path, "Maintenance Agent Report", body)


def summarize_body(body: str) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    filtered = [line for line in lines if line != "# Telegram Inbox Capture"]
    if not filtered:
        return ""
    preview = filtered[0]
    return preview[:160]


def parse_timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def reviewed_note_priority(item: dict[str, object]) -> tuple[int, datetime, str]:
    decisions = set(item.get("decisions", []))
    score = 0
    if "answer_needed" in decisions:
        score += 8
    if "attachment_review" in decisions:
        score += 4
    if "task_follow_up" in decisions:
        score += 3
    if "review_needed" in decisions:
        score += 1
    if "command_capture" in decisions and len(decisions) == 2 and "review_needed" in decisions:
        score -= 3
    if item.get("sender"):
        score += 1
    if item.get("preview"):
        score += 1
    return (score, parse_timestamp(item.get("updated_at")), str(item.get("title") or ""))


def select_question_notes(reviewed_notes: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    selected = [
        item
        for item in reviewed_notes
        if "answer_needed" in item.get("decisions", []) and not is_answered_frontmatter(item.get("frontmatter", {}))
    ]
    selected.sort(key=reviewed_note_priority, reverse=True)
    return selected[:limit]


def select_followup_notes(reviewed_notes: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    selected = [
        item
        for item in reviewed_notes
        if set(item.get("decisions", [])) != {"command_capture", "review_needed"}
    ]
    selected.sort(key=reviewed_note_priority, reverse=True)
    return selected[:limit]


def select_open_tasks(tasks: list[sqlite3.Row], limit: int) -> list[sqlite3.Row]:
    seen: set[tuple[str, str]] = set()
    selected: list[sqlite3.Row] = []
    for row in tasks:
        key = (str(row["text"]), str(row["title"]))
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def stale_priority(row: sqlite3.Row, open_task_count: int) -> tuple[int, int, datetime, str]:
    relative_path = str(row["relative_path"] or "")
    parts = {part.casefold() for part in Path(relative_path).parts}
    tags = json.loads(row["tags_json"] or "[]")
    links = json.loads(row["links_json"] or "[]")
    age_days = max(0, int((datetime.now(timezone.utc) - datetime.fromtimestamp(float(row["last_seen_mtime"]), tz=timezone.utc)).days))
    project_bonus = 2 if parts.intersection({"project", "projects", "area", "areas"}) else 0
    tag_bonus = 1 if tags else 0
    link_bonus = min(len(links), 3) * 2
    task_bonus = min(open_task_count, 3) * 4
    score = task_bonus + link_bonus + project_bonus + tag_bonus
    return (score, age_days, parse_timestamp(row["updated_at"]), str(row["title"] or ""))


def select_stale_notes(rows: list[sqlite3.Row], task_counts: dict[int, int], limit: int) -> list[sqlite3.Row]:
    selected = [row for row in rows if str(row["source_type"] or "") != "inbox"]
    selected.sort(
        key=lambda row: stale_priority(row, task_counts.get(int(row["note_id"]), 0)),
        reverse=True,
    )
    return selected[:limit]


def select_untagged_notes(rows: list[sqlite3.Row], limit: int) -> list[sqlite3.Row]:
    selected = [
        row
        for row in rows
        if str(row["source_type"] or "") != "inbox" and not should_ignore_maintenance(load_frontmatter(row))
    ]
    selected.sort(key=lambda row: parse_timestamp(row["updated_at"]), reverse=True)
    return selected[:limit]


def select_broken_links(rows: list[sqlite3.Row], known_relative_paths: set[str], limit: int) -> list[dict[str, str]]:
    known_paths = {normalize_relative_markdown_path(path) for path in known_relative_paths if path}
    known_stems = {Path(path).stem.casefold() for path in known_relative_paths if path}
    title_lookup = build_note_title_lookup(rows)
    selected: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        frontmatter = load_frontmatter(row)
        if str(row["source_type"] or "") == "inbox" or should_ignore_maintenance(frontmatter):
            continue
        source_relative = str(row["relative_path"] or "")
        if not source_relative:
            continue
        links = json.loads(row["links_json"] or "[]")
        for raw_target in links:
            target = str(raw_target or "").strip()
            if not target or is_external_link(target):
                continue
            resolved_target = resolve_internal_link_target(source_relative, target)
            if resolved_target is None:
                continue
            target_path, target_label = resolved_target
            if resolve_known_internal_path(target_path, target_label, known_paths, known_stems, title_lookup):
                continue
            key = (source_relative, target_label)
            if key in seen:
                continue
            seen.add(key)
            selected.append(
                {
                    "title": str(row["title"] or ""),
                    "absolute_path": str(row["absolute_path"] or ""),
                    "relative_path": source_relative,
                    "target": target_label,
                    "resolved_target": target_path,
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
    selected.sort(
        key=lambda item: (
            parse_timestamp(item["updated_at"]),
            item["title"].casefold(),
            item["target"].casefold(),
        ),
        reverse=True,
    )
    return selected[:limit]


def normalize_duplicate_key(row: sqlite3.Row) -> str:
    candidates = [str(row["title"] or "").strip(), Path(str(row["relative_path"] or "")).stem.strip()]
    for candidate in candidates:
        normalized = re.sub(r"[^a-z0-9]+", " ", candidate.casefold()).strip()
        if normalized:
            return normalized
    return ""


def select_duplicate_clusters(rows: list[sqlite3.Row], limit: int) -> list[dict[str, object]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        frontmatter = load_frontmatter(row)
        if str(row["source_type"] or "") == "inbox" or should_ignore_maintenance(frontmatter):
            continue
        key = normalize_duplicate_key(row)
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
    clusters: list[dict[str, object]] = []
    for key, cluster_rows in grouped.items():
        if len(cluster_rows) < 2:
            continue
        cluster_rows.sort(key=lambda row: (parse_timestamp(row["updated_at"]), str(row["relative_path"] or "")), reverse=True)
        clusters.append({"key": key, "display": cluster_rows[0]["title"] or key, "rows": cluster_rows})
    clusters.sort(
        key=lambda cluster: (
            len(cluster["rows"]),
            parse_timestamp(cluster["rows"][0]["updated_at"]),
            str(cluster["display"] or "").casefold(),
        ),
        reverse=True,
    )
    return clusters[:limit]


def select_orphan_notes(rows: list[sqlite3.Row], known_relative_paths: set[str], limit: int) -> list[sqlite3.Row]:
    linked_paths = collect_resolved_internal_links(rows, known_relative_paths)
    selected = []
    for row in rows:
        frontmatter = load_frontmatter(row)
        if str(row["source_type"] or "") == "inbox" or should_ignore_maintenance(frontmatter):
            continue
        relative_path = normalize_relative_markdown_path(str(row["relative_path"] or ""))
        if not relative_path or relative_path in linked_paths:
            continue
        selected.append(row)
    selected.sort(key=lambda row: parse_timestamp(row["updated_at"]))
    return selected[:limit]


def collect_resolved_internal_links(rows: list[sqlite3.Row], known_relative_paths: set[str]) -> set[str]:
    known_paths = {normalize_relative_markdown_path(path) for path in known_relative_paths if path}
    known_stems = {Path(path).stem.casefold() for path in known_relative_paths if path}
    title_lookup = build_note_title_lookup(rows)
    linked_paths: set[str] = set()
    for row in rows:
        frontmatter = load_frontmatter(row)
        if str(row["source_type"] or "") == "inbox" or should_ignore_maintenance(frontmatter):
            continue
        source_relative = str(row["relative_path"] or "")
        if not source_relative:
            continue
        links = json.loads(row["links_json"] or "[]")
        for raw_target in links:
            target = str(raw_target or "").strip()
            if not target or is_external_link(target):
                continue
            resolved_target = resolve_internal_link_target(source_relative, target)
            if resolved_target is None:
                continue
            target_path, target_label = resolved_target
            matched_path = resolve_known_internal_path(target_path, target_label, known_paths, known_stems, title_lookup)
            if matched_path:
                linked_paths.add(matched_path)
    return linked_paths


def build_note_title_lookup(rows: list[sqlite3.Row]) -> dict[str, set[str]]:
    title_lookup: dict[str, set[str]] = {}
    for row in rows:
        title = str(row["title"] or "").strip().casefold()
        relative_path = normalize_relative_markdown_path(str(row["relative_path"] or ""))
        if not title or not relative_path:
            continue
        title_lookup.setdefault(title, set()).add(relative_path)
    return title_lookup


def resolve_known_internal_path(
    target_path: str,
    target_label: str,
    known_paths: set[str],
    known_stems: set[str],
    title_lookup: dict[str, set[str]],
) -> str | None:
    if target_path in known_paths:
        return target_path
    stem = Path(target_path).stem.casefold()
    if stem in known_stems:
        stem_matches = sorted(candidate for candidate in known_paths if Path(candidate).stem.casefold() == stem)
        if len(stem_matches) == 1:
            return stem_matches[0]
    title_matches = sorted(title_lookup.get(str(target_label or "").strip().casefold(), set()))
    if len(title_matches) == 1:
        return title_matches[0]
    return None


def is_external_link(target: str) -> bool:
    lowered = target.strip().lower()
    return lowered.startswith(("http://", "https://", "mailto:", "tel:")) or lowered.startswith("#")


def resolve_internal_link_target(source_relative: str, target: str) -> tuple[str, str] | None:
    cleaned = target.strip()
    if not cleaned:
        return None
    cleaned = cleaned.split("#", 1)[0].strip()
    if not cleaned:
        return None
    if cleaned.startswith("[[") and cleaned.endswith("]]"):
        cleaned = cleaned[2:-2].strip()
    normalized = cleaned.replace("\\", "/")
    base_dir = posixpath.dirname(source_relative.replace("\\", "/"))
    if "/" in normalized or normalized.lower().endswith(".md"):
        joined = posixpath.normpath(posixpath.join(base_dir, normalized))
        if joined in {".", ""}:
            return None
        if not joined.lower().endswith(".md"):
            joined = f"{joined}.md"
        return (normalize_relative_markdown_path(joined), normalized)
    stem = Path(normalized).stem
    return (normalize_relative_markdown_path(f"{stem}.md"), normalized)


def normalize_relative_markdown_path(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if normalized in {".", ""}:
        return ""
    return normalized


def load_frontmatter(row: sqlite3.Row | dict[str, object]) -> dict[str, object]:
    raw = row["frontmatter_json"] if isinstance(row, sqlite3.Row) and "frontmatter_json" in row.keys() else row.get("frontmatter", {})
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value or "").strip().lower()
    return lowered in {"1", "true", "yes", "y", "done"}


def is_answered_frontmatter(frontmatter: dict[str, object]) -> bool:
    return is_truthy(frontmatter.get("answered")) or bool(frontmatter.get("answered_at"))


def inbox_status(frontmatter: dict[str, object]) -> str:
    return str(frontmatter.get("inbox_status") or "active").strip().casefold()


def should_skip_inbox_note(frontmatter: dict[str, object]) -> bool:
    return inbox_status(frontmatter) in {"done", "archived", "moved"}


def should_ignore_maintenance(frontmatter: dict[str, object]) -> bool:
    return is_truthy(frontmatter.get("ignore_maintenance"))


def is_recently_reviewed(frontmatter: dict[str, object], threshold: datetime) -> bool:
    reviewed_at = frontmatter.get("reviewed_at")
    if not reviewed_at:
        return False
    return parse_timestamp(reviewed_at) >= threshold


def should_include_stale(row: sqlite3.Row, frontmatter: dict[str, object], threshold: datetime) -> bool:
    if str(row["source_type"] or "") == "inbox":
        return False
    if should_ignore_maintenance(frontmatter):
        return False
    if is_recently_reviewed(frontmatter, threshold):
        return False
    return datetime.fromtimestamp(float(row["last_seen_mtime"]), tz=timezone.utc) < threshold


def should_include_untagged(row: sqlite3.Row, frontmatter: dict[str, object], tags: list[object]) -> bool:
    if str(row["source_type"] or "") == "inbox":
        return False
    if should_ignore_maintenance(frontmatter):
        return False
    return not tags


def build_inbox_actions(reviewed_notes: list[dict[str, object]], tasks: list[sqlite3.Row]) -> list[str]:
    actions: list[str] = []
    if reviewed_notes:
        actions.append("Review the newest inbox captures and either convert them into project notes or archive them.")
    if any("attachment_review" in item.get("decisions", []) for item in reviewed_notes):
        actions.append("Check inbox entries with attachments and decide whether the files need dedicated notes.")
    if any("answer_needed" in item.get("decisions", []) for item in reviewed_notes):
        actions.append("Answer or triage inbox questions before they get buried in the capture stream.")
    if tasks:
        actions.append("Promote open checkbox items from inbox notes into your main task workflow.")
    if not actions:
        actions.append("Inbox looks clear; keep captures flowing and rerun the inbox agent after new messages arrive.")
    return actions[:5]


def build_inbox_report_body(
    vault: VaultConfig,
    reviewed_notes: list[dict[str, object]],
    tasks: list[sqlite3.Row],
    attachment_count: int,
    question_count: int,
    suggested_actions: list[str],
) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Recent inbox notes sampled: {len(reviewed_notes)}",
        f"Open tasks selected: {len(tasks)}",
        f"Attachments pending review: {attachment_count}",
        f"Inbox questions detected: {question_count}",
        "",
        "## Needs Review",
        "",
    ]
    if not reviewed_notes:
        lines.append("No inbox notes indexed.")
    else:
        for item in select_followup_notes(reviewed_notes, 6):
            sender_suffix = f" from {item['sender']}" if item["sender"] else ""
            preview_suffix = f": {item['preview']}" if item["preview"] else ""
            decision_suffix = f" [{summarize_decisions(item['decisions'])}]"
            lines.append(f"- {item['title']}{sender_suffix}{preview_suffix}{decision_suffix}")
    lines.extend(["", "## Open Tasks", ""])
    if not tasks:
        lines.append("No open tasks.")
    else:
        for row in tasks[:MAX_REPORT_ITEMS]:
            lines.append(f"- {row['text']} [{row['title']}]")
    lines.extend(["", "## Suggested Next Actions", ""])
    for action in suggested_actions:
        lines.append(f"- {action}")
    return "\n".join(lines).strip()


def build_inbox_shared_body(
    reviewed_notes: list[dict[str, object]],
    tasks: list[sqlite3.Row],
    attachment_count: int,
    question_count: int,
    suggested_actions: list[str],
) -> str:
    shared_lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Recent inbox notes sampled: {len(reviewed_notes)}",
        f"Open tasks selected: {len(tasks)}",
        f"Attachments pending review: {attachment_count}",
        f"Inbox questions detected: {question_count}",
        "",
        "Suggested actions:",
    ]
    shared_lines.extend(f"- {action}" for action in suggested_actions[:4])
    return "\n".join(shared_lines).strip()


def build_inbox_triage_body(
    reviewed_notes: list[dict[str, object]],
    tasks: list[sqlite3.Row],
    suggested_actions: list[str],
) -> str:
    question_notes = select_question_notes(reviewed_notes, 8)
    lines = [
        "## Deterministic Decisions",
        "",
        "- current inbox triage is rule-based and local",
        "- future LLM use is optional and should stay advisory",
        f"- canonical question limit: {MAX_CANONICAL_QUESTIONS}",
        f"- canonical follow-up limit: {MAX_CANONICAL_FOLLOWUPS}",
        "",
        "## Questions To Answer",
        "",
    ]
    if not question_notes:
        lines.append("No direct question captures found.")
    else:
        for item in question_notes:
            sender_suffix = f" from {item['sender']}" if item.get("sender") else ""
            preview = str(item.get("preview") or "").strip()
            lines.append(f"- {item['title']}{sender_suffix}: {preview}")
    lines.extend(["", "## Open Tasks", ""])
    if not tasks:
        lines.append("No open tasks extracted from indexed notes.")
    else:
        for row in tasks[:MAX_REPORT_ITEMS]:
            lines.append(f"- {row['text']} [{row['title']}]")
    lines.extend(["", "## Follow-Up Queue", ""])
    if not reviewed_notes:
        lines.append("No inbox items to triage.")
    else:
        for item in select_followup_notes(reviewed_notes, 8):
            preview = str(item.get("preview") or "").strip()
            decisions = summarize_decisions(item.get("decisions", []))
            llm_candidates = item.get("llm_assist_candidates", [])
            llm_suffix = f"; later LLM assist: {', '.join(llm_candidates)}" if llm_candidates else ""
            lines.append(f"- Review: {item['title']} -> {preview or item['absolute_path']} [{decisions}{llm_suffix}]")
    lines.extend(["", "## Recommended Next Actions", ""])
    for action in suggested_actions:
        lines.append(f"- {action}")
    return "\n".join(lines).strip()


def build_inbox_followups_body(reviewed_notes: list[dict[str, object]]) -> str:
    lines = ["## Queue", ""]
    if not reviewed_notes:
        lines.append("No inbox items require review.")
    else:
        for item in reviewed_notes[:MAX_REPORT_ITEMS]:
            preview = str(item.get("preview") or "").strip()
            decisions = summarize_decisions(item.get("decisions", []))
            lines.append(f"- [{decisions}] {item['title']} -> {preview or item['absolute_path']}")
    return "\n".join(lines).strip()


def build_inbox_task_rollup_body(tasks: list[sqlite3.Row]) -> str:
    lines = ["## Open Tasks", ""]
    if not tasks:
        lines.append("No open tasks extracted from indexed notes.")
    else:
        for row in tasks[:MAX_REPORT_ITEMS]:
            lines.append(f"- [ ] {row['text']} ({row['title']})")
    return "\n".join(lines).strip()


def build_canonical_questions_body(vault: VaultConfig, reviewed_notes: list[dict[str, object]]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_QUESTIONS} question-like inbox captures by priority and recency",
        "",
        "## Open Questions",
        "",
    ]
    if not reviewed_notes:
        lines.append("No open question-like inbox captures detected.")
    else:
        for item in reviewed_notes:
            sender_suffix = f" from {item['sender']}" if item.get("sender") else ""
            preview = str(item.get("preview") or "").strip()
            source_path = str(item.get("relative_path") or item.get("absolute_path") or "")
            lines.append(f"- {item['title']}{sender_suffix}: {preview or item['absolute_path']}")
            if source_path:
                lines.append(f"  Source: {source_path}")
    return "\n".join(lines).strip()


def build_canonical_followups_body(vault: VaultConfig, reviewed_notes: list[dict[str, object]]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_FOLLOWUPS} follow-up items by decision priority and recency",
        "",
        "## Follow-Up Queue",
        "",
    ]
    if not reviewed_notes:
        lines.append("No inbox follow-ups are pending.")
    else:
        for item in reviewed_notes:
            preview = str(item.get("preview") or "").strip()
            decisions = summarize_decisions(item.get("decisions", []))
            source_path = str(item.get("relative_path") or item.get("absolute_path") or "")
            lines.append(f"- [{decisions}] {item['title']} -> {preview or item['absolute_path']}")
            if source_path:
                lines.append(f"  Source: {source_path}")
    return "\n".join(lines).strip()


def build_canonical_tasks_body(vault: VaultConfig, tasks: list[sqlite3.Row]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_TASKS} unique open tasks from non-generated notes",
        "",
        "## Open Tasks",
        "",
    ]
    if not tasks:
        lines.append("No open tasks extracted from indexed notes.")
    else:
        for row in tasks:
            lines.append(f"- [ ] {row['text']} ({row['title']})")
            source_path = str(row['relative_path'] or row['absolute_path'] or '')
            if source_path:
                lines.append(f"  Source: {source_path}")
    return "\n".join(lines).strip()


def build_maintenance_report_body(
    vault: VaultConfig,
    stale: list[sqlite3.Row],
    untagged: list[sqlite3.Row],
    broken_links: list[dict[str, str]],
    orphan_notes: list[sqlite3.Row],
    duplicate_clusters: list[dict[str, object]],
    open_task_count: int,
) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Open tasks: {open_task_count}",
        f"Stale notes selected: {len(stale)}",
        f"Untagged notes selected: {len(untagged)}",
        f"Broken links selected: {len(broken_links)}",
        f"Orphan notes selected: {len(orphan_notes)}",
        f"Duplicate clusters selected: {len(duplicate_clusters)}",
        "",
        "## Stale Notes",
        "",
    ]
    if not stale:
        lines.append("No stale notes in the selected set.")
    else:
        for row in stale[:MAX_REPORT_ITEMS]:
            lines.append(f"- {row['title']} ({row['absolute_path']})")
    lines.extend(["", "## Untagged Notes", ""])
    if not untagged:
        lines.append("No untagged notes in the selected set.")
    else:
        for row in untagged[:MAX_REPORT_ITEMS]:
            lines.append(f"- {row['title']} ({row['absolute_path']})")
    lines.extend(["", "## Broken Links", ""])
    if not broken_links:
        lines.append("No broken internal links detected in the selected set.")
    else:
        for item in broken_links[:MAX_REPORT_ITEMS]:
            lines.append(f"- {item['title']} -> {item['target']} ({item['absolute_path']})")
    lines.extend(["", "## Orphan Notes", ""])
    if not orphan_notes:
        lines.append("No orphan notes detected in the selected set.")
    else:
        for row in orphan_notes[:MAX_REPORT_ITEMS]:
            lines.append(f"- {row['title']} ({row['absolute_path']})")
    lines.extend(["", "## Duplicate Notes", ""])
    if not duplicate_clusters:
        lines.append("No duplicate-note clusters detected in the selected set.")
    else:
        for cluster in duplicate_clusters[:MAX_REPORT_ITEMS]:
            lines.append(f"- {cluster['display']} ({len(cluster['rows'])} notes)")
    return "\n".join(lines).strip()


def build_canonical_stale_body(vault: VaultConfig, stale: list[sqlite3.Row]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_STALE} stale notes ranked by age, open tasks, links, tags, and project-like paths",
        "",
        "## Stale Notes",
        "",
    ]
    if not stale:
        lines.append("No stale notes detected.")
    else:
        for row in stale:
            lines.append(f"- {row['title']} ({row['absolute_path']})")
    return "\n".join(lines).strip()


def untagged_area_label(row: sqlite3.Row) -> str:
    relative_path = str(row["relative_path"] or "")
    parts = [part for part in Path(relative_path).parts if part not in {".", ""}]
    if len(parts) <= 1:
        return "Root"
    return parts[0]


def group_untagged_notes(untagged: list[sqlite3.Row]) -> list[tuple[str, list[sqlite3.Row]]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in untagged:
        area = untagged_area_label(row)
        grouped.setdefault(area, []).append(row)
    return sorted(grouped.items(), key=lambda item: (item[0] != "Root", item[0].casefold()))


def build_canonical_untagged_body(vault: VaultConfig, untagged: list[sqlite3.Row]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: newest {MAX_CANONICAL_UNTAGGED} non-inbox untagged notes from non-generated sources, grouped by top-level area",
        "",
        "## Untagged Notes",
        "",
    ]
    if not untagged:
        lines.append("No untagged notes detected.")
    else:
        for area, rows in group_untagged_notes(untagged):
            lines.append(f"### {area}")
            lines.append("")
            for row in rows:
                lines.append(f"- {row['title']} ({row['absolute_path']})")
            lines.append("")
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).strip()


def build_canonical_broken_links_body(vault: VaultConfig, broken_links: list[dict[str, str]]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_BROKEN} unresolved internal links from non-inbox, non-generated notes",
        "",
        "## Broken Links",
        "",
    ]
    if not broken_links:
        lines.append("No broken internal links detected.")
    else:
        for item in broken_links:
            lines.append(f"- {item['title']} -> {item['target']}")
            lines.append(f"  {item['absolute_path']}")
    return "\n".join(lines).strip()


def build_canonical_duplicates_body(vault: VaultConfig, duplicate_clusters: list[dict[str, object]]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_DUPLICATES} duplicate-title clusters from non-inbox, non-generated notes",
        "",
        "## Duplicate Notes",
        "",
    ]
    if not duplicate_clusters:
        lines.append("No duplicate-note clusters detected.")
    else:
        for cluster in duplicate_clusters:
            lines.append(f"### {cluster['display']} ({len(cluster['rows'])} notes)")
            lines.append("")
            for row in cluster["rows"]:
                lines.append(f"- {row['title']} ({row['absolute_path']})")
            lines.append("")
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).strip()


def build_canonical_orphans_body(vault: VaultConfig, orphan_notes: list[sqlite3.Row]) -> str:
    lines = [
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"Vault: {vault.name}",
        f"Selection: top {MAX_CANONICAL_ORPHANS} non-inbox, non-generated notes with no incoming internal links",
        "",
        "## Orphan Notes",
        "",
    ]
    if not orphan_notes:
        lines.append("No orphan notes detected.")
    else:
        for row in orphan_notes:
            lines.append(f"- {row['title']} ({row['absolute_path']})")
    return "\n".join(lines).strip()


def derive_sidecar_output_path(output_path: str | None, suffix: str) -> str:
    if not output_path:
        raise RuntimeError("agent is missing output_path")
    path = Path(output_path)
    if path.suffix.lower() == ".md":
        return str(path.with_name(f"{path.stem}{suffix}")).replace("\\", "/")
    return str(path.with_name(f"{path.name}{suffix}")).replace("\\", "/")


def write_agent_output(connection: sqlite3.Connection, vault: VaultConfig, output_path: str | None, title: str, body: str) -> Path:
    if not output_path:
        raise RuntimeError("agent is missing output_path")
    target = vault.resolved_path() / output_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
    index_generated_note(connection, vault, target)
    return target


def write_managed_output(connection: sqlite3.Connection, vault: VaultConfig, output_path: str | None, title: str, generated_body: str) -> Path:
    if not output_path:
        raise RuntimeError("agent is missing output_path")
    target = vault.resolved_path() / output_path
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_text = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(merge_managed_note(existing_text, title, generated_body), encoding="utf-8")
    index_generated_note(connection, vault, target)
    return target


def merge_managed_note(existing_text: str, title: str, generated_body: str) -> str:
    start_index = existing_text.find(MANAGED_START)
    end_index = existing_text.find(MANAGED_END)
    if start_index != -1 and end_index != -1 and end_index > start_index:
        prefix = existing_text[:start_index]
        suffix = existing_text[end_index + len(MANAGED_END) :]
        return build_managed_document(title, strip_title_heading(prefix, title), generated_body, suffix)
    if is_probably_legacy_generated_note(existing_text, title):
        return build_managed_document(title, "", generated_body, "")
    return build_managed_document(title, strip_title_heading(existing_text, title), generated_body, "")


def build_managed_document(title: str, prefix_body: str, generated_body: str, suffix_body: str) -> str:
    sections = [f"# {title}"]
    if prefix_body.strip():
        sections.append(prefix_body.strip())
    sections.append("\n".join([MANAGED_START, generated_body.strip(), MANAGED_END]))
    if suffix_body.strip():
        sections.append(suffix_body.strip())
    return "\n\n".join(sections).strip() + "\n"


def strip_title_heading(text: str, title: str) -> str:
    stripped = text.strip()
    heading = f"# {title}"
    if not stripped.startswith(heading):
        return stripped
    remainder = stripped[len(heading) :].lstrip()
    return remainder


def is_probably_legacy_generated_note(text: str, title: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith(f"# {title}"):
        return False
    if MANAGED_START in stripped or MANAGED_END in stripped:
        return False
    return "Generated at:" in stripped or "Selection:" in stripped


def resolve_vault(config: AppConfig, name: str) -> VaultConfig:
    for vault in config.vaults:
        if vault.name == name:
            return vault
    raise RuntimeError(f"configured agent vault does not exist: {name}")

