"""Owner-run JEV batch evaluation: synthetic inputs, real API, no Discord access."""

import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import textwrap
import time

from .classifier import MODEL, RUBRIC_HASH, RESERVED_INPUT_TOKENS, RESERVED_MICROUSD, encoded, validate_response
from .config import Config
from .configure_jev import regular_owned
from .jev import ProviderError, evaluate
from .jev_cases import CASES, SUITE, requests
from .storage import Store

TABLE = "jev_batch_attempts_v1"
MAX_RUN_SECONDS = 180
MAX_RECORDS = 1000
ERRORS = frozenset({"missing_key", "authentication_failed", "access_denied", "rate_limited",
                    "provider_overloaded", "http_error", "response_too_large", "invalid_json",
                    "provider_or_response_error", "timeout", "uncertain", "stale",
                    "usage_exceeds_reservation"})


def run_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", value):
        raise ValueError("Run name must be 1-32 lowercase letters, digits or hyphens, starting with a letter")
    return value


def owned_directory(path):
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("Expected owned profile directories without symlinks")


def load_policy(profile):
    for directory in (profile.parent.parent, profile.parent, profile, profile / "state"):
        owned_directory(directory)
    regular_owned(profile / "moderation.toml")
    config = Config.from_file(profile / "moderation.toml")
    database = profile / "state/moderation.sqlite3"
    if profile.name != "liberdus-mod" or Path(config.storage.database_path) != database:
        raise ValueError("Expected the existing profile-local Liberdus moderation database")
    regular_owned(database)
    return config


def profile_key(profile):
    # Only this file; no default-profile lookup, interpolation or process-env fallback.
    from dotenv import dotenv_values
    path = profile / ".env"
    regular_owned(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("The profile .env must be private to its owner")
    key = dotenv_values(path, interpolate=False).get("TYPESAFE_API_KEY")
    if not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key or "${" in key:
        raise ProviderError("missing_key")
    return key


@contextmanager
def batch_lock(profile):
    path = profile / "state/jev-batch.lock"
    regular_owned(path, optional=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another batch is running") from None
        yield
    finally:
        os.close(fd)


class Ledger(Store):
    """Reuse only settings/transactions, never construct the live moderation engine."""

    def __init__(self, database, *, write=False):
        self.path = str(database)
        self.db = sqlite3.connect(database.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                  uri=True, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        if (self.db.execute("PRAGMA application_id").fetchone()[0] != self.APPLICATION_ID
                or self.db.execute("PRAGMA user_version").fetchone()[0] != self.SCHEMA_VERSION):
            self.close()
            raise ValueError("Expected the existing moderation database schema")

    def initialize_batch(self):
        with self.transaction():
            self.db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
                run_id TEXT NOT NULL, request_hash TEXT NOT NULL, suite TEXT NOT NULL,
                case_name TEXT NOT NULL, expected TEXT NOT NULL, policy_hash TEXT NOT NULL, model TEXT NOT NULL,
                rubric_hash TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
                outcome TEXT NOT NULL, result_json TEXT, latency_ms INTEGER,
                PRIMARY KEY(run_id, request_hash))""")
            # Only this batch table, while holding the batch lock; never touch the live worker's attempts.
            self.db.execute(f"UPDATE {TABLE} SET outcome='uncertain' WHERE outcome='running'")

    def row(self, run_id, digest):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return None
        return self.db.execute(
            f"SELECT run_id,request_hash,expected,policy_hash,model,rubric_hash,started_at,finished_at,outcome,latency_ms,"
            f"CASE WHEN length(result_json)<=8192 THEN result_json ELSE NULL END AS result_json "
            f"FROM {TABLE} WHERE run_id=? AND request_hash=?", (run_id, digest)).fetchone()

    def number(self, name, default=0):
        value = self.get_setting(name, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid shared budget accounting")
        return value

    def gate(self, config):
        if not config.ai_enabled or config.classifier.mode != "shadow" or config.actions_enabled:
            return "shadow_mode_required"
        if self.get_setting("policy_hash") != config.policy_hash:
            return "policy_changed"
        if self.get_setting("paused", False):
            return "moderation_paused"
        if self.get_setting("classifier_billing_guard", False):
            return "billing_guard"
        if self.get_setting("classifier_schema_version", 1) != 1:
            return "unsupported_classifier_schema"
        return None

    def reserve(self, run_id, case, digest, config, now):
        with self.transaction():
            if self.row(run_id, digest) is not None:
                return "cached"
            reason = self.gate(config)
            if reason:
                return reason
            if now < self.number("classifier_last_attempt_at") + config.classifier.min_interval_seconds:
                return "wait"
            day = int(now // 86400)
            prior_day = self.number("classifier_budget_day", day)
            if day < prior_day:
                return "clock_rollback"
            if self.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] >= MAX_RECORDS:
                return "history_full"
            daily = self.number("classifier_daily_calls") if day == prior_day else 0
            daily_reserved = self.number("classifier_daily_reserved_microusd") if day == prior_day else 0
            total = self.number("classifier_total_calls")
            total_reserved = self.number("classifier_total_reserved_microusd")
            settings = config.classifier
            if (daily >= settings.max_daily_calls or total >= settings.max_total_calls
                    or daily_reserved + RESERVED_MICROUSD > settings.daily_budget_microusd
                    or total_reserved + RESERVED_MICROUSD > settings.total_budget_microusd):
                return "budget_exhausted"
            self.db.execute(f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?,?,?,NULL,'running',NULL,NULL)",
                            (run_id, digest, SUITE, case.name, case.expected, config.policy_hash, MODEL, RUBRIC_HASH, now))
            for name, value in (
                ("classifier_budget_day", day), ("classifier_daily_calls", daily + 1),
                ("classifier_daily_reserved_microusd", daily_reserved + RESERVED_MICROUSD),
                ("classifier_total_calls", total + 1),
                ("classifier_total_reserved_microusd", total_reserved + RESERVED_MICROUSD),
                ("classifier_last_attempt_at", now),
            ):
                self.set_setting(name, value)
        return "reserved"

    def finish(self, run_id, digest, outcome, now, latency, result=None):
        with self.transaction():
            self.db.execute(f"UPDATE {TABLE} SET finished_at=?,outcome=?,result_json=?,latency_ms=? "
                            "WHERE run_id=? AND request_hash=? AND outcome='running'",
                            (now, outcome, encoded(result).decode() if result else None,
                             max(0, latency), run_id, digest))
            if outcome == "usage_exceeds_reservation":
                self.set_setting("classifier_billing_guard", True)

    def report(self, run_id, prepared, *, new_attempts=0, stopped=None):
        records = []
        for case, payload, digest in prepared:
            row = self.row(run_id, digest)
            item = {"case": case.name, "title": case.title, "expected": case.expected,
                    "request_hash": digest, "outcome": "not_run", "actual": None, "match": None}
            if row is not None:
                item.update(expected_at_evaluation=row["expected"], policy_hash=row["policy_hash"],
                            started_at=row["started_at"], finished_at=row["finished_at"])
                item["outcome"] = row["outcome"] if row["outcome"] in ERRORS | {"ok", "running"} else "unavailable"
                if type(row["latency_ms"]) is int and 0 <= row["latency_ms"] <= 86400000:
                    item["latency_ms"] = row["latency_ms"]
                if row["outcome"] in {"stale", "usage_exceeds_reservation"}:
                    try:
                        usage = json.loads(row["result_json"])
                        if all(type(usage[key]) is int and 0 <= usage[key] <= 1000000
                               for key in ("input_tokens", "output_tokens")):
                            item.update(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                                        estimated_microusd=(usage["input_tokens"] * 42 + 999) // 1000)
                    except (TypeError, ValueError, KeyError, RecursionError):
                        pass
                if row["outcome"] == "ok":
                    try:
                        result = json.loads(row["result_json"])
                        valid = validate_response({"model": result["model"], "answers": {"context": {
                            "type": "choice", "choice": result["choice"], "confidence": result["confidence"],
                            "probabilities": result["probabilities"]}}, "usage": {
                            "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"]}})
                        if row["model"] != MODEL or row["rubric_hash"] != RUBRIC_HASH:
                            raise ValueError
                        item.update(actual=valid["choice"], match=valid["choice"] == case.expected,
                                    confidence=valid["confidence"], input_tokens=valid["input_tokens"],
                                    output_tokens=valid["output_tokens"],
                                    estimated_microusd=(valid["input_tokens"] * 42 + 999) // 1000)
                    except (TypeError, ValueError, KeyError, RecursionError):
                        item["outcome"] = "unavailable"
            records.append(item)
        attempts = sum(record["outcome"] != "not_run" for record in records)
        return {"suite": SUITE, "run_id": run_id, "model": MODEL, "rubric_hash": RUBRIC_HASH,
                "records": records, "matched": sum(record["match"] is True for record in records),
                "successful": sum(record["outcome"] == "ok" for record in records),
                "attempted": attempts, "new_attempts": new_attempts, "stopped": stopped,
                "reserved_microusd": attempts * RESERVED_MICROUSD,
                "estimated_microusd": sum(record.get("estimated_microusd", 0) for record in records),
                "shared_total_calls": self.number("classifier_total_calls"),
                "shared_total_reserved_microusd": self.number("classifier_total_reserved_microusd")}


async def run_batch(ledger, config, prepared, evaluator, *, run_id="baseline", current=lambda: True,
                    clock=time.time, monotonic=time.monotonic, sleep=asyncio.sleep, progress=lambda text: None):
    run_name(run_id)
    ledger.initialize_batch()
    deadline = monotonic() + MAX_RUN_SECONDS
    attempts, stopped = 0, None
    for case, payload, digest in prepared:
        while True:
            if ledger.row(run_id, digest) is not None:
                progress(f"{case.name}: saved attempt reused")
                admission = "cached"
                break
            if monotonic() >= deadline:
                admission = "time_limit"
                break
            if not current():
                admission = "policy_changed"
                break
            admission = ledger.reserve(run_id, case, digest, config, clock())
            if admission != "wait":
                break
            await sleep(min(1.0, max(0.0, deadline - monotonic())))
        if admission == "cached":
            continue
        if admission != "reserved":
            stopped = admission
            break
        attempts += 1
        started = monotonic()
        result = None
        try:
            async def invoke():
                if not current() or ledger.gate(config):
                    raise ProviderError("stale")
                return await evaluator(payload)
            raw = await asyncio.wait_for(invoke(), config.classifier.timeout_seconds)
            result = validate_response(raw)
            if result["input_tokens"] > RESERVED_INPUT_TOKENS:
                outcome = "usage_exceeds_reservation"
                result = {key: result[key] for key in ("input_tokens", "output_tokens")}
            elif not current() or ledger.gate(config):
                outcome = "stale"
                result = {key: result[key] for key in ("input_tokens", "output_tokens")}
            else:
                outcome = "ok"
        except asyncio.CancelledError:
            ledger.finish(run_id, digest, "uncertain", clock(), int((monotonic() - started) * 1000))
            raise
        except TimeoutError:
            outcome = "timeout"
        except Exception as error:
            outcome = str(error) if isinstance(error, ProviderError) and str(error) in ERRORS else "provider_or_response_error"
        ledger.finish(run_id, digest, outcome, clock(), int((monotonic() - started) * 1000), result)
        progress(f"{case.name}: {outcome}")
        if outcome != "ok":
            stopped = outcome
            break
    return ledger.report(run_id, prepared, new_attempts=attempts, stopped=stopped)


def format_report(report):
    lines = ["JEV BATCH - " + report["run_id"], "-" * 32]
    for index, item in enumerate(report["records"], 1):
        verdict = ("MATCH" if item["match"] else "REVIEW") if item["outcome"] == "ok" else item["outcome"].upper()
        lines += [f"{index:02d} {item['title']} [{verdict}]",
                  f"  Want: {item['expected']}", f"  Got : {item['actual'] or '-'}"]
        if item["outcome"] == "ok":
            lines.append(f"  Score {item['confidence']:.2f} | {item.get('latency_ms', 0)} ms")
    lines += ["-" * 32, f"Matched: {report['matched']}/{len(report['records'])}",
              f"Valid responses: {report['successful']}", f"New attempts: {report['new_attempts']}",
              "Batch reserved: $" + f"{report['reserved_microusd'] / 1000000:.6f}",
              "Known token estimate: $" + f"{report['estimated_microusd'] / 1000000:.6f}",
              f"Shared total calls: {report['shared_total_calls']}"]
    if report["stopped"]:
        lines.append("Stopped: " + report["stopped"])
    lines += ["Synthetic text; real JEV API.", "No Discord events tested.",
              "Expected labels are hypotheses.", "Scores are not accuracy guarantees.",
              "Reservations are not invoices.", "Failures may have unknown charges."]
    return "\n".join(textwrap.fill(line, width=32, subsequent_indent="  ", break_on_hyphens=False) for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preview", "run", "results"), nargs="?", default="preview")
    parser.add_argument("--run-id", default="baseline", help="Same name reuses attempts; a new name buys fresh evaluations")
    parser.add_argument("--json", action="store_true", help="Print structured results")
    args = parser.parse_args()
    try:
        run_name(args.run_id)
        if args.operation == "preview":
            data = {"suite": SUITE, "run_id": args.run_id, "model": MODEL, "rubric_hash": RUBRIC_HASH,
                    "cases": [{"case": c.name, "expected": c.expected, "text": c.text} for c in CASES],
                    "maximum_attempts": len(CASES), "maximum_reserved_microusd": len(CASES) * RESERVED_MICROUSD,
                    "provider_called": False}
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0
        profile = Path.home() / ".hermes/profiles/liberdus-mod"
        config = load_policy(profile)
        prepared = requests(config.classifier if args.operation == "run" and config.ai_enabled else None)
        database = profile / "state/moderation.sqlite3"
        if args.operation == "results":
            with Ledger(database) as ledger:
                report = ledger.report(args.run_id, prepared)
        else:
            python = Path.home() / ".hermes/hermes-agent/venv/bin/python"
            if not python.is_file():
                raise ValueError("Expected the existing Hermes virtualenv")
            if Path(sys.prefix).resolve() != python.parent.parent.resolve():
                entry = [sys.argv[0]] if sys.argv[0].endswith(".pyz") else ["-m", "liberdus_moderator.jev_batch"]
                os.execv(str(python), [str(python), "-B", *entry, *sys.argv[1:]])
            with batch_lock(profile), Ledger(database, write=True) as ledger:
                reason = ledger.gate(config)
                if reason:
                    raise ValueError("Batch stopped: " + reason)
                key = None
                async def provider(payload):
                    nonlocal key
                    if key is None:
                        key = profile_key(profile)
                    return await evaluate(payload, key, config.classifier.timeout_seconds)
                def current():
                    try:
                        return load_policy(profile).policy_hash == config.policy_hash
                    except (OSError, ValueError):
                        return False
                report = asyncio.run(run_batch(
                    ledger, config, prepared, provider, run_id=args.run_id, current=current,
                    progress=lambda text: print(text, file=sys.stderr, flush=True)))
        print(json.dumps(report, indent=2) if args.json else format_report(report))
        if report["successful"] != len(CASES):
            return 2
        return 0 if report["matched"] == len(CASES) else 1
    except KeyboardInterrupt:
        print("Batch interrupted. Saved attempts are not retried. Use results to inspect them.", file=sys.stderr)
        return 130
    except (ValueError, ProviderError) as error:
        if isinstance(error, ProviderError):
            message = "The profile TypeSafe key is missing"
        else:
            message = str(error) if str(error).startswith(("Run name", "Expected", "Another batch", "Batch stopped", "The profile", "Synthetic")) else "Check the owned profile, policy and budget state"
        print("Batch stopped: " + message + ".", file=sys.stderr)
        return 2
    except Exception:
        print("Batch stopped. Check the owned profile and saved results; raw errors omitted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
