from __future__ import annotations

import unittest

from lk_agent.agents.memory import write_shared_memory
from lk_agent.integrations.telegram_bot import (
    build_brokenlinks_text,
    build_followups_text,
    build_help_text,
    build_recent_text,
    build_stale_text,
    build_taskrollup_text,
    build_tasks_text,
    build_triage_text,
    build_untagged_text,
    extract_command_name,
    render_telegram_html,
    should_capture_to_note,
)
from lk_agent.vault.index import normalize_search_query, search_notes

from tests.test_helpers import TempApp


class SearchAndTelegramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = TempApp()
        self.app.write_note(
            "First Note.md",
            "# First Note\n\n- [ ] Finish parser\n\nThis project mentions привет and parser.\n",
        )
        self.app.write_note(
            "Inbox/2026-03-16/inbox.md",
            "---\nsource: telegram\nsender: \"tester\"\nreceived_at: 2026-03-16T10:00:00+00:00\n---\n\n# Telegram Inbox Capture\n\nIs everything okay?\n",
        )
        self.app.rebuild()

    def tearDown(self) -> None:
        self.app.close()

    def test_normalize_search_query_handles_punctuation_and_unicode(self) -> None:
        self.assertEqual(normalize_search_query("привет!"), '"привет"')
        self.assertEqual(normalize_search_query("parser!!! test"), '"parser" "test"')
        self.assertIsNone(normalize_search_query("!!!"))

    def test_search_notes_does_not_raise_on_punctuation_query(self) -> None:
        connection = self.app.db_connection()
        try:
            rows = search_notes(connection, "привет!", 5)
        finally:
            connection.close()
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "First Note")

    def test_telegram_command_routing_marks_read_only_commands_correctly(self) -> None:
        self.assertEqual(extract_command_name("/help"), "help")
        self.assertEqual(extract_command_name("/tasks"), "tasks")
        self.assertEqual(extract_command_name("/recent"), "recent")
        self.assertEqual(extract_command_name("/triage"), "triage")
        self.assertEqual(extract_command_name("/followups"), "followups")
        self.assertEqual(extract_command_name("/taskrollup"), "taskrollup")
        self.assertEqual(extract_command_name("/dailybrief"), "dailybrief")
        self.assertEqual(extract_command_name("/stale"), "stale")
        self.assertEqual(extract_command_name("/untagged"), "untagged")
        self.assertEqual(extract_command_name("/brokenlinks"), "brokenlinks")
        self.assertEqual(extract_command_name("/task-rollup"), "taskrollup")
        self.assertEqual(extract_command_name("/daily-brief"), "dailybrief")
        self.assertFalse(should_capture_to_note("/help"))
        self.assertFalse(should_capture_to_note("/tasks"))
        self.assertFalse(should_capture_to_note("/recent"))
        self.assertFalse(should_capture_to_note("/triage"))
        self.assertFalse(should_capture_to_note("/followups"))
        self.assertFalse(should_capture_to_note("/taskrollup"))
        self.assertFalse(should_capture_to_note("/stale"))
        self.assertFalse(should_capture_to_note("/untagged"))
        self.assertFalse(should_capture_to_note("/brokenlinks"))
        self.assertFalse(should_capture_to_note("/task-rollup"))
        self.assertFalse(should_capture_to_note("/daily-brief"))
        self.assertTrue(should_capture_to_note("/capture hello"))
        self.assertTrue(should_capture_to_note("plain text"))

    def test_telegram_read_only_builders_return_local_state(self) -> None:
        connection = self.app.db_connection()
        try:
            vault = self.app.config.vaults[0]
            tasks_text = build_tasks_text(connection, vault)
            recent_text = build_recent_text(connection, vault)
        finally:
            connection.close()
        self.assertIn("Finish parser", tasks_text)
        self.assertIn("Recent Notes", recent_text)
        self.assertIn("Telegram Inbox Capture", recent_text)

    def test_build_help_text_groups_commands_and_mentions_maintenance(self) -> None:
        text = build_help_text()
        self.assertIn("# Telegram Commands", text)
        self.assertIn("## Capture", text)
        self.assertIn("## Maintenance", text)
        self.assertIn("/dailybrief", text)
        self.assertIn("/brokenlinks", text)


    def test_render_telegram_html_formats_headings_lists_code_paths_and_indents(self) -> None:
        rendered = render_telegram_html(
            "# Daily Brief\n\n## Inbox\n\n- item one\n  D:/vault/note.md\nPath: `C:/vault/other.md`\n/plain"
        )
        self.assertIn("<b>Daily Brief</b>", rendered)
        self.assertIn("<b>Inbox</b>", rendered)
        self.assertIn("• item one", rendered)
        self.assertIn("&nbsp;&nbsp;<code>D:/vault/note.md</code>", rendered)
        self.assertIn("<code>C:/vault/other.md</code>", rendered)
        self.assertIn("/plain", rendered)

    def test_triage_text_uses_fallback_memory_then_triage_note(self) -> None:
        connection = self.app.db_connection()
        try:
            vault = self.app.config.vaults[0]
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
            fallback = build_triage_text(connection, vault)
            triage = self.app.write_note(
                "Reports/main-inbox-agent-triage.md",
                "# Inbox Triage Summary\n\n## Questions To Answer\n\n- Telegram Inbox Capture: Is everything okay?\n",
            )
            from lk_agent.vault.parser import parse_markdown
            from lk_agent.vault.storage import sync_vault_record, upsert_note
            vault_id = sync_vault_record(connection, vault)
            upsert_note(connection, vault_id, triage, "Reports/main-inbox-agent-triage.md", parse_markdown(triage))
            preferred = build_triage_text(connection, vault)
        finally:
            connection.close()
        self.assertIn("# Inbox Triage", fallback)
        self.assertIn("Recent inbox notes: 1", fallback)
        self.assertIn("# Inbox Triage Summary", preferred)
        self.assertIn("Questions To Answer", preferred)

    def test_followups_taskrollup_stale_untagged_and_brokenlinks_prefer_generated_notes_then_fallback(self) -> None:
        connection = self.app.db_connection()
        try:
            vault = self.app.config.vaults[0]
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
            write_shared_memory(
                connection,
                self.app.config,
                vault.name,
                "maintenance-state",
                "main Maintenance State",
                "Generated at: now\nStale notes (>30d): 1\nUntagged notes: 1",
                1,
                root=self.app.root,
            )
            followups_fallback = build_followups_text(connection, vault)
            taskrollup_fallback = build_taskrollup_text(connection, vault)
            stale_fallback = build_stale_text(connection, vault)
            untagged_fallback = build_untagged_text(connection, vault)
            brokenlinks_fallback = build_brokenlinks_text(connection, vault)

            followups = self.app.write_note(
                "Reports/main-inbox-agent-followups.md",
                "# Inbox Follow-Up Queue\n\n- Reply to tester about parser state\n",
            )
            task_rollup = self.app.write_note(
                "Reports/main-inbox-agent-tasks.md",
                "# Inbox Task Rollup\n\n- [ ] Finish parser\n",
            )
            stale = self.app.write_note(
                "Reports/Stale.md",
                "# Stale\n\n## Stale Notes\n\n- First Note\n",
            )
            untagged = self.app.write_note(
                "Reports/Untagged.md",
                "# Untagged\n\n## Untagged Notes\n\n- First Note\n",
            )
            brokenlinks = self.app.write_note(
                "Reports/BrokenLinks.md",
                "# BrokenLinks\n\n## Broken Links\n\n- First Note -> Missing.md\n",
            )

            from lk_agent.vault.parser import parse_markdown
            from lk_agent.vault.storage import sync_vault_record, upsert_note
            vault_id = sync_vault_record(connection, vault)
            upsert_note(connection, vault_id, followups, "Reports/main-inbox-agent-followups.md", parse_markdown(followups))
            upsert_note(connection, vault_id, task_rollup, "Reports/main-inbox-agent-tasks.md", parse_markdown(task_rollup))
            upsert_note(connection, vault_id, stale, "Reports/Stale.md", parse_markdown(stale))
            upsert_note(connection, vault_id, untagged, "Reports/Untagged.md", parse_markdown(untagged))
            upsert_note(connection, vault_id, brokenlinks, "Reports/BrokenLinks.md", parse_markdown(brokenlinks))

            followups_preferred = build_followups_text(connection, vault)
            taskrollup_preferred = build_taskrollup_text(connection, vault)
            stale_preferred = build_stale_text(connection, vault)
            untagged_preferred = build_untagged_text(connection, vault)
            brokenlinks_preferred = build_brokenlinks_text(connection, vault)
        finally:
            connection.close()
        self.assertIn("# Inbox Follow-Ups", followups_fallback)
        self.assertIn("Recent inbox notes: 1", followups_fallback)
        self.assertIn("Finish parser", taskrollup_fallback)
        self.assertIn("# Stale", stale_fallback)
        self.assertIn("Stale notes (>30d): 1", stale_fallback)
        self.assertIn("# Untagged", untagged_fallback)
        self.assertIn("Untagged notes: 1", untagged_fallback)
        self.assertIn("# Broken Links", brokenlinks_fallback)
        self.assertIn("Stale notes (>30d): 1", brokenlinks_fallback)
        self.assertIn("# Inbox Follow-Up Queue", followups_preferred)
        self.assertIn("Reply to tester", followups_preferred)
        self.assertIn("# Inbox Task Rollup", taskrollup_preferred)
        self.assertIn("Finish parser", taskrollup_preferred)
        self.assertIn("# Stale", stale_preferred)
        self.assertIn("First Note", stale_preferred)
        self.assertIn("# Untagged", untagged_preferred)
        self.assertIn("First Note", untagged_preferred)
        self.assertIn("# BrokenLinks", brokenlinks_preferred)
        self.assertIn("Missing.md", brokenlinks_preferred)


if __name__ == "__main__":
    unittest.main()
