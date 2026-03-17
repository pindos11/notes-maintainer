from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from lk_agent.agents.memory import write_shared_memory
from lk_agent.core.db import initialize
from lk_agent.integrations.runtime import run_cycle
from lk_agent.integrations.telegram_bot import build_brief_text, ingest_message

from tests.test_helpers import TempApp


class RuntimeAndIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = TempApp()
        self.app.config.db_path = str((self.app.root / "data" / "app.db").resolve())
        self.app.write_note(
            "First Note.md",
            "# First Note\n\n- [ ] Finish parser\n\nBody text.\n",
        )
        self.app.rebuild()

    def tearDown(self) -> None:
        self.app.close()

    def make_message(self, text: str, message_id: int = 1) -> object:
        user = SimpleNamespace(username="tester", full_name="Test User")
        return SimpleNamespace(
            text=text,
            caption=None,
            date=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
            chat_id=123,
            message_id=message_id,
            from_user=user,
            document=None,
            voice=None,
            photo=None,
            to_dict=lambda: {"text": text, "message_id": message_id, "chat_id": 123},
        )

    def test_ingest_message_captures_plain_text_but_not_read_only_command(self) -> None:
        connection = self.app.db_connection()
        try:
            vault = self.app.config.vaults[0]
            plain = self.make_message("hello world", message_id=1)
            search = self.make_message("/search parser", message_id=2)
            with patch("lk_agent.integrations.telegram_bot.send_text", new=AsyncMock()):
                plain_result = ingest_message(connection, vault, self.app.config, plain)
                search_result = ingest_message(connection, vault, self.app.config, search)
            mapped = connection.execute(
                "SELECT telegram_message_id, mapped_note_path FROM telegram_messages ORDER BY telegram_message_id"
            ).fetchall()
        finally:
            connection.close()
        self.assertIsNotNone(plain_result["note_path"])
        self.assertTrue(Path(plain_result["note_path"]).exists())
        self.assertEqual(search_result["command"], "search")
        self.assertIsNone(search_result["note_path"])
        self.assertEqual(mapped[0]["mapped_note_path"], plain_result["note_path"])
        self.assertIsNone(mapped[1]["mapped_note_path"])

    def test_build_brief_text_prefers_shared_memory_then_falls_back_to_digest(self) -> None:
        connection = self.app.db_connection()
        try:
            vault = self.app.config.vaults[0]
            fallback = build_brief_text(connection, vault, "Daily Brief", ["inbox-state"])
            write_shared_memory(
                connection,
                self.app.config,
                vault.name,
                "inbox-state",
                "main Inbox State",
                "Generated at: now\nRecent inbox notes: 1",
                1,
                root=self.app.root,
            )
            preferred = build_brief_text(connection, vault, "Daily Brief", ["inbox-state"])
        finally:
            connection.close()
        self.assertIn("# Daily Brief", fallback)
        self.assertIn("Vault: main", fallback)
        self.assertIn("## Inbox", preferred)
        self.assertIn("Recent inbox notes: 1", preferred)

    def test_run_cycle_reports_telegram_and_job_results(self) -> None:
        reports: list[str] = []
        config = self.app.config
        with patch("lk_agent.integrations.runtime.poll_once", return_value=[{"chat_id": 123, "message_id": 5, "note_path": "note.md"}]), patch(
            "lk_agent.integrations.runtime.run_due_jobs",
            return_value=[("main-inbox-job", Path("report.md"))],
        ):
            result = run_cycle(config, telegram_limit=10, skip_telegram=False, skip_jobs=False, report=reports.append)
        self.assertEqual(result.telegram_count, 1)
        self.assertEqual(result.job_count, 1)
        self.assertTrue(any("telegram chat=123 msg=5 -> note.md" in item for item in reports))
        self.assertTrue(any("job 'main-inbox-job' wrote report.md" in item for item in reports))


if __name__ == "__main__":
    unittest.main()
