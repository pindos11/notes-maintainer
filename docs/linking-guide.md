# Linking Guide

This guide explains how to link notes so the app can understand the relationships between them.

## What Link Formats Work

The app currently understands these internal link styles:

```md
[[Project Plan]]
```

```md
[Project Plan](Project Plan.md)
```

```md
[Plan](Projects/Project Plan.md)
```

These are all indexed as note links when you run `vault rebuild`.

## Recommended Simple Rule

If you want the maintenance features to work well, use one of these two habits:

1. use `[[Note Name]]` when you want a fast wiki-style note reference
2. use `[label](Relative Path.md)` when you want an exact path-based link

That is enough for the current app.

## What The App Uses Links For

Right now links matter for:

- broken-link review in `Reports/BrokenLinks.md`
- orphan-note review in `Reports/Orphans.md`
- general note metadata and future maintenance heuristics

### Broken Links

A link is treated as broken when it points to a local note the app cannot resolve.

Examples that will be flagged if the target note does not exist:

```md
[[Missing Note]]
```

```md
[Missing](Missing Note.md)
```

```md
[Missing](Projects/Missing Note.md)
```

### Orphans

A note is treated as an orphan when no other non-generated, non-ignored note links to it.

That means if you want a note to stop appearing in `Orphans.md`, the normal fix is to add at least one incoming link from another real note.

## Good Practical Examples

### Example 1: Project Hub

`Projects/Alpha.md`

```md
# Alpha

- [[Alpha Tasks]]
- [[Alpha Decisions]]
- [[Meeting Notes]]
```

This creates clear incoming links for those notes and helps avoid orphaned project pages.

### Example 2: Exact File Link

```md
See [Alpha Tasks](Projects/Alpha Tasks.md)
```

Use this when you want to be precise about the target file path.

### Example 3: Daily Notes Linking Back To Work

```md
Worked on [[Parser Cleanup]] today.
Need to revisit [API Draft](Projects/API Draft.md).
```

This helps the app see that those notes are still active parts of the vault.

## Recommended Linking Habits

- create a small number of hub notes for projects or areas
- link meeting notes, tasks, and decisions back to those hubs
- when you create a new important note, add at least one incoming or outgoing link soon after
- prefer exact Markdown path links when file names may be ambiguous
- prefer wiki links when you want fast note-to-note references and stable naming

## What Does Not Count As A Local Note Link

These are not treated as local note relationships:

```md
https://example.com
mailto:person@example.com
tel:+123456789
#local-heading
```

They are ignored by broken-link and orphan-note maintenance.

## How To Fix `BrokenLinks.md`

If a note appears in `BrokenLinks.md`, do one of these:

- create the missing note
- fix the target path or note name
- remove the outdated link
- if the whole note should be ignored by maintenance, add:

```yaml
---
ignore_maintenance: true
---
```

## How To Fix `Orphans.md`

If a note appears in `Orphans.md`, do one of these:

- add a link to it from a relevant note
- move it under a better project structure and link it from the project hub
- decide it is junk and delete or archive it
- if it should stay isolated on purpose, add:

```yaml
---
ignore_maintenance: true
---
```

## After Editing Links

Run:

```powershell
python -m lk_agent.cli.main vault rebuild
python -m lk_agent.cli.main agents run main-maintenance
```

That refreshes the link index and regenerates the maintenance notes.

## Current Limits

The app does not currently do all of these things:

- it does not rewrite links automatically
- it does not maintain backlinks inside note files
- it does not resolve every possible Markdown edge case
- it does not have Obsidian-level link semantics

The current goal is simpler: enough local link understanding to support useful maintenance.
