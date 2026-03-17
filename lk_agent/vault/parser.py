from __future__ import annotations

import json
import re
from pathlib import Path

from lk_agent.core.models import ParsedNote, ParsedTask


TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_\-/]+)")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def parse_markdown(path: Path) -> ParsedNote:
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(raw)
    lines = body.splitlines()
    return ParsedNote(
        title=extract_title(path, lines),
        raw_content=raw,
        body=body,
        frontmatter=frontmatter,
        tags=sorted(set(TAG_RE.findall(body))),
        links=sorted(set(WIKILINK_RE.findall(body) + MARKDOWN_LINK_RE.findall(body))),
        tasks=extract_tasks(lines),
        word_count=len(re.findall(r"\b\w+\b", body)),
    )


def split_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    lines = raw.splitlines()
    try:
        end_index = lines[1:].index("---") + 1
    except ValueError:
        return {}, raw
    return parse_frontmatter(lines[1:end_index]), "\n".join(lines[end_index + 1 :])


def parse_frontmatter(lines: list[str]) -> dict[str, object]:
    data: dict[str, object] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        data[key] = parse_frontmatter_value(raw_value.strip())
    return data


def parse_frontmatter_value(value: str) -> object:
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",")]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip("'\"")


def extract_title(path: Path, lines: list[str]) -> str:
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return path.stem


def extract_tasks(lines: list[str]) -> list[ParsedTask]:
    tasks: list[ParsedTask] = []
    for index, line in enumerate(lines, start=1):
        match = TASK_RE.match(line)
        if match is None:
            continue
        checked, text = match.groups()
        tasks.append(
            ParsedTask(
                text=text.strip(),
                status="done" if checked.lower() == "x" else "open",
                line_no=index,
            )
        )
    return tasks

