"""Read-only, bounded moderation statistics with explicit history/accounting scope."""

import json
import sqlite3

from .classifier import RESERVED_MICROUSD
from .display import panel
from .screening import validated_saved

MAX_COST_ROWS = 5000
MAX_RESULT_BYTES = 8192
MAX_COUNTER = 2**63 - 1


def _number(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNTER else None


def _counter(store, key):
    try:
        return _number(store.get_setting(key, 0))
    except (ValueError, TypeError, sqlite3.Error):
        return None


def _exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _counts(db, table, expressions):
    """All identifiers/expressions are fixed code; saved labels never reach output."""
    names = tuple(expressions)
    try:
        if not _exists(db, table):
            return {name: 0 for name in names}
        row = db.execute("SELECT " + ",".join(expressions.values()) + " FROM " + table).fetchone()
        return {name: _number(row[index] if row[index] is not None else 0) for index, name in enumerate(names)}
    except sqlite3.Error:
        return {name: None for name in names}


def _costs(db, table):
    """Only retained rows can be split by source; shared lifetime totals are separate.

    A result with validated usage yields a known token estimate even if its
    classification became stale. All other scanned attempts retain the original
    conservative reservation. Scan limits produce explicit lower bounds.
    """
    result = {"attempts": 0, "scanned": 0, "known_microusd": 0,
              "unknown_reserved_microusd": 0, "unknown_attempts": 0, "partial": False}
    try:
        if not _exists(db, table):
            return result
        result["attempts"] = db.execute("SELECT count(*) FROM " + table).fetchone()[0]
        rows = db.execute("SELECT CASE WHEN typeof(result_json)='text' "
            "AND length(CAST(result_json AS BLOB))<=? THEN result_json ELSE NULL END "
            "FROM " + table + " LIMIT ?", (MAX_RESULT_BYTES, MAX_COST_ROWS))
        for row in rows:
            result["scanned"] += 1
            try:
                saved = validated_saved(json.loads(row[0]))
                amount = (saved["input_tokens"] * 42 + 999) // 1000
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
                result["unknown_attempts"] += 1
                result["unknown_reserved_microusd"] += RESERVED_MICROUSD
            else:
                result["known_microusd"] += amount
        result["partial"] = result["scanned"] < result["attempts"]
        return result
    except sqlite3.Error:
        return {"attempts": None, "scanned": 0, "known_microusd": None,
                "unknown_reserved_microusd": None, "unknown_attempts": None, "partial": True}


def summary_snapshot(engine):
    """No pruning, optional schema initialization, AI calls or moderation writes."""
    store, db = engine.store, engine.store.db
    own_transaction = not db.in_transaction
    if own_transaction:
        db.execute("BEGIN")
    try:
        status = engine.status()
        data = {
            "state": {"mode": "paused" if status["paused"] is True else engine.config.mode,
                "deletion_enabled": status["deletion_enabled"] is True,
                "auto_delete_enabled": status["auto_delete_enabled"] is True,
                "timeout_enabled": status["timeout_enabled"] is True,
                "role_exemption_enabled": status["role_exemption_enabled"] is True},
            "live_lifetime_events": {"checked_versions": _counter(store, "screening_checked"),
                "unchecked_events": _counter(store, "screening_unchecked"),
                "role_exempt_events": _counter(store, "screening_exempt")},
            "incidents": _counts(db, "incidents", {"retained": "count(*)"}),
            "actions": _counts(db, "action_attempts_v1", {
                "auto_delete_done": "sum(kind='delete' AND outcome='done' AND automatic=1)",
                "staff_delete_done": "sum(kind='delete' AND outcome='done' AND automatic=0)",
                "delete_already_absent": "sum(kind='delete' AND outcome='already_absent')",
                "delete_uncertain": "sum(kind='delete' AND outcome='uncertain')",
                "delete_sending": "sum(kind='delete' AND outcome='sending')",
                "delete_denied": "sum(kind='delete' AND outcome='denied')",
                "timeout_done": "sum(kind='timeout' AND outcome='done')"}),
            "staff": _counts(db, "staff_assessments_v1", {"events": "count(*)",
                "needs_attention": "sum(label='needs_attention')", "looks_okay": "sum(label='looks_okay')",
                "unsure": "sum(label='unsure')", "dismissed": "sum(label='dismissed')"}),
            "live_retained": _costs(db, "screening_attempts_v1"),
            "evaluation_retained": _costs(db, "screening_eval_attempts_v1"),
            "shared_screening_lifetime": {"attempts": _counter(store, "screening_total_calls"),
                "used_reserved_microusd": _counter(store, "screening_total_reserved_microusd")},
            "scope": {"live_counters": "Recorded lifetime version/event counters; not unique messages.",
                "history": "Currently retained incidents, action outcomes and staff assessment events; older rows can be pruned.",
                "staff": "All retained assessment events, including repeated reviews and historical revisions.",
                "costs": "Screening only. Live/evaluation estimates use retained rows; shared lifetime accounting also retains pruned usage. Legacy shadow/batch costs excluded.",
                "limits": "Cost scans are bounded; partial estimates and unknown reservations are lower bounds, not invoices."},
        }
        return data
    finally:
        if own_transaction:
            db.rollback()  # End only our read snapshot; never commit or undo a caller's work.


def _count(value):
    value = _number(value)
    if value is None:
        return "?"
    return str(value) if value < 1000000 else "1000000+"


def _money(value, partial=False):
    value = _number(value)
    if value is None:
        return "?"
    if value > 999999999999:
        return ">$999999"
    return (">=" if partial else "") + "$" + str(value // 1000000) + "." + f"{value % 1000000:06d}"


def format_summary(data, connected=False):
    """Fixed labels and validated scalars only; no saved text, names or mentions."""
    state, events = data["state"], data["live_lifetime_events"]
    action, staff = data["actions"], data["staff"]
    live, evaluation = data["live_retained"], data["evaluation_retained"]
    shared = data["shared_screening_lifetime"]
    enabled = lambda flag: "ON" if flag is True else "OFF"
    mode = state["mode"] if state["mode"] in ("paused", "off", "report_only") else "unknown"
    lines = ["STATE", "--------------------------------", "Mode: " + mode,
        "Connected: " + ("Yes" if connected is True else "No"),
        "Deletion: " + enabled(state["deletion_enabled"]),
        "Auto-delete: " + enabled(state["auto_delete_enabled"]),
        "Timeout: " + enabled(state["timeout_enabled"]),
        "Role exemption: " + enabled(state["role_exemption_enabled"]), "",
        "LIVE JEV - LIFETIME COUNTERS", "--------------------------------",
        "Checked versions: " + _count(events["checked_versions"]),
        "Unchecked events: " + _count(events["unchecked_events"]),
        "Role-exempt events: " + _count(events["role_exempt_events"]),
        "Not unique messages; may overlap.", "",
        "RETAINED HISTORY", "--------------------------------",
        "Incidents: " + _count(data["incidents"]["retained"]),
        "Auto-delete done: " + _count(action["auto_delete_done"]),
        "Staff delete done: " + _count(action["staff_delete_done"]),
        "Already absent: " + _count(action["delete_already_absent"]),
        "Delete uncertain: " + _count(action["delete_uncertain"]),
        "Delete sending: " + _count(action["delete_sending"]),
        "Delete denied: " + _count(action["delete_denied"]),
        "Timeout done: " + _count(action["timeout_done"]),
        "Staff assessment events: " + _count(staff["events"]),
        "Needs attention: " + _count(staff["needs_attention"]),
        "Looks okay: " + _count(staff["looks_okay"]),
        "Unsure: " + _count(staff["unsure"]),
        "Dismissed: " + _count(staff["dismissed"]),
        "Includes old/repeated reviews.", "Pruned history is not counted.", "",
        "SCREENING COSTS", "--------------------------------",
        "Live retained attempts: " + _count(live["attempts"]),
        "Eval retained attempts: " + _count(evaluation["attempts"]),
        "Live known est: " + _money(live["known_microusd"], live["partial"]),
        "Eval known est: " + _money(evaluation["known_microusd"], evaluation["partial"]),
        "Live unknown reserve: " + _money(live["unknown_reserved_microusd"], live["partial"]),
        "Eval unknown reserve: " + _money(evaluation["unknown_reserved_microusd"], evaluation["partial"]),
        "Shared lifetime calls: " + _count(shared["attempts"]),
        "Shared lifetime used/reserved:", _money(shared["used_reserved_microusd"]),
        "Shared includes eval/pruned use.", "Legacy shadow/batch excluded.",
        "Estimates, not provider invoices.", "? means unavailable.",
        "Read only; no AI call or action."]
    return panel("Moderation summary", lines)
