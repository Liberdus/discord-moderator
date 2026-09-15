"""Local JSON fixture runner. No Discord or Hermes network connection is made."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .commands import CommandRequest, handle_command
from .config import Config
from .engine import Engine
from .models import MessageEvent
from .storage import Store


def _emit(value):
    # Escape terminal control characters instead of rendering untrusted content.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _events(path):
    with Path(path).open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            if len(line) > 100_000:
                raise ValueError(f"Event line {number} exceeds the fixture size limit")
            try:
                yield MessageEvent.from_dict(json.loads(line))
            except (ValueError, TypeError) as error:
                raise ValueError(f"Invalid event at line {number}: {error}") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("validate", "replay", "simulate", "command", "incidents", "reports"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
        if name != "validate":
            command.add_argument("--database", help="Override SQLite path; relative paths use the working directory")
        if name in ("replay", "simulate"):
            command.add_argument("--events", required=True, help="JSONL fixture file; fixture timestamps drive a monotonic replay clock")
        if name == "command":
            command.add_argument("--request", required=True, help="JSON request with trusted synthetic identity metadata")
    args = parser.parse_args(argv)
    try:
        config = Config.from_file(args.config)
        if args.operation == "validate":
            _emit({"valid": True, "mode": config.mode, "coverage": "code_only", "policy_hash": config.policy_hash})
            return 0
        path = ":memory:" if args.operation == "simulate" else args.database or config.storage.database_path
        with Store(path) as store:
            if args.operation in ("replay", "simulate"):
                replay_time = [store.get_setting("last_processed_at", 0.0)]
                engine = Engine(config, store, clock=lambda: replay_time[0])
                for event in _events(args.events):
                    replay_time[0] = max(replay_time[0], event.modified_at)
                    _emit(engine.process(event))
                _emit({"summary": engine.status(), "simulation": args.operation == "simulate"})
            else:
                if args.operation == "command":
                    engine = Engine(config, store)
                    request_path = Path(args.request)
                    if request_path.stat().st_size > 100_000:
                        raise ValueError("Command fixture exceeds the size limit")
                    request = CommandRequest.from_dict(json.loads(request_path.read_text(encoding="utf-8")))
                    response = handle_command(engine, request)
                    _emit(response)
                    return 0 if response["ok"] else 2
                elif args.operation == "incidents":
                    _emit(store.incidents())
                elif args.operation == "reports":
                    _emit(store.reports())
        return 0
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
