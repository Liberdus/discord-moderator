"""Synthetic JEV screening evaluation; real provider, no Discord connection/actions."""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import textwrap
import time

from . import actions
from .classifier import MODEL, RESERVED_INPUT_TOKENS, RESERVED_MICROUSD, encoded
from .config import Config, ClassifierSettings, StorageSettings
from .engine import Engine
from .jev import ProviderError, evaluate
from .jev_batch import ERRORS, batch_lock, load_policy, profile_key, run_name
from .models import MessageEvent
from .screening import MessageScreener, QUESTIONS, SCREENING_HASH, validate_screening, validated_saved
from .screening_cases import CASES, SUITE, SUITE_HASH
from .screening_eval_ledger import ScreeningEvalLedger
from .storage import Store

MAX_RUN_SECONDS = 600
DECISION_VERSION = "moderator-0.5.4"


@contextmanager
def simulation(case, settings=None):
    """Production payload and decision code with disposable synthetic state only.

    Never starts a worker task or constructs a Discord adapter/action executor.
    All scope/role/flag/freshness prerequisites are deliberately satisfied here.
    """
    settings = settings or ClassifierSettings(mode="report_only", max_daily_calls=10000,
        max_total_calls=100000, daily_budget_microusd=1000000, total_budget_microusd=4000000)
    settings = replace(settings, mode="report_only", exempt_role_ids=())
    config = Config("1", "99", ("10",), ("20",), ("98",), schema_version=2,
        ai_enabled=True, actions_enabled=True, classifier=settings,
        storage=StorageSettings(database_path=":memory:"))
    with Store(":memory:") as store:
        engine = Engine(config, store, clock=lambda: 1000.0)
        actions.initialize(engine)
        store.set_setting("deletion_enabled", True)
        store.set_setting("auto_delete_enabled", True)
        engine.process(MessageEvent("1", "10", "100", "50", case.text, 1000., author_role_ids=("1",)))
        worker = MessageScreener(engine, None)
        job = worker.snapshot("100")
        if job is None:
            raise ValueError("Synthetic request exceeds screening limits")
        yield engine, worker, job


def requests(settings=None):
    result = []
    for case in CASES:
        with simulation(case, settings) as (_, _, job):
            result.append((case, job.payload, hashlib.sha256(job.payload).hexdigest()))
    if len({digest for _, _, digest in result}) != len(result):
        raise ValueError("Synthetic requests must be unique")
    return result


def decision(case, result):
    """Would the validated answer report/qualify, assuming other action gates pass?"""
    result = validated_saved(result)
    if result["input_tokens"] > RESERVED_INPUT_TOKENS:
        raise ValueError("Usage exceeds reservation")
    with simulation(case) as (engine, worker, job):
        if not worker.reserve(job):
            raise ValueError("Synthetic screening reservation failed")
        worker.finish(job, "awaiting_apply", time.monotonic(), result)
        identity = worker.apply(job)
        row = worker.store.db.execute("SELECT outcome FROM screening_attempts_v1 WHERE key=?", (job.key,)).fetchone()
        if row is None or row["outcome"] != "ok":
            raise ValueError("Synthetic decision could not be evaluated")
        qualifies = bool(identity and actions.automatic_candidate(engine, worker.store.incident(identity)))
        # A report outbox exists only in this disposable database; nothing delivers it.
        return {"would_report": bool(identity), "would_qualify_for_auto_delete": qualifies}


def report(ledger, run_id, prepared, *, new_attempts=0, stopped=None):
    records = []
    for case, _, digest in prepared:
        item = {"case": case.name, "title": case.title, "text": case.text, "safety": case.safety,
            "expected_purposes": list(case.expected_purposes), "expected_concerns": list(case.expected_concerns),
            "fixture_hash": case.fixture_hash, "request_hash": digest, "outcome": "not_run", "match": None,
            "would_report": None, "would_qualify_for_auto_delete": None}
        row = ledger.row(run_id, digest)
        if row is not None:
            item.update(policy_hash=row["policy_hash"], started_at=row["started_at"], finished_at=row["finished_at"])
            item["outcome"] = row["outcome"] if row["outcome"] in ERRORS | {"ok", "running"} else "unavailable"
            if (row["model"] != MODEL or row["rubric_hash"] != SCREENING_HASH or row["suite"] != SUITE
                    or row["case_name"] != case.name or row["fixture_hash"] != case.fixture_hash):
                item["outcome"] = "unavailable"
            if type(row["latency_ms"]) is int and 0 <= row["latency_ms"] <= 86400000:
                item["latency_ms"] = row["latency_ms"]
            if item["outcome"] in {"ok", "stale", "usage_exceeds_reservation"}:
                try:
                    result = validated_saved(json.loads(row["result_json"]))
                    amount = (result["input_tokens"] * 42 + 999) // 1000
                    item.update(estimated_microusd=amount, input_tokens=result["input_tokens"], output_tokens=result["output_tokens"])
                    if item["outcome"] == "ok":
                        if result["input_tokens"] > RESERVED_INPUT_TOKENS:
                            raise ValueError("Usage exceeds reservation")
                        item.update(actual_purpose=result["choice"], actual_concern=result["concern"],
                            purpose_score=result["confidence"], concern_score=result["concern_confidence"],
                            result=result,
                            purpose_match=result["choice"] in case.expected_purposes,
                            concern_match=result["concern"] in case.expected_concerns,
                            **decision(case, result))
                        item["match"] = item["purpose_match"] and item["concern_match"]
                except (ValueError, TypeError, KeyError, RecursionError):
                    item.update(outcome="unavailable", match=None, would_report=None, would_qualify_for_auto_delete=None)
        records.append(item)
    valid = [item for item in records if item["outcome"] == "ok"]
    groups = {}
    for safety in ("benign", "harmful", "ambiguous"):
        subset = [item for item in valid if item["safety"] == safety]
        groups[safety] = {"total": sum(item["safety"] == safety for item in records), "evaluated": len(subset),
            "would_report": sum(item["would_report"] for item in subset),
            "would_qualify_for_auto_delete": sum(item["would_qualify_for_auto_delete"] for item in subset)}
    false_delete = [item["case"] for item in valid if item["safety"] == "benign" and item["would_qualify_for_auto_delete"]]
    false_report = [item["case"] for item in valid if item["safety"] == "benign" and item["would_report"]]
    missed = [item["case"] for item in valid if item["safety"] == "harmful" and not item["would_report"]]
    attempted = sum(item["outcome"] != "not_run" for item in records)
    unknown_cost = sum(item["outcome"] != "not_run" and "estimated_microusd" not in item for item in records)
    return {"suite": SUITE, "suite_hash": SUITE_HASH, "run_id": run_id, "model": MODEL,
        "rubric_hash": SCREENING_HASH, "decision_version": DECISION_VERSION, "records": records,
        "matched": sum(item["match"] is True for item in valid),
        "purpose_matched": sum(item["purpose_match"] for item in valid),
        "concern_matched": sum(item["concern_match"] for item in valid), "successful": len(valid),
        "attempted": attempted, "new_attempts": new_attempts, "stopped": stopped, "groups": groups,
        "benign_auto_delete_cases": false_delete, "benign_report_cases": false_report,
        "harmful_unreported_cases": missed,
        "reserved_microusd": attempted * RESERVED_MICROUSD,
        "estimated_microusd": sum(item.get("estimated_microusd", 0) for item in records),
        "unknown_cost_attempts": unknown_cost,
        "shared_total_calls": ledger.number("screening_total_calls"),
        "shared_total_used_reserved_microusd": ledger.number("screening_total_reserved_microusd"),
        "note": "Synthetic hypotheses, not measured server accuracy. Qualification assumes other gates pass; no Discord actions."}


async def run_evaluation(ledger, config, prepared, evaluator, *, run_id=SUITE, current=lambda: True,
        clock=time.time, monotonic=time.monotonic, sleep=asyncio.sleep, progress=lambda text: None):
    run_name(run_id)
    expected = {case.name: (case, payload, digest) for case, payload, digest in requests(config.classifier)}
    if len({case.name for case, _, _ in prepared}) != len(prepared) or any(expected.get(item[0].name) != item for item in prepared):
        raise ValueError("Expected the fixed screening suite requests")
    ledger.initialize()
    deadline = monotonic() + MAX_RUN_SECONDS
    attempts, stopped = 0, None
    for case, payload, digest in prepared:
        while True:
            if ledger.row(run_id, digest) is not None:
                admission = "cached"
                progress(case.name + ": saved attempt reused")
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
        started, result = monotonic(), None
        try:
            async def invoke():
                if not current() or ledger.gate(config):
                    raise ProviderError("stale")
                return await evaluator(payload)
            raw = await asyncio.wait_for(invoke(), config.classifier.timeout_seconds)
            result = validate_screening(raw)
            if result["input_tokens"] > RESERVED_INPUT_TOKENS:
                outcome = "usage_exceeds_reservation"
            elif not current() or ledger.gate(config):
                outcome = "stale"
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
        progress(case.name + ": " + outcome)
        if outcome != "ok":
            stopped = outcome
            break
    return report(ledger, run_id, prepared, new_attempts=attempts, stopped=stopped)


def format_report(data):
    lines = ["JEV SCREENING EVALUATION", "Run: " + data["run_id"], "-" * 32]
    benign, harmful, ambiguous = (data["groups"][name] for name in ("benign", "harmful", "ambiguous"))
    lines += [f"Valid: {data['successful']}/{len(data['records'])}", f"Label matches: {data['matched']}/{data['successful']}",
        f"Purpose matches: {data['purpose_matched']}/{data['successful']}",
        f"Concern matches: {data['concern_matched']}/{data['successful']}",
        f"Benign evaluated: {benign['evaluated']}/{benign['total']}",
        f"Benign auto-delete candidates: {len(data['benign_auto_delete_cases'])}",
        f"Benign staff reports: {len(data['benign_report_cases'])}",
        f"Harmful evaluated: {harmful['evaluated']}/{harmful['total']}",
        f"Harmful without report: {len(data['harmful_unreported_cases'])}",
        f"Ambiguous evaluated: {ambiguous['evaluated']}/{ambiguous['total']}",
        f"Ambiguous auto-delete candidates: {ambiguous['would_qualify_for_auto_delete']}", "-" * 32]
    for index, item in enumerate(data["records"], 1):
        # Keep terminal output short; JSON retains every case, score and distribution.
        risk = ((item["safety"] == "benign" and item["would_report"])
                or (item["safety"] == "harmful" and item["would_report"] is False)
                or (item["safety"] == "ambiguous" and item["would_qualify_for_auto_delete"]))
        if item["outcome"] == "not_run" or (item["match"] is True and not risk):
            continue
        verdict = ("MATCH" if item["match"] else "REVIEW") if item["outcome"] == "ok" else item["outcome"].upper()
        if item["safety"] == "benign" and item["would_qualify_for_auto_delete"]:
            verdict = "FALSE DELETE RISK"
        lines += [f"{index:02d} {item['title']}", f"  {item['safety']} | {verdict}",
            "  Want concern: " + "/".join(item["expected_concerns"]),
            "  Want purpose: " + "/".join(item["expected_purposes"])]
        if item["outcome"] == "ok":
            route = "AUTO-DELETE CANDIDATE" if item["would_qualify_for_auto_delete"] else "STAFF REPORT" if item["would_report"] else "NO REPORT"
            lines += ["  Got concern: " + item["actual_concern"], "  Got purpose: " + item["actual_purpose"],
                f"  Concern score: {item['concern_score']:.2f}", f"  {route} | {item.get('latency_ms', 0)} ms"]
    lines += ["-" * 32, f"New attempts: {data['new_attempts']}",
        f"Initial reservations: ${data['reserved_microusd'] / 1000000:.6f}",
        f"Known token estimate: ${data['estimated_microusd'] / 1000000:.6f}",
        f"Unknown-cost attempts: {data['unknown_cost_attempts']}",
        f"Shared screening calls: {data['shared_total_calls']}"]
    if data["stopped"]:
        lines.append("Stopped: " + data["stopped"])
    lines += ["Details: results --json", "Synthetic text; real JEV API.", "No Discord messages or actions.",
        "Candidates assume other gates pass.", "Expected labels are hypotheses.",
        "Not server accuracy or proof of safety.", "Missing results are not passes.",
        "Scores are not accuracy guarantees.", "Estimates are not invoices."]
    return "\n".join(textwrap.fill(line, width=32, subsequent_indent="  ", break_on_hyphens=False) for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preview", "run", "results"), nargs="?", default="preview")
    parser.add_argument("--run-id", default=SUITE, help="Reuse to resume; a new name buys fresh evaluations")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        args.run_id = run_name(args.run_id)
        if args.operation == "preview":
            prepared = requests()
            print(json.dumps({"suite": SUITE, "suite_hash": SUITE_HASH, "model": MODEL,
                "rubric_hash": SCREENING_HASH, "decision_version": DECISION_VERSION, "questions": QUESTIONS,
                "cases": [asdict(case) for case in CASES], "maximum_attempts": len(CASES),
                "maximum_initial_reservation_microusd": len(CASES) * RESERVED_MICROUSD,
                "maximum_payload_bytes": max(len(payload) for _, payload, _ in prepared),
                "provider_called": False}, indent=2, ensure_ascii=False))
            return 0
        profile = Path.home() / ".hermes/profiles/liberdus-mod"
        config = load_policy(profile)
        database = profile / "state/moderation.sqlite3"
        prepared = requests(config.classifier if args.operation == "run" else None)
        if args.operation == "results":
            with ScreeningEvalLedger(database) as ledger:
                data = report(ledger, args.run_id, prepared)
        else:
            python = Path.home() / ".hermes/hermes-agent/venv/bin/python"
            if not python.is_file():
                raise ValueError("Expected the existing Hermes virtualenv")
            if Path(sys.prefix).resolve() != python.parent.parent.resolve():
                entry = [sys.argv[0]] if sys.argv[0].endswith(".pyz") else ["-m", "liberdus_moderator.screening_eval"]
                os.execv(str(python), [str(python), "-B", *entry, *sys.argv[1:]])
            with batch_lock(profile), ScreeningEvalLedger(database, write=True) as ledger:
                reason = ledger.gate(config)
                if reason:
                    raise ValueError("Evaluation stopped: " + reason)
                # Validate the scoped key before reserving; never print it or construct a Discord client.
                key = profile_key(profile)
                async def provider(payload):
                    return await evaluate(payload, key, config.classifier.timeout_seconds)
                def current():
                    try:
                        return load_policy(profile).policy_hash == config.policy_hash
                    except (OSError, ValueError):
                        return False
                data = asyncio.run(run_evaluation(ledger, config, prepared, provider, run_id=args.run_id,
                    current=current, progress=lambda line: print(line, file=sys.stderr, flush=True)))
        print(json.dumps(data, indent=2, ensure_ascii=False) if args.json else format_report(data))
        if data["successful"] != len(CASES):
            return 2
        return 0 if data["matched"] == len(CASES) and not data["benign_auto_delete_cases"] else 1
    except KeyboardInterrupt:
        print("Interrupted. Saved attempts will not be retried; use results or resume the same run.", file=sys.stderr)
        return 130
    except (ValueError, ProviderError) as error:
        allowed = ("Run name", "Expected", "Another batch", "Evaluation stopped", "The profile", "Synthetic")
        message = "The profile TypeSafe key is missing" if isinstance(error, ProviderError) else (
            str(error) if str(error).startswith(allowed) else "Check the owned profile, policy and budget state")
        print("Evaluation stopped: " + message + ".", file=sys.stderr)
        return 2
    except Exception:
        print("Evaluation stopped. Check owned profile and saved results; raw errors omitted.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
