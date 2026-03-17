from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lk_agent.actions.digest import write_digest
from lk_agent.agents.manager import execute_agent, row_to_agent
from lk_agent.core.models import AppConfig, VaultConfig


@dataclass(slots=True)
class JobRecord:
    name: str
    kind: str
    schedule: str
    vault_name: str | None
    output_path: str | None
    config: dict[str, object]
    enabled: bool
    last_run_at: str | None
    last_status: str | None
    last_error: str | None


def add_digest_job(connection: sqlite3.Connection, name: str, schedule: str, vault_name: str, output_path: str) -> None:
    connection.execute(
        """
        INSERT INTO jobs (name, kind, schedule, vault_name, output_path, config_json, enabled)
        VALUES (?, 'digest', ?, ?, ?, '{}', 1)
        ON CONFLICT(name) DO UPDATE SET
            schedule = excluded.schedule,
            vault_name = excluded.vault_name,
            output_path = excluded.output_path,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, schedule, vault_name, output_path),
    )
    connection.commit()


def add_agent_job(connection: sqlite3.Connection, name: str, schedule: str, agent_name: str) -> None:
    connection.execute(
        """
        INSERT INTO jobs (name, kind, schedule, config_json, enabled)
        VALUES (?, 'agent', ?, ?, 1)
        ON CONFLICT(name) DO UPDATE SET
            schedule = excluded.schedule,
            config_json = excluded.config_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (name, schedule, json.dumps({"agent_name": agent_name}, ensure_ascii=False)),
    )
    connection.commit()


def list_jobs(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT name, kind, schedule, vault_name, output_path, config_json, enabled, last_run_at, last_status, last_error
        FROM jobs
        ORDER BY name
        """
    ).fetchall()


def run_job(connection: sqlite3.Connection, config: AppConfig, name: str, root: Path | None = None) -> Path:
    row = connection.execute("SELECT * FROM jobs WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"unknown job: {name}")
    job = row_to_job(row)
    return execute_job(connection, config, job, root=root)


def run_due_jobs(connection: sqlite3.Connection, config: AppConfig, root: Path | None = None) -> list[tuple[str, Path]]:
    rows = connection.execute("SELECT * FROM jobs WHERE enabled = 1 ORDER BY name").fetchall()
    results: list[tuple[str, Path]] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        job = row_to_job(row)
        if is_due(job, now):
            results.append((job.name, execute_job(connection, config, job, root=root)))
    return results


def execute_job(connection: sqlite3.Connection, config: AppConfig, job: JobRecord, root: Path | None = None) -> Path:
    try:
        if job.kind == "digest":
            vault = resolve_vault(config, job.vault_name)
            if not job.output_path:
                raise RuntimeError(f"job '{job.name}' is missing output_path")
            result = write_digest(connection, vault, job.output_path)
        elif job.kind == "agent":
            agent_name = str(job.config.get("agent_name") or "")
            if not agent_name:
                raise RuntimeError(f"job '{job.name}' is missing agent_name")
            row = connection.execute("SELECT * FROM agents WHERE name = ? AND enabled = 1", (agent_name,)).fetchone()
            if row is None:
                raise RuntimeError(f"configured agent does not exist or is disabled: {agent_name}")
            result = execute_agent(connection, config, row_to_agent(row), root=root)
        else:
            raise RuntimeError(f"unsupported job kind: {job.kind}")
        mark_job(connection, job.name, "ok", None)
        connection.commit()
        return result
    except Exception as exc:
        mark_job(connection, job.name, "error", str(exc))
        connection.commit()
        raise


def mark_job(connection: sqlite3.Connection, name: str, status: str, error: str | None) -> None:
    connection.execute(
        """
        UPDATE jobs
        SET last_run_at = ?, last_status = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
        WHERE name = ?
        """,
        (datetime.now(timezone.utc).isoformat(), status, error, name),
    )


def row_to_job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        name=row["name"],
        kind=row["kind"],
        schedule=row["schedule"],
        vault_name=row["vault_name"],
        output_path=row["output_path"],
        config=json.loads(row["config_json"]),
        enabled=bool(row["enabled"]),
        last_run_at=row["last_run_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
    )


def is_due(job: JobRecord, now: datetime) -> bool:
    if job.schedule == "manual":
        return False
    if job.last_run_at is None:
        return True
    last_run = datetime.fromisoformat(job.last_run_at)
    if job.schedule == "hourly":
        return now - last_run >= timedelta(hours=1)
    if job.schedule == "daily":
        return now - last_run >= timedelta(days=1)
    if job.schedule.startswith("interval:"):
        seconds = int(job.schedule.split(":", 1)[1])
        return now - last_run >= timedelta(seconds=seconds)
    raise RuntimeError(f"unsupported schedule: {job.schedule}")


def resolve_vault(config: AppConfig, name: str | None) -> VaultConfig:
    if not name:
        raise RuntimeError("job is missing vault_name")
    for vault in config.vaults:
        if vault.name == name:
            return vault
    raise RuntimeError(f"configured job vault does not exist: {name}")
