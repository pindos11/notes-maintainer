from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from lk_agent.core.config import ensure_runtime_dirs, load_config
from lk_agent.core.db import initialize
from lk_agent.integrations.scheduler import run_due_jobs
from lk_agent.integrations.telegram_bot import TelegramConfigError, poll_once


@dataclass(slots=True)
class LoopResult:
    cycle_started_at: str
    telegram_count: int
    job_count: int


def run_loop(
    interval_seconds: int | None = None,
    telegram_limit: int = 20,
    once: bool = False,
    skip_telegram: bool = False,
    skip_jobs: bool = False,
    reporter: Callable[[str], None] | None = None,
) -> int:
    report = reporter or (lambda message: None)
    config = load_config()
    ensure_runtime_dirs(config)
    interval = interval_seconds or config.scheduler.poll_seconds
    if interval <= 0:
        raise RuntimeError("interval must be greater than zero")

    cycle = 0
    while True:
        cycle += 1
        result = run_cycle(config, telegram_limit, skip_telegram, skip_jobs, report)
        report(
            f"cycle={cycle} started={result.cycle_started_at} telegram={result.telegram_count} jobs={result.job_count}"
        )
        if once:
            return 0
        time.sleep(interval)


def run_cycle(
    config,
    telegram_limit: int,
    skip_telegram: bool,
    skip_jobs: bool,
    report: Callable[[str], None],
) -> LoopResult:
    connection = initialize(config.resolved_db_path(Path.cwd()))
    telegram_count = 0
    job_count = 0
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        if not skip_telegram:
            try:
                telegram_results = poll_once(connection, config, telegram_limit)
                telegram_count = len(telegram_results)
                for item in telegram_results:
                    report(f"telegram chat={item['chat_id']} msg={item['message_id']} -> {item['note_path']}")
            except TelegramConfigError as exc:
                report(f"telegram skipped: {exc}")
            except Exception as exc:
                report(f"telegram error: {exc}")
        if not skip_jobs:
            try:
                job_results = run_due_jobs(connection, config)
                job_count = len(job_results)
                for name, path in job_results:
                    report(f"job '{name}' wrote {path}")
            except Exception as exc:
                report(f"jobs error: {exc}")
        connection.commit()
    finally:
        connection.close()
    return LoopResult(cycle_started_at=started_at, telegram_count=telegram_count, job_count=job_count)
