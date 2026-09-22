"""Read and render stored JEV evidence. No worker, secrets, provider I/O or writes."""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import sqlite3
import textwrap

from .classifier import MODEL, RUBRIC_HASH, encoded, validate_response


OUTCOMES = frozenset({
    "running", "ok", "uncertain", "stale", "stale_before_request", "timeout",
    "missing_key", "authentication_failed", "access_denied", "rate_limited",
    "provider_overloaded", "http_error", "response_too_large", "invalid_json",
    "provider_or_response_error", "usage_exceeds_reservation",
})
REASONS = {
    "matches": "matches current saved evidence",
    "policy_changed": "policy changed",
    "classifier_changed": "model or rubric changed",
    "clock_changed": "evaluation time is ahead of this clock",
    "disabled": "classification is off",
    "paused": "moderation paused",
    "incident_changed": "incident closed or window reset",
    "expired": "evidence window expired",
    "revision_changed": "incident revision changed",
    "displayed_revision": "evaluation covers a different snapshot",
    "evidence_changed": "evidence changed or unavailable",
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 253402300799


def _reason(engine, incident, attempt, now, *, expected_rubric=RUBRIC_HASH, mode="shadow"):
    config, store = engine.config, engine.store
    if attempt["policy_hash"] != config.policy_hash or incident["policy_hash"] != config.policy_hash:
        return "policy_changed"
    if attempt["model"] != MODEL or attempt["rubric_hash"] != expected_rubric:
        return "classifier_changed"
    if (attempt["finished_at"] or attempt["started_at"]) > now:
        return "clock_changed"
    if not config.ai_enabled or config.classifier.mode != mode or config.mode != "report_only":
        return "disabled"
    if store.get_setting("paused", False):
        return "paused"
    if incident["status"] != "open":
        return "incident_changed"
    if incident["expires_at"] <= now:
        return "expired"
    if attempt["revision"] != incident["revision"]:
        return "revision_changed"
    evidence = incident["evidence"]
    if not evidence or hashlib.sha256(encoded(evidence)).hexdigest() != attempt["evidence_hash"]:
        return "evidence_changed"
    for item in evidence:
        message = store.db.execute("SELECT version, eligible FROM messages WHERE message_id=?",
                                   (item["message_id"],)).fetchone()
        if (not message or message["version"] != item["version"] or not message["eligible"]
                or item["guild_id"] != config.guild_id
                or item["channel_id"] not in config.monitored_channel_ids
                or item["author_id"] != incident["author_id"] or item["author_id"] == config.bot_user_id
                or any(item.get(flag) for flag in ("is_bot", "is_webhook", "is_thread", "has_attachments"))):
            return "evidence_changed"
    return "matches"


def saved_classification(engine, incident):
    """Read one saved attempt after command authorization; never initialize a worker.

    Expiration is checked without pruning/mutating the incident. A result may still
    be displayed as history after an edit, coverage reset, policy change or disable.
    Only validated fixed fields reach the command response, never provider text.
    """
    if incident["rule_id"] == "jev_message":
        from .screening import saved_screening
        return saved_screening(engine, incident)
    store = engine.store
    try:
        if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='classifier_attempts'").fetchone():
            return {"outcome": "not_evaluated"}
        if store.get_setting("classifier_schema_version", 1) != 1:
            return {"outcome": "unavailable"}
        row = store.db.execute(
            "SELECT revision,evidence_hash,policy_hash,rubric_hash,model,started_at,finished_at,outcome,latency_ms,"
            "CASE WHEN length(result_json)<=8192 THEN result_json ELSE NULL END AS result_json "
            "FROM classifier_attempts WHERE incident_id=?", (incident["id"],)).fetchone()
        if row is None:
            return {"outcome": "not_evaluated"}
        attempt = dict(row)
        if (attempt["outcome"] not in OUTCOMES
                or type(attempt["revision"]) is not int or not 1 <= attempt["revision"] <= 1000000000
                or not isinstance(attempt["model"], str)
                or not re.fullmatch(r"jev-[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", attempt["model"])
                or not all(isinstance(attempt[key], str) and re.fullmatch(r"[a-f0-9]{64}", attempt[key])
                           for key in ("evidence_hash", "policy_hash", "rubric_hash"))
                or not _number(attempt["started_at"])
                or (attempt["finished_at"] is not None and (not _number(attempt["finished_at"])
                    or attempt["finished_at"] < attempt["started_at"]))
                or (attempt["outcome"] == "ok" and attempt["finished_at"] is None)
                or (attempt["latency_ms"] is not None and (type(attempt["latency_ms"]) is not int
                    or not 0 <= attempt["latency_ms"] <= 86400000))):
            return {"outcome": "unavailable"}
        now = engine._now()
        at = attempt["finished_at"] or attempt["started_at"]
        reason = _reason(engine, incident, attempt, now)
        view = {"outcome": attempt["outcome"], "revision": attempt["revision"], "model": attempt["model"],
                "age_seconds": int(now - at) if now >= at else None,
                "latency_ms": attempt["latency_ms"],
                "evidence_state": "current" if reason == "matches" else "historical", "reason": reason}
        if attempt["outcome"] == "ok":
            result = json.loads(attempt["result_json"])
            valid = validate_response({"model": result["model"], "answers": {"context": {
                "type": "choice", "choice": result["choice"], "confidence": result["confidence"],
                "probabilities": result["probabilities"]}}, "usage": {
                "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"]}},
                expected_model=attempt["model"])
            view.update(choice=valid["choice"], confidence=valid["confidence"])
        return view
    except (sqlite3.Error, KeyError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        # Corrupt/unsupported historical records must not break private commands
        # or expose raw SQL/provider errors. Read-only fallback, no repair/retry.
        return {"outcome": "unavailable"}


def _age(seconds):
    if seconds is None:
        return "unknown"
    for unit, divisor in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= divisor:
            return f"{min(seconds // divisor, 99999)}{unit} ago"
    return f"{seconds}s ago"


PANEL_WIDTH = 32


def _field(label, value):
    return textwrap.fill(str(value), width=PANEL_WIDTH, initial_indent=f"{label:<10}: ",
                         subsequent_indent=" " * 12, break_on_hyphens=False)


def format_classification(view):
    """Plain ASCII rows for a narrow Discord code block; inputs are validated."""
    lines = ["JEV - SAVED RESULT", "-" * PANEL_WIDTH]
    if view["outcome"] in {"not_evaluated", "unavailable"}:
        lines.append(_field("Result", "No saved evaluation" if view["outcome"] == "not_evaluated"
                            else "Saved evaluation unavailable"))
        return "\n".join(lines)
    if view["outcome"] == "ok":
        lines += [_field("Label", view["choice"].replace("_", " ").capitalize()),
                  _field("Confidence", f"{view['confidence']:.2f} (model score)")]
    lines += [_field("Outcome", "OK" if view["outcome"] == "ok" else view["outcome"].replace("_", " ").capitalize()),
              _field("Model", view["model"]), _field("Eval rev", view["revision"]),
              _field("Age", _age(view["age_seconds"])),
              _field("Evidence", view["evidence_state"].upper()),
              _field("Reason", REASONS[view["reason"]].capitalize())]
    return "\n".join(lines)


def format_moderator_review(view):
    from .moderator_review import LABELS
    lines = ["MODERATOR REVIEW", "-" * PANEL_WIDTH]
    if view["state"] in {"not_reviewed", "unavailable"}:
        lines.append(_field("Result", "Not reviewed" if view["state"] == "not_reviewed" else "Saved review unavailable"))
    else:
        lines += [_field("Label", LABELS[view["label"]]), _field("Review rev", view["revision"]),
                  _field("Evidence", view["state"].upper()),
                  _field("Reviewed", datetime.fromtimestamp(view["reviewed_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))]
    return "\n".join(lines)


def incident_view(engine, incident, revision=None):
    from .evidence_view import saved_evidence
    from .moderator_review import saved_review
    from .staff_review import saved_assessment, snapshot
    evidence = incident["evidence"] if revision is None else snapshot(engine, incident, revision)
    from .actions import history
    view = {**incident, "public_deletion_allowed": engine.config.allow_public_deletion, "actions_enabled": engine.config.actions_enabled, "action_history": history(engine, incident["id"]), "revision": incident["revision"] if revision is None else revision,
            "latest_revision": incident["revision"], "evidence": evidence,
            "classification": saved_classification(engine, incident),
            "moderator_review": saved_review(engine, incident),
            "staff_assessment": saved_assessment(engine, incident, revision)}
    if (view["classification"].get("evidence_state") == "current"
            and view["classification"].get("revision") != view["revision"]):
        view["classification"] = {**view["classification"], "evidence_state": "historical", "reason": "displayed_revision"}
    view["evidence_view"] = saved_evidence(engine, view)
    return view


def format_incident(incident, *, details=False):
    from .review_display import format_review
    return format_review(incident, details=details)
