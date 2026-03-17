# Lightweight Local-First Knowledge Agent

This project is for people who already keep notes in Markdown and want a small local assistant that helps keep those notes usable.

It does not try to replace your files. It does not trap your data in a remote service. It does not need a big web app to be useful.

It watches the state of your vault through rebuilds, stores derived metadata in SQLite, accepts quick captures from Telegram, and writes useful maintenance notes back into your Markdown folders.

If you want a simple description, it is this:

- your Markdown files stay the source of truth
- Telegram becomes a quick capture inbox
- scheduled jobs and agents keep summaries, follow-up queues, task lists, and cleanup notes up to date
- everything stays local unless you choose to sync the files yourself

## Why Someone Would Use This

Many people have a notes folder full of useful information that slowly turns into a mess.

Typical problems:

- ideas get sent from your phone but never land in the right place
- tasks are buried across random notes
- you forget what needs a reply or follow-up
- you know something is in the vault, but search is messy
- maintenance work like “what is stale?” or “what is untagged?” never gets done

This project is meant to solve that in a practical way.

## What It Feels Like In Practice

You send a Telegram message like:

```text
Call Alex tomorrow about the invoice
```

The app can save that into your vault inbox as a Markdown note.

Later, the inbox agent can turn recent inbox notes into:

- an inbox triage note
- a follow-up queue
- a task rollup
- stable maintained notes like `Questions.md`, `Followups.md`, and `Tasks.md`
- shared memory notes that help build briefs and summaries

The maintenance agent can also write stable cleanup notes like:

- `Stale.md`
- `Untagged.md`
- `BrokenLinks.md`
- `Orphans.md`
- `Duplicates.md`

So instead of random captures piling up forever, you get maintained notes you can actually review.

## Good Use Cases

### 1. Personal Inbox For Ideas And Reminders

You are away from the computer and want to save something quickly.

Examples:

```text
Remember to compare hosting prices next week
```

```text
Idea: write a short guide about local Python tooling
```

```text
Ask Marina if the draft is final
```

Send that to the bot. The app stores it in your vault. Later, the inbox agent groups it into follow-ups, questions, and tasks.

### 2. Daily Or Weekly Review Of A Markdown Vault

You keep project notes, personal notes, or research notes in Markdown and want a compact way to review what changed.

The app can generate:

- digest notes
- inbox triage summaries
- follow-up queues
- task rollups
- untagged note reviews
- stale note reviews

That makes it easier to look at maintained notes instead of scanning dozens of files.

### 3. Quick Local Search Through Notes

You want to search your vault from the CLI or Telegram.

Example:

```text
/search invoice
```

The bot replies with local search results based on the SQLite index. No external search service is required.

### 4. Small Personal Knowledge Maintenance System

You want a lightweight system that helps maintain order in your files, but you do not want a giant automation platform.

This project is a good fit if you want:

- local files first
- a small CLI
- Telegram capture
- scheduled maintenance
- deterministic behavior you can inspect

## What It Does Right Now

Implemented now:

- register one or more Markdown vaults
- rebuild an index of notes, tags, links, and tasks
- search notes locally with SQLite FTS5
- connect a Telegram bot for inbox capture
- test Telegram bot configuration with `telegram test`
- run a foreground `serve` loop for Telegram polling and scheduled jobs
- store Telegram secrets through a provider-based secret layer
- run bounded agents:
  - `inbox`
  - `digest`
  - `maintenance`
- write generated maintenance notes back into the vault
- store shared memory and per-agent memory under `data/memory/`

Current Telegram commands:

- `/help`
- `/capture`
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

## What It Creates For You

Inside your vault, the app can create things like:

- `Inbox/...` for Telegram captures
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

That means the app is not just answering commands. It is writing useful Markdown artifacts you can keep, edit, sync, or version however you want. For canonical maintained notes, the app now owns a marked generated section and preserves any user text outside that section on future runs.

The app also now supports a few explicit frontmatter controls so you can intentionally clear maintained lists:

- `answered: true` or `answered_at: <timestamp>` to suppress a question-like inbox capture from `Questions.md`
- `reviewed_at: <timestamp>` to suppress a note from `Stale.md` when that review time is recent
- `ignore_maintenance: true` to suppress a note from `Stale.md`, `Untagged.md`, `BrokenLinks.md`, `Orphans.md`, and `Duplicates.md`

## Git And Testing On Real Data

If you want to put this into Git and start testing on a real vault, use the deployment notes in [docs/git-and-deployment.md](/d:/assistant_py/docs/git-and-deployment.md).

The short version is:

- commit code, docs, tests, and `config.example.json`
- keep `config.json`, `data/`, Telegram secrets, generated reports, and real inbox captures local
- start with a copied test vault before pointing the app at your main notes folder

## What It Does Not Try To Be

This is not:

- a cloud note service
- a browser agent
- a plugin marketplace
- a giant no-code automation system
- a full autonomous AI platform
- a polished always-on desktop app

The current design goal is simpler: be useful, local, understandable, and small.

## How A Beginner Can Start

1. Create the app folders:

```powershell
python -m lk_agent.cli.main init
```

2. Add your Markdown vault:

```powershell
python -m lk_agent.cli.main vault add D:\path\to\vault --name main
python -m lk_agent.cli.main vault rebuild
```

3. Set up Telegram if you want phone capture:

```powershell
python -m lk_agent.cli.main telegram set-token <YOUR_BOT_TOKEN>
python -m lk_agent.cli.main telegram allow-chat <YOUR_CHAT_ID>
python -m lk_agent.cli.main telegram set-inbox-vault main --dir Inbox
python -m lk_agent.cli.main telegram test
```

4. Bootstrap the default agents:

```powershell
python -m lk_agent.cli.main agents bootstrap --vault main
```

5. Run the app in foreground mode:

```powershell
python -m lk_agent.cli.main serve --interval-seconds 5
```

6. Send a message to the bot, then run the inbox and maintenance agents if needed:

```powershell
python -m lk_agent.cli.main agents run main-inbox
python -m lk_agent.cli.main agents run main-maintenance
```

7. Review the generated notes in `Reports/`.

## A Very Simple Example Flow

Imagine this is your day:

1. You message the bot:

```text
Need to send revised contract to Anna on Friday
```

2. The app stores that as a Markdown inbox note.

3. Later, `main-inbox` runs.

4. The app writes:

- a triage summary
- a follow-up queue
- a task rollup
- stable notes like `Questions.md`, `Followups.md`, and `Tasks.md`

5. Then `main-maintenance` runs.

6. The app writes cleanup surfaces like `Stale.md`, `Untagged.md`, `BrokenLinks.md`, `Orphans.md`, and `Duplicates.md`.

7. From Telegram, you ask:

```text
/taskrollup
```

and get a readable view of current task-like items.

That is the core promise of the project: turn quick captures and scattered notes into maintained Markdown outputs.

## Clearing Maintained Lists

If you want a note to stop appearing in maintained outputs, edit the source note instead of the generated report:

- to clear an item from `Questions.md`, add `answered: true` or `answered_at:` in the source inbox note frontmatter
- to clear an item from `Stale.md`, add `reviewed_at:` with a recent timestamp in the source note frontmatter
- to fully suppress a note from maintenance lists, add `ignore_maintenance: true`

Then run:

```powershell
python -m lk_agent.cli.main vault rebuild
python -m lk_agent.cli.main agents run main-inbox
python -m lk_agent.cli.main agents run main-maintenance
```

## Current Limitations

Right now, the project still has important limits:

- no live file watcher yet
- no detached background service yet
- no Telegram webhook mode
- no LLM-driven agent behavior in normal workflows yet
- no embeddings or semantic retrieval yet
- vault sync or upload is still your responsibility
- maintenance surfaces now have Telegram reads for stale notes, untagged notes, broken internal links, and orphan notes

## Documentation

- [5 Minute Quickstart](docs/quickstart-5min.md)
- [User Guide](docs/user-guide.md)
- [Linking Guide](docs/linking-guide.md)
- [Foreground Usage](docs/foreground-usage.md)
- [Architecture](docs/architecture.md)
- [MVP Spec](docs/mvp-spec.md)
- [Data Model](docs/data-model.md)
- [Roadmap](docs/roadmap.md)




