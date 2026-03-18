# 5 Minute Quickstart

This is the fastest way to see the app do something useful.

Goal:

- add one vault
- connect Telegram
- or use local CLI/file capture
- capture one message
- generate one inbox report
- read it back

## Before You Start

You need:

- Python 3.11+
- this repo checked out
- one folder with Markdown files, or an empty folder you want to use as a vault
- a Telegram bot token if you want phone capture right away

Install the package from the repo root:

```powershell
python -m pip install -e .
```

## Step 1. Initialize The App

```powershell
python -m lk_agent.cli.main init
```

This creates the local config and data folders.

## Step 2. Add Your Vault

Example:

```powershell
python -m lk_agent.cli.main vault add D:\notes --name main
python -m lk_agent.cli.main vault rebuild
```

What this does:

- registers `D:\notes` as a vault named `main`
- builds the local SQLite index

## Step 3. Set Up Telegram

```powershell
python -m lk_agent.cli.main telegram set-token <YOUR_BOT_TOKEN>
python -m lk_agent.cli.main telegram allow-chat <YOUR_CHAT_ID>
python -m lk_agent.cli.main telegram set-inbox-vault main --dir Inbox
python -m lk_agent.cli.main telegram test
```

What this does:

- stores the bot token
- allows your Telegram chat to use the bot
- tells the app where Telegram captures should be written
- confirms the bot can be reached

## Step 4. Bootstrap The Default Agents

```powershell
python -m lk_agent.cli.main agents bootstrap --vault main
```

This gives you the built-in agents for the vault:

- `main-inbox`
- `main-digest`
- `main-maintenance`

## Step 5. Start The Foreground Loop

```powershell
python -m lk_agent.cli.main serve --interval-seconds 5
```

Leave this terminal running.

## Optional Local Capture

If you want to test without Telegram first, you can create an inbox note locally:

```powershell
python -m lk_agent.cli.main capture Remember to review parser cleanup
python -m lk_agent.cli.main inbox import D:\path\to\note.txt
```

## Step 6. Send One Telegram Message

Send something simple to the bot, for example:

```text
Need to follow up with Anna about the invoice
```

Because this is plain text, the app should save it as an inbox note.

## Step 7. Run The Inbox Agent

In another terminal:

```powershell
python -m lk_agent.cli.main agents run main-inbox
```

This turns recent inbox captures into maintained Markdown outputs.

## Step 8. Check The Results

Look in your vault for files like:

```text
Inbox\...
Reports\main-inbox-agent.md
Reports\main-inbox-agent-triage.md
Reports\main-inbox-agent-followups.md
Reports\main-inbox-agent-tasks.md
```

These are real Markdown files you can open directly.

## Step 9. Ask The Bot For The Generated View

Send these to the bot:

```text
/triage
/followups
/taskrollup
```

You should get readable Telegram replies based on the generated local notes.

## If You Only Want The Shortest Possible Demo

Run these commands, in order:

```powershell
python -m lk_agent.cli.main init
python -m lk_agent.cli.main vault add D:\notes --name main
python -m lk_agent.cli.main vault rebuild
python -m lk_agent.cli.main telegram set-token <YOUR_BOT_TOKEN>
python -m lk_agent.cli.main telegram allow-chat <YOUR_CHAT_ID>
python -m lk_agent.cli.main telegram set-inbox-vault main --dir Inbox
python -m lk_agent.cli.main telegram test
python -m lk_agent.cli.main agents bootstrap --vault main
python -m lk_agent.cli.main serve --interval-seconds 5
```

Then:

1. send a plain text message to the bot
2. run `python -m lk_agent.cli.main agents run main-inbox`
3. send `/followups` or `/stale`

## What You Have After 5 Minutes

If everything worked, you now have:

- a local indexed Markdown vault
- Telegram inbox capture
- an inbox agent that writes useful reports
- Telegram commands that read those reports back

That is the current core workflow of the project.

## If Something Fails

Try these checks:

```powershell
python -m lk_agent.cli.main telegram info
python -m lk_agent.cli.main telegram test
python -m lk_agent.cli.main vault list
python -m lk_agent.cli.main agents list
```

If Telegram is the problem, the most common causes are:

- wrong bot token
- wrong chat ID
- inbox vault not configured
- `serve` is not running


