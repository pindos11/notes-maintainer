# User Guide

This guide explains the project in plain terms and shows how someone would actually use it.

See also: [5 Minute Quickstart](quickstart-5min.md)

## In One Sentence

This app helps you keep a Markdown notes folder under control by combining:

- local note indexing
- quick Telegram, CLI, or file-based capture
- scheduled maintenance
- generated Markdown summaries, follow-ups, tasks, and cleanup notes

If your notes folder feels messy, this is meant to help without forcing you into a heavy system.

## The Main Idea

Your Markdown files are still the real data.

The app does not replace them. It builds useful support around them:

- SQLite for search and derived state
- Telegram for quick capture from your phone
- CLI capture and local file import when you are already at the machine
- agents for maintenance tasks
- generated notes for summaries, follow-up queues, task lists, and cleanup reviews

You can stop using the app and still keep all your Markdown files.

## Who This Is For

This project is a good fit if you are someone who:

- keeps notes in `.md` files
- wants local search that is fast and simple
- wants to send yourself quick notes from Telegram
- wants tasks and follow-ups pulled out of scattered notes
- wants lightweight automation without a giant platform

This project is probably not for you if you want:

- a polished GUI-first product
- live collaboration
- cloud sync managed by the app
- unrestricted AI agents doing everything automatically

## Real-Life Use Cases

### Use Case 1: Capture Thoughts From Your Phone

You are outside, away from the computer, and want to save something fast.

You send:

```text
Idea: create a short note about local-first project structure
```

or:

```text
Need to ask Oleg whether the server migration is done
```

The app can save that into your vault as an inbox note.

Later, the inbox agent can turn those raw captures into:

- a triage summary
- a follow-up queue
- a task rollup
- canonical notes like `Questions.md`, `Followups.md`, and `Tasks.md`

So instead of forgetting the message, you get an actual maintained note inside your vault.

### Use Case 2: Keep Work Notes Less Chaotic

Suppose you have a folder full of project notes, meeting notes, and scratch files.

After some time, it becomes hard to answer basic questions:

- what do I need to follow up on?
- what open tasks are buried in old notes?
- what recent notes were changed?
- what should I review today?
- which notes are stale?
- which notes still have no tags?

This app helps by generating local reports and letting you ask for some of them through Telegram.

Examples:

```text
/tasks
/recent
/triage
/followups
/taskrollup
```

And inside the vault it can maintain:

- `Reports/Questions.md`
- `Reports/Followups.md`
- `Reports/Tasks.md`
- `Reports/Stale.md`
- `Reports/Untagged.md`
- `Reports/BrokenLinks.md`
- `Reports/Orphans.md`
- `Reports/Duplicates.md`

### Use Case 3: Review The State Of A Vault Quickly

You want a short briefing without opening many files.

The app can produce a daily brief and summaries from local state.

Examples:

```text
/dailybrief
/summarize
```

These are built from indexed notes and maintained memory, not from an external service.

### Use Case 4: Use Markdown As A Simple Personal System

You want something between “just a folder of files” and “a huge productivity suite.”

This project gives you:

- a vault folder you already own
- Telegram and the CLI as fast inbox inputs
- generated notes in `Reports/`
- small agents with narrow responsibilities

That is enough to create a practical personal knowledge maintenance workflow.

## What The App Produces

The app writes useful files back into your vault.

Common examples:

- `Inbox/...` for captured Telegram messages, CLI notes, and imported files
- `Reports/daily.md`
- `Reports/<vault>-inbox-agent.md`
- `Reports/<vault>-inbox-agent-triage.md`
- `Reports/<vault>-inbox-agent-followups.md`
- `Reports/<vault>-inbox-agent-tasks.md`
- `Reports/Questions.md`
- `Reports/Followups.md`
- `Reports/Tasks.md`
- `Reports/Stale.md`
- `Reports/Untagged.md`
- `Reports/BrokenLinks.md`
- `Reports/Orphans.md`
- `Reports/Duplicates.md`

This matters because the output is not trapped in a database. You can open these files directly, edit them, sync them, or version them. Canonical maintained notes now use marked generated sections, so the app can refresh its own block while keeping your manual text outside that block.

The app also supports explicit frontmatter controls for clearing maintained outputs on purpose:

- `answered: true` or `answered_at:` for inbox captures you do not want in `Questions.md` anymore
- `inbox_status: done`, `archived`, or `moved` for inbox captures you no longer want in inbox-driven outputs like `Questions.md`, `Followups.md`, and `Tasks.md`
- `reviewed_at:` for notes you have reviewed recently and do not want treated as stale
- `ignore_maintenance: true` for notes you want hidden from `Stale.md`, `Untagged.md`, `BrokenLinks.md`, `Orphans.md`, and `Duplicates.md`

## A Simple Example Day

Imagine this happens during the day.

In the morning, you send:

```text
Need to send invoice reminder to Petro
```

At lunch, you send:

```text
Idea: compare two Python packaging approaches for the project
```

In the evening, you run:

```powershell
python -m lk_agent.cli.main agents run main-inbox
python -m lk_agent.cli.main agents run main-maintenance
```

Now the app creates Markdown outputs that help you review what matters:

- what needs follow-up
- what looks like a task
- what probably needs an answer
- which notes are stale
- which notes are still untagged

Then from Telegram you can ask:

```text
/followups
```

or:

```text
/taskrollup
```

and get the maintained view back in chat.

## Main Features Right Now

### Local Vault Indexing

The app can register one or more vault folders and rebuild a local index.

This gives you:

- local search
- extracted tasks
- extracted tags and links
- metadata for reports and briefs

For practical note-linking examples and maintenance behavior, see [Linking Guide](linking-guide.md).

### CLI Capture

You can also write directly into the inbox from the local terminal.

Example:

```powershell
python -m lk_agent.cli.main capture Remember to review parser cleanup
```

This uses the same capture path as Telegram, so future input methods can share the same inbox logic.

### File Import

You can import local files into the inbox without Telegram.

Examples:

```powershell
python -m lk_agent.cli.main inbox import D:\path\to\note.txt
python -m lk_agent.cli.main inbox scan-drop --source-dir D:\path\to\InboxDrop
```

`inbox import` keeps the source file in place. `inbox scan-drop` imports every file in the drop folder and, by default, archives the originals into `_processed/` so repeated scans do not duplicate them.

### Telegram Capture

Telegram is used as a narrow, safe input channel.

What it is good for:

- quick notes
- reminders
- questions to yourself
- small task captures

What it is not used for:

- open internet browsing
- unrestricted remote execution
- broad autonomous control

### Telegram Commands

Current safe commands:

- `/help`
- `/capture <text>`
- `/search <query>`
- `/summarize`
- `/dailybrief`
- `/tasks`
- `/recent`
- `/triage`
- `/followups`
- `/taskrollup`
- `/stale`
- `/untagged`
- `/brokenlinks`

What these mean in plain language:

- `/help`: show the grouped command list
- `/capture`: save this message as a note
- `/search`: search your local indexed notes
- `/summarize`: get a short local summary
- `/dailybrief`: get a broader state-of-the-vault brief
- `/tasks`: show open tasks found in notes
- `/recent`: show recent indexed notes
- `/triage`: show the generated inbox triage note
- `/followups`: show the generated follow-up queue
- `/taskrollup`: show the generated task rollup
- `/stale`: show the stale-note review
- `/untagged`: show the untagged-note review
- `/brokenlinks`: show broken internal links that need review

### Agents

The app has a few bounded agents.

Current built-ins:

- `inbox`
- `digest`
- `maintenance`

Current practical outputs:

- `inbox` writes per-run inbox reports and stable maintained notes for questions, follow-ups, and tasks
- `maintenance` writes a per-run maintenance report plus stable cleanup notes for stale notes, untagged notes, broken internal links, orphan notes, and duplicate-note suspicion
- `digest` writes brief digest-style summaries

They are intentionally narrow. That makes their behavior easier to inspect and trust.

### Memory

The app maintains two memory layers under `data/memory/`:

- shared memory for vault-level maintained state
- per-agent memory for recent output and internal tracking

This memory is used to build brief replies and keep generated outputs more stable.

## How To Start If You Are New

### 1. Initialize The App

```powershell
python -m lk_agent.cli.main init
```

### 2. Add Your Vault

```powershell
python -m lk_agent.cli.main vault add D:\path\to\vault --name main
python -m lk_agent.cli.main vault rebuild
```

### 3. Set Up Telegram

```powershell
python -m lk_agent.cli.main telegram set-token <YOUR_BOT_TOKEN>
python -m lk_agent.cli.main telegram allow-chat <YOUR_CHAT_ID>
python -m lk_agent.cli.main telegram set-inbox-vault main --dir Inbox
python -m lk_agent.cli.main telegram test
```

### 4. Bootstrap Default Agents

```powershell
python -m lk_agent.cli.main agents bootstrap --vault main
```

### 5. Run The Foreground Loop

```powershell
python -m lk_agent.cli.main serve --interval-seconds 5
```

### 6. Send A Message To The Bot

Examples:

```text
Review supplier contract tomorrow
```

```text
/capture idea for docs structure
```

### 7. Run The Agents If Needed

```powershell
python -m lk_agent.cli.main agents run main-inbox
python -m lk_agent.cli.main agents run main-maintenance
```

### 8. Review Results

Look in your vault at:

- `Inbox/`
- `Reports/Questions.md`
- `Reports/Followups.md`
- `Reports/Tasks.md`
- `Reports/Stale.md`
- `Reports/Untagged.md`
- `Reports/BrokenLinks.md`
- `Reports/Orphans.md`
- `Reports/Duplicates.md`

and in Telegram try:

```text
/triage
/followups
/taskrollup
```

## What Happens To Your Data

Your notes stay in Markdown files.

The app also stores derived state in:

- `data/app.db`
- `data/memory/shared/`
- `data/memory/agents/`

If you sync your vault with Git, Syncthing, Dropbox, or something else, that is still your choice. The app does not force a sync method.

## How To Clear Maintained Lists

Do not edit the generated block inside `Questions.md`, `Stale.md`, or similar files if your goal is to clear an item. Those files are regenerated. Edit the source note instead.

Examples:

- add `answered: true` or `answered_at: 2026-03-17T12:00:00+00:00` to an inbox capture to remove it from `Questions.md`
- add `inbox_status: done` to an inbox capture once you have processed it and do not want it to keep resurfacing in inbox-driven outputs
- add `reviewed_at: 2026-03-17T12:00:00+00:00` to a note to keep it out of `Stale.md`
- add `ignore_maintenance: true` to suppress a note from stale, untagged, broken-link, orphan-note, and duplicate-note reviews

After that, run:

```powershell
python -m lk_agent.cli.main vault rebuild
python -m lk_agent.cli.main agents run main-inbox
python -m lk_agent.cli.main agents run main-maintenance
```

## Important Current Limits

Be clear about the current state:

- no live file watcher or automatic drop-folder watch yet
- no detached background service yet
- no Telegram webhook mode
- no Ollama-driven agent behavior in the normal workflow yet
- no embeddings or semantic retrieval yet
- maintenance outputs are now stable Markdown notes and have Telegram reads for stale notes, untagged notes, broken internal links, and orphan notes

## Best Current Workflow

A simple working pattern today is:

1. keep your notes in Markdown
2. rebuild after meaningful manual edits
3. use Telegram for quick capture
4. run `main-inbox` regularly
5. review `Questions.md`, `Followups.md`, and `Tasks.md`
6. run `main-maintenance` regularly
7. review `Stale.md`, `Untagged.md`, `BrokenLinks.md`, `Orphans.md`, and `Duplicates.md`
8. use `/triage`, `/followups`, `/taskrollup`, `/stale`, `/untagged`, and `/brokenlinks` from Telegram when helpful

That gives you a lightweight, local, understandable note maintenance system.

## Git And Sharing This Project

If you want to put the project into Git, test it on a real vault, or let another person try it safely, use [docs/git-and-deployment.md](/d:/assistant_py/docs/git-and-deployment.md).

