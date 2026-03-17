from __future__ import annotations

import asyncio
import argparse
import json
import sys
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

from lk_agent.actions.capture import capture_text_to_inbox
from lk_agent.agents.manager import bootstrap_default_agents, list_agent_status, list_agents, run_agent
from lk_agent.core.config import ensure_runtime_dirs, load_config, save_config
from lk_agent.core.db import initialize
from lk_agent.core.models import VaultConfig
from lk_agent.core.secrets import ENV_PROVIDER, ENV_TELEGRAM_TOKEN, SecretStoreError, clear_telegram_token, default_provider, provider_supported, resolve_telegram_token, store_telegram_token, supported_providers
from lk_agent.integrations.runtime import run_loop
from lk_agent.integrations.scheduler import add_agent_job, add_digest_job, list_jobs, run_due_jobs, run_job
from lk_agent.integrations.telegram_bot import TelegramConfigError, config_summary, dependency_available, get_bot_identity, poll_once
from lk_agent.llm.ollama import OllamaError, probe
from lk_agent.vault.index import rebuild_all, rebuild_vault, search_notes


def _configure_console() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lk-agent", description="Local-first knowledge agent")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Create default config and runtime directories")
    init_parser.set_defaults(handler=cmd_init)

    onboard_parser = subparsers.add_parser("onboard", help="Detect local Ollama models and save default config")
    onboard_parser.add_argument("--base-url", default=None, help="Ollama base URL")
    onboard_parser.add_argument("--model", default=None, help="Preferred model name")
    onboard_parser.set_defaults(handler=cmd_onboard)

    config_parser = subparsers.add_parser("config", help="Inspect current config")
    config_parser.set_defaults(handler=cmd_config_show)

    serve_parser = subparsers.add_parser("serve", help="Run the local scheduler loop and Telegram polling")
    serve_parser.add_argument("--once", action="store_true", help="Run one loop cycle and exit")
    serve_parser.add_argument("--interval-seconds", type=int, default=None, help="Override loop interval")
    serve_parser.add_argument("--telegram-limit", type=int, default=20, help="Telegram update fetch limit per cycle")
    serve_parser.add_argument("--skip-telegram", action="store_true", help="Do not poll Telegram in the loop")
    serve_parser.add_argument("--skip-jobs", action="store_true", help="Do not run due jobs in the loop")
    serve_parser.set_defaults(handler=cmd_serve)

    vault_parser = subparsers.add_parser("vault", help="Manage registered vaults")
    vault_subparsers = vault_parser.add_subparsers(dest="vault_command")

    vault_add = vault_subparsers.add_parser("add", help="Register a new vault path")
    vault_add.add_argument("path", help="Path to a folder containing Markdown files")
    vault_add.add_argument("--name", default=None, help="Optional vault name")
    vault_add.set_defaults(handler=cmd_vault_add)

    vault_list = vault_subparsers.add_parser("list", help="List configured vaults")
    vault_list.set_defaults(handler=cmd_vault_list)

    vault_rebuild = vault_subparsers.add_parser("rebuild", help="Rebuild note index from registered vaults")
    vault_rebuild.add_argument("--name", default=None, help="Only rebuild the named vault")
    vault_rebuild.set_defaults(handler=cmd_vault_rebuild)

    search_parser = subparsers.add_parser("search", help="Search indexed notes")
    search_parser.add_argument("query", help="FTS5 query")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.set_defaults(handler=cmd_search)

    capture_parser = subparsers.add_parser("capture", help="Write a local inbox note from the CLI")
    capture_parser.add_argument("text", nargs="+", help="Capture text")
    capture_parser.add_argument("--vault", default=None, help="Target vault name; defaults to Telegram inbox vault or the only configured vault")
    capture_parser.add_argument("--dir", dest="inbox_dir", default=None, help="Relative inbox folder inside the vault")
    capture_parser.add_argument("--title", default="CLI Inbox Capture", help="Markdown note title")
    capture_parser.set_defaults(handler=cmd_capture)


    telegram_parser = subparsers.add_parser("telegram", help="Manage Telegram integration")
    telegram_subparsers = telegram_parser.add_subparsers(dest="telegram_command")

    telegram_info = telegram_subparsers.add_parser("info", help="Show Telegram config state")
    telegram_info.set_defaults(handler=cmd_telegram_info)

    telegram_test = telegram_subparsers.add_parser("test", help="Verify token and fetch bot identity")
    telegram_test.set_defaults(handler=cmd_telegram_test)

    telegram_token = telegram_subparsers.add_parser("set-token", help="Store Telegram bot token using the selected secret provider")
    telegram_token.add_argument("token", help="Telegram bot token")
    telegram_token.add_argument("--provider", choices=supported_providers(), default=None, help="Secret provider to use")
    telegram_token.add_argument("--target", default=None, help="Optional secret key or target name")
    telegram_token.set_defaults(handler=cmd_telegram_set_token)

    telegram_clear = telegram_subparsers.add_parser("clear-token", help="Delete the stored Telegram bot token")
    telegram_clear.set_defaults(handler=cmd_telegram_clear_token)

    telegram_allow = telegram_subparsers.add_parser("allow-chat", help="Allow a Telegram chat id")
    telegram_allow.add_argument("chat_id", type=int, help="Telegram chat id")
    telegram_allow.set_defaults(handler=cmd_telegram_allow_chat)

    telegram_inbox = telegram_subparsers.add_parser("set-inbox-vault", help="Configure inbox destination vault")
    telegram_inbox.add_argument("name", help="Configured vault name")
    telegram_inbox.add_argument("--dir", default="Inbox", help="Relative inbox folder inside the vault")
    telegram_inbox.set_defaults(handler=cmd_telegram_set_inbox_vault)

    telegram_poll = telegram_subparsers.add_parser("poll-once", help="Fetch Telegram updates once and write inbox notes")
    telegram_poll.add_argument("--limit", type=int, default=20)
    telegram_poll.set_defaults(handler=cmd_telegram_poll_once)

    agents_parser = subparsers.add_parser("agents", help="Manage bounded agents")
    agents_subparsers = agents_parser.add_subparsers(dest="agents_command")

    agents_list_parser = agents_subparsers.add_parser("list", help="List configured agents")
    agents_list_parser.set_defaults(handler=cmd_agents_list)

    agents_status_parser = agents_subparsers.add_parser("status", help="Show detailed agent status and maintained-note paths")
    agents_status_parser.add_argument("--name", default=None, help="Optional agent name")
    agents_status_parser.set_defaults(handler=cmd_agents_status)

    agents_bootstrap_parser = agents_subparsers.add_parser("bootstrap", help="Create default agents for a vault")
    agents_bootstrap_parser.add_argument("--vault", required=True, help="Vault name")
    agents_bootstrap_parser.set_defaults(handler=cmd_agents_bootstrap)

    agents_run_parser = agents_subparsers.add_parser("run", help="Run a named agent immediately")
    agents_run_parser.add_argument("name", help="Agent name")
    agents_run_parser.set_defaults(handler=cmd_agents_run)

    jobs_parser = subparsers.add_parser("jobs", help="Manage scheduled jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command")

    jobs_list_parser = jobs_subparsers.add_parser("list", help="List configured jobs")
    jobs_list_parser.set_defaults(handler=cmd_jobs_list)

    jobs_add_digest = jobs_subparsers.add_parser("add-digest", help="Create or update a digest job")
    jobs_add_digest.add_argument("name", help="Job name")
    jobs_add_digest.add_argument("--vault", required=True, help="Vault name")
    jobs_add_digest.add_argument("--schedule", required=True, help="manual, hourly, daily, or interval:<seconds>")
    jobs_add_digest.add_argument("--output", required=True, help="Output Markdown path inside the vault")
    jobs_add_digest.set_defaults(handler=cmd_jobs_add_digest)

    jobs_add_agent = jobs_subparsers.add_parser("add-agent", help="Create or update a scheduled agent job")
    jobs_add_agent.add_argument("name", help="Job name")
    jobs_add_agent.add_argument("--agent", required=True, help="Agent name")
    jobs_add_agent.add_argument("--schedule", required=True, help="manual, hourly, daily, or interval:<seconds>")
    jobs_add_agent.set_defaults(handler=cmd_jobs_add_agent)

    jobs_run = jobs_subparsers.add_parser("run", help="Run a named job immediately")
    jobs_run.add_argument("name", help="Job name")
    jobs_run.set_defaults(handler=cmd_jobs_run)

    jobs_due = jobs_subparsers.add_parser("run-due", help="Run all jobs that are currently due")
    jobs_due.set_defaults(handler=cmd_jobs_run_due)

    return parser


def cmd_init(_: argparse.Namespace) -> int:
    config = load_config()
    ensure_runtime_dirs(config)
    save_config(config)
    initialize(config.resolved_db_path(Path.cwd())).close()
    print(f"initialized config at {Path.cwd() / 'config.json'}")
    print(f"database ready at {config.resolved_db_path(Path.cwd())}")
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    config = load_config()
    if args.base_url:
        config.ollama.base_url = args.base_url
    try:
        result = probe(config.ollama.base_url, args.model or config.ollama.default_model)
    except OllamaError as exc:
        print(f"ollama probe failed: {exc}", file=sys.stderr)
        return 1

    models = result["models"]
    selected_model = args.model or result["selected_model"]
    if not models:
        print("ollama is reachable but no local models were found", file=sys.stderr)
        return 1
    if selected_model is None:
        print("no model could be selected", file=sys.stderr)
        return 1
    if selected_model not in models:
        print(f"requested model '{selected_model}' was not found locally", file=sys.stderr)
        return 1

    config.ollama.default_model = selected_model
    ensure_runtime_dirs(config)
    save_config(config)
    initialize(config.resolved_db_path(Path.cwd())).close()

    print(f"ollama base URL: {config.ollama.base_url}")
    print(f"available models: {', '.join(models)}")
    print(f"default model: {selected_model}")
    print(f"test response: {str(result['sample']).strip().replace(chr(10), ' ')}")
    return 0


def cmd_config_show(_: argparse.Namespace) -> int:
    config = load_config()
    telegram = asdict(config.telegram)
    telegram["bot_token"] = "<legacy-plain-text>" if config.telegram.bot_token else None
    telegram["token_available"] = bool(resolve_telegram_token(config.telegram, config))
    print(
        json.dumps(
            {
                "data_dir": config.data_dir,
                "db_path": config.db_path,
                "vaults": [asdict(vault) for vault in config.vaults],
                "ollama": asdict(config.ollama),
                "telegram": telegram,
                "scheduler": asdict(config.scheduler),
            },
            indent=2,
        )
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        return run_loop(
            interval_seconds=args.interval_seconds,
            telegram_limit=args.telegram_limit,
            once=args.once,
            skip_telegram=args.skip_telegram,
            skip_jobs=args.skip_jobs,
            reporter=print,
        )
    except KeyboardInterrupt:
        print("stopped")
        return 0
    except Exception as exc:
        print(f"serve failed: {exc}", file=sys.stderr)
        return 1


def cmd_vault_add(args: argparse.Namespace) -> int:
    config = load_config()
    raw_path = Path(args.path).expanduser().resolve()
    if not raw_path.exists():
        print(f"vault path does not exist: {raw_path}", file=sys.stderr)
        return 1
    if not raw_path.is_dir():
        print(f"vault path is not a directory: {raw_path}", file=sys.stderr)
        return 1

    name = args.name or raw_path.name
    for vault in config.vaults:
        if vault.name == name:
            vault.path = str(raw_path)
            break
    else:
        config.vaults.append(VaultConfig(name=name, path=str(raw_path)))

    ensure_runtime_dirs(config)
    save_config(config)
    initialize(config.resolved_db_path(Path.cwd())).close()
    print(f"registered vault '{name}' at {raw_path}")
    return 0


def cmd_vault_list(_: argparse.Namespace) -> int:
    config = load_config()
    if not config.vaults:
        print("no vaults configured")
        return 0
    for vault in config.vaults:
        print(f"{vault.name}: {vault.path} ({'enabled' if vault.enabled else 'disabled'})")
    return 0


def cmd_vault_rebuild(args: argparse.Namespace) -> int:
    config = load_config()
    ensure_runtime_dirs(config)
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        if args.name:
            vault = next((item for item in config.vaults if item.name == args.name), None)
            if vault is None:
                print(f"unknown vault: {args.name}", file=sys.stderr)
                return 1
            result = rebuild_vault(connection, vault)
            print(f"{vault.name}: indexed={result['indexed']} deleted={result['deleted']}")
            return 0

        results = rebuild_all(connection, config.vaults)
        if not results:
            print("no enabled vaults configured")
            return 0
        for name, result in results:
            print(f"{name}: indexed={result['indexed']} deleted={result['deleted']}")
        return 0
    finally:
        connection.close()


def resolve_capture_vault(config, preferred_name: str | None) -> VaultConfig | None:
    if preferred_name:
        return next((vault for vault in config.vaults if vault.name == preferred_name), None)
    if config.telegram.inbox_vault:
        return next((vault for vault in config.vaults if vault.name == config.telegram.inbox_vault), None)
    enabled = [vault for vault in config.vaults if vault.enabled]
    if len(enabled) == 1:
        return enabled[0]
    return None


def cmd_capture(args: argparse.Namespace) -> int:
    config = load_config()
    vault = resolve_capture_vault(config, args.vault)
    if vault is None:
        target = args.vault or config.telegram.inbox_vault or '<unspecified>'
        print(f"unknown or ambiguous capture vault: {target}", file=sys.stderr)
        return 1
    inbox_dir = args.inbox_dir or config.telegram.inbox_dir or "Inbox"
    capture_text = " ".join(args.text).strip()
    if not capture_text:
        print("capture text is empty", file=sys.stderr)
        return 1
    ensure_runtime_dirs(config)
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        note_path = capture_text_to_inbox(
            connection,
            vault=vault,
            inbox_dir=inbox_dir,
            source="cli",
            title=args.title,
            text=capture_text,
            metadata={"captured_at": datetime.now(timezone.utc).isoformat()},
        )
        connection.commit()
    finally:
        connection.close()
    print(f"captured to {note_path}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        rows = search_notes(connection, args.query, args.limit)
    finally:
        connection.close()

    if not rows:
        print("no matches")
        return 0

    for row in rows:
        print(row["title"])
        print(f"  {row['absolute_path']}")
        print(f"  {row['snippet']}")
    return 0


def cmd_telegram_info(_: argparse.Namespace) -> int:
    config = load_config()
    summary = config_summary(config)
    summary["dependency_available"] = dependency_available()
    summary["default_provider"] = default_provider()
    summary["supported_providers"] = supported_providers()
    print(json.dumps(summary, indent=2))
    return 0


def cmd_telegram_test(_: argparse.Namespace) -> int:
    config = load_config()
    try:
        identity = asyncio.run(get_bot_identity(config))
    except TelegramConfigError as exc:
        print(f"telegram error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"telegram test failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(identity, indent=2))
    return 0


def cmd_telegram_set_token(args: argparse.Namespace) -> int:
    config = load_config()
    provider = args.provider or config.telegram.bot_token_provider or default_provider()
    if not provider_supported(provider):
        print(f"secret provider is not supported on this system: {provider}", file=sys.stderr)
        return 1
    if provider == ENV_PROVIDER:
        print(f"provider '{ENV_PROVIDER}' is read-only; set {ENV_TELEGRAM_TOKEN} in your environment instead", file=sys.stderr)
        return 1
    try:
        chosen_provider, chosen_target = store_telegram_token(
            config,
            args.token,
            provider=provider,
            target=args.target,
            root=Path.cwd(),
        )
    except SecretStoreError as exc:
        print(f"secret storage error: {exc}", file=sys.stderr)
        return 1
    config.telegram.bot_token_provider = chosen_provider
    config.telegram.bot_token_ref = chosen_target
    config.telegram.bot_token = None
    save_config(config)
    print(f"telegram bot token stored using provider '{chosen_provider}' as '{chosen_target}'")
    return 0


def cmd_telegram_clear_token(_: argparse.Namespace) -> int:
    config = load_config()
    try:
        deleted = clear_telegram_token(config, root=Path.cwd())
    except SecretStoreError as exc:
        print(f"secret storage error: {exc}", file=sys.stderr)
        return 1
    config.telegram.bot_token = None
    config.telegram.bot_token_provider = None
    config.telegram.bot_token_ref = None
    save_config(config)
    print("telegram bot token cleared" if deleted else "telegram bot token reference cleared")
    return 0


def cmd_telegram_allow_chat(args: argparse.Namespace) -> int:
    config = load_config()
    if args.chat_id not in config.telegram.allowed_chat_ids:
        config.telegram.allowed_chat_ids.append(args.chat_id)
        config.telegram.allowed_chat_ids.sort()
    save_config(config)
    print(f"allowed chat id: {args.chat_id}")
    return 0


def cmd_telegram_set_inbox_vault(args: argparse.Namespace) -> int:
    config = load_config()
    if not any(vault.name == args.name for vault in config.vaults):
        print(f"unknown vault: {args.name}", file=sys.stderr)
        return 1
    config.telegram.inbox_vault = args.name
    config.telegram.inbox_dir = args.dir
    save_config(config)
    print(f"telegram inbox destination: {args.name}/{args.dir}")
    return 0


def cmd_telegram_poll_once(args: argparse.Namespace) -> int:
    config = load_config()
    ensure_runtime_dirs(config)
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        results = poll_once(connection, config, args.limit)
    except TelegramConfigError as exc:
        print(f"telegram error: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()

    if not results:
        print("no new Telegram messages")
        return 0
    for result in results:
        suffix = f" command={result['command']}" if result.get("command") else ""
        target = result["note_path"] or "<no note>"
        print(f"chat={result['chat_id']} msg={result['message_id']} -> {target}{suffix}")
    return 0


def cmd_agents_list(_: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        rows = list_agents(connection)
    finally:
        connection.close()
    if not rows:
        print("no agents configured")
        return 0
    for row in rows:
        print(f"{row['name']}: kind={row['kind']} vault={row['vault_name']} output={row['output_path']} status={row['last_status']}")
    return 0


def cmd_agents_status(args: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        rows = list_agent_status(connection, args.name)
    finally:
        connection.close()
    if not rows:
        print("no matching agents")
        return 0
    for row in rows:
        print(f"{row['name']}: kind={row['kind']} vault={row['vault_name']} status={row['last_status']} last_run={row['last_run_at']}")
        print(f"  output={row['output_path']}")
        if row['memory_path']:
            print(f"  memory={row['memory_path']} updated={row['memory_updated_at']}")
        managed_paths = row.get('managed_paths', {}) or {}
        if managed_paths:
            for key, value in sorted(managed_paths.items()):
                print(f"  {key}={value}")
        if row['last_error']:
            print(f"  error={row['last_error']}")
    return 0


def cmd_agents_bootstrap(args: argparse.Namespace) -> int:
    config = load_config()
    if not any(vault.name == args.vault for vault in config.vaults):
        print(f"unknown vault: {args.vault}", file=sys.stderr)
        return 1
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        names = bootstrap_default_agents(connection, args.vault)
    finally:
        connection.close()
    print(f"bootstrapped agents: {', '.join(names)}")
    return 0


def cmd_agents_run(args: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        result = run_agent(connection, config, args.name, root=Path.cwd())
    except Exception as exc:
        print(f"agent failed: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    print(f"agent '{args.name}' wrote {result}")
    return 0


def cmd_jobs_list(_: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        rows = list_jobs(connection)
    finally:
        connection.close()

    if not rows:
        print("no jobs configured")
        return 0
    for row in rows:
        extra = ""
        config_json = json.loads(row["config_json"])
        if row["kind"] == "agent":
            extra = f" agent={config_json.get('agent_name')}"
        print(f"{row['name']}: kind={row['kind']} schedule={row['schedule']} vault={row['vault_name']} output={row['output_path']} status={row['last_status']}{extra}")
    return 0


def cmd_jobs_add_digest(args: argparse.Namespace) -> int:
    if not valid_schedule(args.schedule):
        print("invalid schedule; use manual, hourly, daily, or interval:<seconds>", file=sys.stderr)
        return 1
    config = load_config()
    if not any(vault.name == args.vault for vault in config.vaults):
        print(f"unknown vault: {args.vault}", file=sys.stderr)
        return 1
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        add_digest_job(connection, args.name, args.schedule, args.vault, args.output)
    finally:
        connection.close()
    print(f"saved digest job '{args.name}'")
    return 0


def cmd_jobs_add_agent(args: argparse.Namespace) -> int:
    if not valid_schedule(args.schedule):
        print("invalid schedule; use manual, hourly, daily, or interval:<seconds>", file=sys.stderr)
        return 1
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        known = connection.execute("SELECT 1 FROM agents WHERE name = ?", (args.agent,)).fetchone()
        if known is None:
            print(f"unknown agent: {args.agent}", file=sys.stderr)
            return 1
        add_agent_job(connection, args.name, args.schedule, args.agent)
    finally:
        connection.close()
    print(f"saved agent job '{args.name}'")
    return 0


def cmd_jobs_run(args: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        result = run_job(connection, config, args.name, root=Path.cwd())
    except Exception as exc:
        print(f"job failed: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    print(f"job '{args.name}' wrote {result}")
    return 0


def cmd_jobs_run_due(_: argparse.Namespace) -> int:
    config = load_config()
    connection = initialize(config.resolved_db_path(Path.cwd()))
    try:
        results = run_due_jobs(connection, config, root=Path.cwd())
    except Exception as exc:
        print(f"scheduler failed: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    if not results:
        print("no jobs due")
        return 0
    for name, path in results:
        print(f"job '{name}' wrote {path}")
    return 0


def valid_schedule(value: str) -> bool:
    if value in {"manual", "hourly", "daily"}:
        return True
    if value.startswith("interval:"):
        try:
            return int(value.split(":", 1)[1]) > 0
        except ValueError:
            return False
    return False


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 1
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())

