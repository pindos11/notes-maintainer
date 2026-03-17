from __future__ import annotations

import os
import time
import unittest

from lk_agent.agents.manager import bootstrap_default_agents, list_agent_status, run_agent
from lk_agent.integrations.scheduler import add_agent_job, run_job

from tests.test_helpers import TempApp


class AgentAndSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = TempApp()
        first_note = self.app.write_note(
            "First Note.md",
            "# First Note\n\n- [ ] Finish parser\n\nBody text.\n",
        )
        old_timestamp = time.time() - (40 * 24 * 60 * 60)
        os.utime(first_note, (old_timestamp, old_timestamp))
        self.app.write_note(
            "Inbox/2026-03-16/inbox.md",
            "---\nsource: telegram\nsender: \"tester\"\nreceived_at: 2026-03-16T10:00:00+00:00\n---\n\n# Telegram Inbox Capture\n\nIs everything okay?\n",
        )
        self.app.write_note(
            "Project/Link Note.md",
            "# Link Note\n\nSee [Missing](Missing.md) and [[Ghost Note]].\n",
        )
        self.app.rebuild()

    def tearDown(self) -> None:
        self.app.close()

    def test_run_agent_creates_report_memory_sidecar_notes_and_canonical_notes(self) -> None:
        connection = self.app.db_connection()
        try:
            names = bootstrap_default_agents(connection, "main")
            self.assertIn("main-inbox", names)
            result = run_agent(connection, self.app.config, "main-inbox", root=self.app.root)
            triage_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/main-inbox-agent-triage.md",),
            ).fetchone()
            followups_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/main-inbox-agent-followups.md",),
            ).fetchone()
            tasks_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/main-inbox-agent-tasks.md",),
            ).fetchone()
            questions_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Questions.md",),
            ).fetchone()
            canonical_followups_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Followups.md",),
            ).fetchone()
            canonical_tasks_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Tasks.md",),
            ).fetchone()
        finally:
            connection.close()
        self.assertTrue(result.exists())
        report_text = result.read_text(encoding="utf-8")
        self.assertIn("Inbox Agent Report", report_text)
        self.assertIn("review_needed", report_text)
        self.assertTrue((self.app.root / "data" / "memory" / "shared" / "main" / "inbox-state.md").exists())
        self.assertTrue((self.app.root / "data" / "memory" / "agents" / "main-inbox" / "latest.md").exists())

        triage_path = self.app.vault_path / "Reports" / "main-inbox-agent-triage.md"
        self.assertTrue(triage_path.exists())
        triage_text = triage_path.read_text(encoding="utf-8")
        self.assertIn("Inbox Triage Summary", triage_text)
        self.assertIn("Deterministic Decisions", triage_text)
        self.assertIn("later LLM assist", triage_text)

        followups_path = self.app.vault_path / "Reports" / "main-inbox-agent-followups.md"
        self.assertTrue(followups_path.exists())
        self.assertIn("Inbox Follow-Up Queue", followups_path.read_text(encoding="utf-8"))

        tasks_path = self.app.vault_path / "Reports" / "main-inbox-agent-tasks.md"
        self.assertTrue(tasks_path.exists())
        self.assertIn("Inbox Task Rollup", tasks_path.read_text(encoding="utf-8"))

        questions_path = self.app.vault_path / "Reports" / "Questions.md"
        self.assertTrue(questions_path.exists())
        questions_text = questions_path.read_text(encoding="utf-8")
        self.assertIn("# Questions", questions_text)
        self.assertIn("Selection:", questions_text)
        self.assertIn("Is everything okay?", questions_text)
        self.assertIn("Source: Inbox/2026-03-16/inbox.md", questions_text)
        self.assertIn("<!-- lk_agent:generated start -->", questions_text)
        self.assertIn("<!-- lk_agent:generated end -->", questions_text)

        canonical_followups_path = self.app.vault_path / "Reports" / "Followups.md"
        self.assertTrue(canonical_followups_path.exists())
        followups_text = canonical_followups_path.read_text(encoding="utf-8")
        self.assertIn("# Followups", followups_text)
        self.assertIn("Selection:", followups_text)
        self.assertIn("Source: Inbox/2026-03-16/inbox.md", followups_text)

        canonical_tasks_path = self.app.vault_path / "Reports" / "Tasks.md"
        self.assertTrue(canonical_tasks_path.exists())
        canonical_tasks_text = canonical_tasks_path.read_text(encoding="utf-8")
        self.assertIn("# Tasks", canonical_tasks_text)
        self.assertIn("Selection:", canonical_tasks_text)
        self.assertIn("Finish parser", canonical_tasks_text)
        self.assertIn("Source: First Note.md", canonical_tasks_text)

        self.assertIsNotNone(triage_row)
        self.assertIsNotNone(followups_row)
        self.assertIsNotNone(tasks_row)
        self.assertIsNotNone(questions_row)
        self.assertIsNotNone(canonical_followups_row)
        self.assertIsNotNone(canonical_tasks_row)

    def test_answered_frontmatter_suppresses_question_list_entries(self) -> None:
        inbox_path = self.app.vault_path / "Inbox" / "2026-03-16" / "inbox.md"
        inbox_path.write_text(
            "---\nsource: telegram\nsender: \"tester\"\nreceived_at: 2026-03-16T10:00:00+00:00\nanswered: true\n---\n\n# Telegram Inbox Capture\n\nIs everything okay?\n",
            encoding="utf-8",
        )
        self.app.rebuild()

        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-inbox", root=self.app.root)
        finally:
            connection.close()

        questions_path = self.app.vault_path / "Reports" / "Questions.md"
        questions_text = questions_path.read_text(encoding="utf-8")
        self.assertNotIn("Is everything okay?", questions_text)
        self.assertIn("No open question-like inbox captures detected.", questions_text)

    def test_managed_sections_preserve_user_text_in_canonical_notes(self) -> None:
        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-inbox", root=self.app.root)
        finally:
            connection.close()

        tasks_path = self.app.vault_path / "Reports" / "Tasks.md"
        original = tasks_path.read_text(encoding="utf-8")
        self.assertIn("<!-- lk_agent:generated start -->", original)
        updated = original.replace(
            "# Tasks\n\n",
            "# Tasks\n\n## My Notes\n\n- Keep this note\n\n",
            1,
        )
        tasks_path.write_text(updated, encoding="utf-8")

        connection = self.app.db_connection()
        try:
            run_agent(connection, self.app.config, "main-inbox", root=self.app.root)
        finally:
            connection.close()

        refreshed = tasks_path.read_text(encoding="utf-8")
        self.assertIn("## My Notes", refreshed)
        self.assertIn("- Keep this note", refreshed)
        self.assertIn("<!-- lk_agent:generated start -->", refreshed)
        self.assertIn("<!-- lk_agent:generated end -->", refreshed)
        self.assertIn("Finish parser", refreshed)

    def test_run_inbox_agent_twice_does_not_amplify_generated_task_notes(self) -> None:
        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-inbox", root=self.app.root)
            run_agent(connection, self.app.config, "main-inbox", root=self.app.root)
        finally:
            connection.close()

        canonical_tasks_path = self.app.vault_path / "Reports" / "Tasks.md"
        self.assertTrue(canonical_tasks_path.exists())
        task_lines = [
            line.strip()
            for line in canonical_tasks_path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("- [ ]")
        ]
        self.assertEqual(task_lines, ["- [ ] Finish parser (First Note)"])

    def test_run_maintenance_agent_creates_canonical_notes_without_self_feeding(self) -> None:
        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            result = run_agent(connection, self.app.config, "main-maintenance", root=self.app.root)
            run_agent(connection, self.app.config, "main-maintenance", root=self.app.root)
            stale_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Stale.md",),
            ).fetchone()
            untagged_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Untagged.md",),
            ).fetchone()
            broken_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/BrokenLinks.md",),
            ).fetchone()
            orphan_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Orphans.md",),
            ).fetchone()
            duplicates_row = connection.execute(
                "SELECT absolute_path FROM notes WHERE relative_path = ?",
                ("Reports/Duplicates.md",),
            ).fetchone()
        finally:
            connection.close()

        self.assertTrue(result.exists())
        report_text = result.read_text(encoding="utf-8")
        self.assertIn("Maintenance Agent Report", report_text)
        self.assertIn("Stale notes selected", report_text)
        self.assertIn("Broken links selected", report_text)
        self.assertIn("Orphan notes selected", report_text)
        self.assertIn("Duplicate clusters selected", report_text)

        stale_path = self.app.vault_path / "Reports" / "Stale.md"
        self.assertTrue(stale_path.exists())
        stale_text = stale_path.read_text(encoding="utf-8")
        self.assertIn("# Stale", stale_text)
        self.assertIn("Selection:", stale_text)
        self.assertIn("First Note", stale_text)
        self.assertNotIn("- Stale (", stale_text)
        self.assertNotIn("- Untagged (", stale_text)
        self.assertNotIn("Telegram Inbox Capture", stale_text)
        self.assertIn("<!-- lk_agent:generated start -->", stale_text)

        untagged_path = self.app.vault_path / "Reports" / "Untagged.md"
        self.assertTrue(untagged_path.exists())
        untagged_text = untagged_path.read_text(encoding="utf-8")
        self.assertIn("# Untagged", untagged_text)
        self.assertIn("Selection:", untagged_text)
        self.assertIn("### Root", untagged_text)
        self.assertIn("### Project", untagged_text)
        self.assertIn("First Note", untagged_text)
        self.assertIn("Link Note", untagged_text)
        self.assertNotIn("Telegram Inbox Capture", untagged_text)
        self.assertNotIn("- Stale (", untagged_text)
        self.assertNotIn("- Untagged (", untagged_text)
        self.assertIn("<!-- lk_agent:generated start -->", untagged_text)

        broken_path = self.app.vault_path / "Reports" / "BrokenLinks.md"
        self.assertTrue(broken_path.exists())
        broken_text = broken_path.read_text(encoding="utf-8")
        self.assertIn("# BrokenLinks", broken_text)
        self.assertIn("Selection:", broken_text)
        self.assertIn("Link Note", broken_text)
        self.assertIn("Missing.md", broken_text)
        self.assertIn("Ghost Note", broken_text)
        self.assertNotIn("BrokenLinks ->", broken_text)
        self.assertIn("<!-- lk_agent:generated start -->", broken_text)

        orphan_path = self.app.vault_path / "Reports" / "Orphans.md"
        self.assertTrue(orphan_path.exists())
        orphan_text = orphan_path.read_text(encoding="utf-8")
        self.assertIn("# Orphans", orphan_text)
        self.assertIn("Selection:", orphan_text)
        self.assertIn("First Note", orphan_text)
        self.assertIn("Link Note", orphan_text)
        self.assertNotIn("Orphans (", orphan_text)
        self.assertIn("<!-- lk_agent:generated start -->", orphan_text)

        duplicates_path = self.app.vault_path / "Reports" / "Duplicates.md"
        self.assertTrue(duplicates_path.exists())
        duplicates_text = duplicates_path.read_text(encoding="utf-8")
        self.assertIn("# Duplicates", duplicates_text)
        self.assertIn("Selection:", duplicates_text)
        self.assertIn("No duplicate-note clusters detected.", duplicates_text)
        self.assertIn("<!-- lk_agent:generated start -->", duplicates_text)

        self.assertIsNotNone(stale_row)
        self.assertIsNotNone(untagged_row)
        self.assertIsNotNone(broken_row)
        self.assertIsNotNone(orphan_row)
        self.assertIsNotNone(duplicates_row)

    def test_stale_ranking_prefers_actionable_old_notes(self) -> None:
        second_note = self.app.write_note(
            "Archive/Old Reference.md",
            "# Old Reference\n\nJust an old reference note.\n",
        )
        old_timestamp = time.time() - (40 * 24 * 60 * 60)
        os.utime(second_note, (old_timestamp, old_timestamp))
        self.app.rebuild()

        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-maintenance", root=self.app.root)
        finally:
            connection.close()

        stale_text = (self.app.vault_path / "Reports" / "Stale.md").read_text(encoding="utf-8")
        stale_lines = [line for line in stale_text.splitlines() if line.startswith("- ")]
        self.assertGreaterEqual(len(stale_lines), 2)
        self.assertIn("First Note", stale_lines[0])
        self.assertIn("Old Reference", stale_lines[1])

    def test_wiki_links_resolve_by_note_title(self) -> None:
        self.app.write_note(
            "second.md",
            "# Second Note\n\nBody text.\n",
        )
        self.app.write_note(
            "Wiki Source.md",
            "# Wiki Source\n\nSee [[Second Note]].\n",
        )
        self.app.rebuild()

        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-maintenance", root=self.app.root)
        finally:
            connection.close()

        broken_text = (self.app.vault_path / "Reports" / "BrokenLinks.md").read_text(encoding="utf-8")
        orphan_text = (self.app.vault_path / "Reports" / "Orphans.md").read_text(encoding="utf-8")
        self.assertNotIn("Wiki Source -> Second Note", broken_text)
        self.assertNotIn("Second Note (", orphan_text)

    def test_duplicate_note_suspicion_groups_similar_titles(self) -> None:
        duplicate_note = self.app.write_note(
            "Archive/First-Note.md",
            "# First Note\n\nDifferent content, same title.\n",
        )
        old_timestamp = time.time() - (35 * 24 * 60 * 60)
        os.utime(duplicate_note, (old_timestamp, old_timestamp))
        self.app.rebuild()

        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-maintenance", root=self.app.root)
        finally:
            connection.close()

        duplicates_text = (self.app.vault_path / "Reports" / "Duplicates.md").read_text(encoding="utf-8")
        self.assertIn("### First Note (2 notes)", duplicates_text)
        self.assertIn("First Note (", duplicates_text)
        self.assertIn("First-Note.md", duplicates_text)

    def test_reviewed_and_ignored_markers_suppress_maintenance_entries(self) -> None:
        first_note = self.app.vault_path / "First Note.md"
        first_note.write_text(
            "---\nreviewed_at: 2099-01-01T00:00:00+00:00\n---\n\n# First Note\n\n- [ ] Finish parser\n\nBody text.\n",
            encoding="utf-8",
        )
        link_note = self.app.vault_path / "Project" / "Link Note.md"
        link_note.write_text(
            "---\nignore_maintenance: true\n---\n\n# Link Note\n\nSee [Missing](Missing.md) and [[Ghost Note]].\n",
            encoding="utf-8",
        )
        self.app.rebuild()

        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            run_agent(connection, self.app.config, "main-maintenance", root=self.app.root)
        finally:
            connection.close()

        stale_text = (self.app.vault_path / "Reports" / "Stale.md").read_text(encoding="utf-8")
        untagged_text = (self.app.vault_path / "Reports" / "Untagged.md").read_text(encoding="utf-8")
        broken_text = (self.app.vault_path / "Reports" / "BrokenLinks.md").read_text(encoding="utf-8")
        orphan_text = (self.app.vault_path / "Reports" / "Orphans.md").read_text(encoding="utf-8")
        self.assertNotIn("First Note", stale_text)
        self.assertIn("No stale notes detected.", stale_text)
        self.assertNotIn("Link Note", untagged_text)
        self.assertNotIn("Link Note", broken_text)
        self.assertIn("No broken internal links detected.", broken_text)
        self.assertNotIn("Link Note", orphan_text)
        self.assertIn("First Note", orphan_text)

    def test_agent_job_runs_and_updates_status(self) -> None:
        connection = self.app.db_connection()
        try:
            bootstrap_default_agents(connection, "main")
            add_agent_job(connection, "main-inbox-job", "manual", "main-inbox")
            result = run_job(connection, self.app.config, "main-inbox-job", root=self.app.root)
            row = connection.execute(
                "SELECT last_status, last_error FROM jobs WHERE name = ?",
                ("main-inbox-job",),
            ).fetchone()
            statuses = list_agent_status(connection)
        finally:
            connection.close()
        self.assertTrue(result.exists())
        self.assertEqual(row["last_status"], "ok")
        self.assertIsNone(row["last_error"])
        self.assertTrue(any(item["name"] == "main-inbox" for item in statuses))


if __name__ == "__main__":
    unittest.main()
