"""Standalone commands with fixed, credential-free failure messages."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .instance import credential_provider, default_home, load_policy
from .secure_files import SetupError, file_lock

COMMANDS = frozenset(("setup", "doctor", "start", "key", "migrate", "install-service", "service-key", "service-unit"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in sorted(COMMANDS):
        command = sub.add_parser(operation)
        if operation not in ("install-service", "service-key", "service-unit"):
            command.add_argument("--home", type=Path, default=default_home(), help="Private standalone instance directory")
        if operation == "doctor":
            command.add_argument("--offline", action="store_true", help="Check local files and state without API requests")
        if operation == "start":
            command.add_argument("--checked", action="store_true", help=argparse.SUPPRESS)
        if operation in ("key", "service-key"):
            command.add_argument("name", choices=("discord", "jev"))
        if operation == "migrate":
            command.add_argument("--from-hermes", type=Path, required=True, help="Disabled and stopped Hermes profile")
        if operation == "install-service":
            command.add_argument("--wheel", type=Path, required=True, help="Locally built moderator wheel")
            command.add_argument("--start", action="store_true", help="Enable and start the reviewed installation")
        if operation in ("install-service", "service-unit"):
            command.add_argument("--plain-credentials", action="store_true", help="Explicitly use root-only, unencrypted credential files")
        if operation == "service-unit":
            command.add_argument("--jev", action="store_true")
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "home"):
            args.home = args.home.absolute()
        if args.operation == "setup":
            from .setup_wizard import wizard
            return 0 if wizard(args.home) else 1
        if args.operation == "doctor":
            from .doctor import check, print_report
            report = check(args.home, offline=args.offline)
            print_report(report)
            return 0 if report["ok"] else 2
        if args.operation == "start":
            from .standalone import configure_logging, serve
            configure_logging()
            # A startup error must not cause the SDK to log request URLs, keys
            # or exception locals. The CLI also catches errors without a traceback.
            if not args.checked:
                from .doctor import check, print_report
                report = check(args.home)
                if not report["ok"]:
                    print_report(report)
                    return 2
            return asyncio.run(serve(args.home))
        if args.operation == "key":
            from .credentials import prompt_secret
            policy = load_policy(args.home)
            if args.name == "jev" and not policy.ai_enabled:
                raise SetupError("Enable JEV in the reviewed policy before adding its credential.")
            credentials = credential_provider(args.home)
            if credentials.backend != "file":
                raise SetupError("System services use: sudo liberdus-moderator service-key discord (or jev).")
            with file_lock(args.home / "state/moderation.lock"):
                value = prompt_secret("New credential (hidden): ")
                credentials.put(args.name, value, replace=True)
            print("Credential replaced. Start the bot to use the new value.")
            return 0
        if args.operation == "migrate":
            from .migrate import migrate
            print(json.dumps(migrate(args.from_hermes, args.home), sort_keys=True))
            print("Keep the Hermes moderator disabled. Run doctor before starting this standalone instance.")
            return 0
        if args.operation == "service-unit":
            from .linux_service import service_unit
            print(service_unit(ai=args.jev, encrypted=not args.plain_credentials), end="")
            return 0
        if args.operation == "install-service":
            from .linux_service import install
            install(args.wheel, encrypted=not args.plain_credentials, start=args.start)
            return 0
        if args.operation == "service-key":
            from .linux_service import replace_service_key
            replace_service_key(args.name)
            return 0
    except SetupError as error:
        print("Stopped: " + str(error), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("Cancelled. No credentials were printed.", file=sys.stderr)
        return 130
    except Exception:
        # Unexpected library/parser errors may contain pasted secrets or HTTP
        # objects. Show no traceback or raw message, including exception causes.
        print("Stopped: configuration, credentials, or connection checks failed. Check the setup guide and run doctor; sensitive error details were withheld.", file=sys.stderr)
        return 2
