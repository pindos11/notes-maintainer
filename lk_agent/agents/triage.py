from __future__ import annotations

from typing import Iterable

DETERMINISTIC_DECISIONS = {
    "answer_needed",
    "attachment_review",
    "command_capture",
    "review_needed",
    "task_follow_up",
}

LLM_ASSIST_CANDIDATES = {
    "categorize_project",
    "rewrite_capture",
    "merge_duplicate_idea",
    "infer_intent",
}


def classify_inbox_item(item: dict[str, object], has_open_tasks: bool = False) -> list[str]:
    decisions: list[str] = []
    preview = str(item.get("preview") or "").strip()
    file_ref = item.get("file_ref")

    if file_ref:
        decisions.append("attachment_review")
    if preview.startswith("/"):
        decisions.append("command_capture")
    if "?" in preview:
        decisions.append("answer_needed")
    if has_open_tasks:
        decisions.append("task_follow_up")
    if not decisions or preview:
        if "review_needed" not in decisions:
            decisions.append("review_needed")
    return decisions


def summarize_decisions(decisions: Iterable[str]) -> str:
    ordered = list(dict.fromkeys(decisions))
    return ", ".join(ordered) if ordered else "review_needed"


def llm_assist_candidates_for_item(item: dict[str, object]) -> list[str]:
    preview = str(item.get("preview") or "").strip()
    candidates: list[str] = []
    if preview and not preview.startswith("/"):
        candidates.append("categorize_project")
        candidates.append("rewrite_capture")
    if len(preview.split()) > 8:
        candidates.append("infer_intent")
    return list(dict.fromkeys(candidates))
