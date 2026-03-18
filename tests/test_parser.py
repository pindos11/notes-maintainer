from __future__ import annotations

import unittest

from lk_agent.vault.parser import parse_markdown
from tests.test_helpers import TempApp


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = TempApp()

    def tearDown(self) -> None:
        self.app.close()

    def test_parse_frontmatter_supports_yaml_style_tag_lists(self) -> None:
        path = self.app.write_note(
            "joplin.md",
            "---\ntitle: IT0001 fields\nupdated: 2025-09-23 20:45:54Z\ncreated: 2025-09-23 20:45:54Z\ntags:\n  - it0001\n  - finance\n---\n\nBody text.\n",
        )
        parsed = parse_markdown(path)
        self.assertEqual(parsed.title, "IT0001 fields")
        self.assertEqual(parsed.frontmatter["title"], "IT0001 fields")
        self.assertEqual(parsed.frontmatter["tags"], ["it0001", "finance"])
        self.assertEqual(parsed.tags, ["finance", "it0001"])

    def test_parse_frontmatter_keeps_existing_inline_forms(self) -> None:
        path = self.app.write_note(
            "inline.md",
            "---\ntitle: Inline Title\ntags: [alpha, beta]\nanswered: true\n---\n\n# Heading Wins\n\nBody text with #gamma.\n",
        )
        parsed = parse_markdown(path)
        self.assertEqual(parsed.title, "Heading Wins")
        self.assertEqual(parsed.frontmatter["tags"], ["alpha", "beta"])
        self.assertEqual(parsed.frontmatter["answered"], True)
        self.assertEqual(parsed.tags, ["alpha", "beta", "gamma"])


if __name__ == "__main__":
    unittest.main()
