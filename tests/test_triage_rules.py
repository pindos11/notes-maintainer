from __future__ import annotations

import unittest

from lk_agent.agents.triage import (
    DETERMINISTIC_DECISIONS,
    LLM_ASSIST_CANDIDATES,
    classify_inbox_item,
    llm_assist_candidates_for_item,
    summarize_decisions,
)


class TriageRulesTests(unittest.TestCase):
    def test_classify_inbox_item_uses_deterministic_rules(self) -> None:
        item = {
            "preview": "Is everything okay?",
            "file_ref": None,
        }
        decisions = classify_inbox_item(item, has_open_tasks=True)
        self.assertIn("answer_needed", decisions)
        self.assertIn("task_follow_up", decisions)
        self.assertIn("review_needed", decisions)
        self.assertTrue(set(decisions).issubset(DETERMINISTIC_DECISIONS))

    def test_classify_inbox_item_marks_command_and_attachment(self) -> None:
        item = {
            "preview": "/capture hello",
            "file_ref": "photo.jpg",
        }
        decisions = classify_inbox_item(item)
        self.assertIn("command_capture", decisions)
        self.assertIn("attachment_review", decisions)

    def test_llm_assist_candidates_are_advisory(self) -> None:
        item = {"preview": "Need to decide which project this belongs to and rewrite it later."}
        candidates = llm_assist_candidates_for_item(item)
        self.assertIn("categorize_project", candidates)
        self.assertIn("rewrite_capture", candidates)
        self.assertTrue(set(candidates).issubset(LLM_ASSIST_CANDIDATES))

    def test_summarize_decisions_is_stable(self) -> None:
        summary = summarize_decisions(["answer_needed", "review_needed", "answer_needed"])
        self.assertEqual(summary, "answer_needed, review_needed")


if __name__ == "__main__":
    unittest.main()
