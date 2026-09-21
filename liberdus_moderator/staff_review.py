"""Staff triage of saved evidence, independent of content labels and enforcement."""

import hashlib
import json
import re
import sqlite3

from .classifier import encoded
from .evidence_view import validated_evidence
from .models import validate_id, validate_timestamp

LABELS = {"needs_attention": "Needs attention", "looks_okay": "Looks okay", "unsure": "Unsure", "dismissed": "Dismissed"}
TABLE = "staff_assessments_v1"
SCHEMA_KEY = "staff_assessment_schema_version"
MAX_ASSESSMENTS = 32
PAGE_SIZE = 5


def digest(evidence):
    return hashlib.sha256(encoded(evidence)).hexdigest()


def snapshot(engine, incident, revision):
    row = engine.store.db.execute("SELECT evidence_json FROM incident_versions WHERE incident_id=? AND revision=?",
                                  (incident["id"], revision)).fetchone()
    if row is None:
        raise ValueError("Saved revision unavailable")
    evidence = json.loads(row[0])
    validated_evidence(engine.config, incident, evidence)
    return evidence


def _rows(store, identity):
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
        return []
    if store.get_setting(SCHEMA_KEY) != 1:
        raise ValueError("Assessment storage unavailable")
    return list(store.db.execute(f"SELECT * FROM {TABLE} WHERE incident_id=? ORDER BY sequence DESC LIMIT ?",
                                 (identity, MAX_ASSESSMENTS)))


def _view(engine, incident, row, target_digest):
    if (row["label"] not in LABELS or type(row["revision"]) is not int or not 1 <= row["revision"] <= 1000000000
            or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= MAX_ASSESSMENTS
            or not all(isinstance(row[key], str) and re.fullmatch(r"[a-f0-9]{64}", row[key])
                       for key in ("evidence_hash", "policy_hash"))):
        raise ValueError("Invalid saved assessment")
    validate_id(row["reviewer_id"], "reviewer_id")
    at = validate_timestamp(row["reviewed_at"], "reviewed_at")
    if digest(snapshot(engine, incident, row["revision"])) != row["evidence_hash"]:
        raise ValueError("Saved assessment evidence unavailable")
    applies = (row["evidence_hash"] == target_digest and row["policy_hash"] == engine.config.policy_hash
               and at <= engine._now())
    current = (applies and row["revision"] == incident["revision"]
               and row["evidence_hash"] == digest(incident["evidence"])
               and incident["status"] == "open" and incident["expires_at"] > engine._now())
    return {"state": "current" if current else "historical", "applies_to_snapshot": applies,
            "label": row["label"], "reviewer_id": row["reviewer_id"], "reviewed_at": at,
            "revision": row["revision"], "sequence": row["sequence"],
            "complete": applies and row["label"] in ("looks_okay", "dismissed")}


def saved_assessment(engine, incident, revision=None):
    """Read-only; a status-only restart does not reopen unchanged reviewed evidence."""
    try:
        evidence = incident["evidence"] if revision is None else snapshot(engine, incident, revision)
        validated_evidence(engine.config, incident, evidence)
        target = digest(evidence)
        rows = _rows(engine.store, incident["id"])
        if not rows:
            return {"state": "not_reviewed", "applies_to_snapshot": False, "complete": False}
        # An old-report click cannot replace an assessment of different, newer evidence.
        matching = next((row for row in rows if row["evidence_hash"] == target
                         and row["policy_hash"] == engine.config.policy_hash), rows[0])
        return _view(engine, incident, matching, target)
    except (sqlite3.Error, KeyError, IndexError, TypeError, ValueError, AttributeError, UnicodeError):
        return {"state": "unavailable", "applies_to_snapshot": False, "complete": False}


def record_assessment(engine, identity, revision, label, reviewer_id):
    """Called after command authorization; retain an append-only, bounded audit."""
    if not re.fullmatch(r"[a-f0-9]{32}", identity) or not re.fullmatch(r"[1-9][0-9]{0,8}", revision):
        raise ValueError("Invalid saved incident revision")
    label = label.replace("-", "_")
    if label not in LABELS:
        raise ValueError("Use needs-attention, looks-okay or unsure")
    validate_id(reviewer_id, "reviewer_id")
    store = engine.store
    with store.transaction():
        incident = store.incident(identity)
        if incident is None:
            raise ValueError("Incident unavailable")
        evidence_hash = digest(snapshot(engine, incident, int(revision)))
        rows = _rows(store, identity)
        if len(rows) >= MAX_ASSESSMENTS:
            raise ValueError("Assessment history full")
        store.db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
            incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL, revision INTEGER NOT NULL,
            evidence_hash TEXT NOT NULL, policy_hash TEXT NOT NULL,
            label TEXT NOT NULL, reviewer_id TEXT NOT NULL, reviewed_at REAL NOT NULL,
            PRIMARY KEY(incident_id,sequence))""")
        store.set_setting(SCHEMA_KEY, 1)
        sequence = rows[0]["sequence"] + 1 if rows else 1
        store.db.execute(f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?,?)",
                         (identity, sequence, int(revision), evidence_hash, engine.config.policy_hash,
                          label, reviewer_id, engine._now()))
        row = store.db.execute(f"SELECT * FROM {TABLE} WHERE incident_id=? AND sequence=?", (identity, sequence)).fetchone()
        view = _view(engine, incident, row, evidence_hash)
    return {"incident_id": identity, **view, "latest_complete": saved_assessment(engine, incident)["complete"]}


def pending_page(engine, page=1):
    if type(page) is not int or not 1 <= page <= 1000:
        raise ValueError("Invalid pending page")
    entries = []
    for incident in engine.store.incidents(engine.config.storage.max_incidents):
        if incident["updated_at"] < engine._now() - engine.config.storage.retention_seconds:
            continue
        try:
            validated_evidence(engine.config, incident)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue  # Never reveal out-of-scope evidence through a queue listing.
        assessment = saved_assessment(engine, incident)
        if assessment["complete"]:
            continue
        label = (LABELS[assessment["label"]] if assessment.get("applies_to_snapshot") else
                 "Review changed evidence" if assessment.get("label") else
                 "Review unavailable" if assessment["state"] == "unavailable" else "Not reviewed")
        entries.append({"id": incident["id"], "revision": incident["revision"], "label": label,
                        "updated_at": incident["updated_at"]})
    rank = {"Needs attention": 0, "Unsure": 1}
    entries.sort(key=lambda item: (rank.get(item["label"], 2), -item["updated_at"], item["id"]))
    pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        raise ValueError("Pending page unavailable")
    return {"total": len(entries), "page": page, "pages": pages,
            "items": entries[(page-1)*PAGE_SIZE:page*PAGE_SIZE]}


def format_pending(data):
    lines = ["**Pending staff reviews**", f"{data['total']} pending | Page {data['page']}/{data['pages']}", "```"]
    for item in data["items"]:
        lines += [item["label"], f"Revision {item['revision']}", item["id"], ""]
    if not data["items"]:
        lines.append("No pending staff reviews.")
    lines += ["```", "Open: `!mod incident ID`"]
    if data["page"] < data["pages"]:
        lines.append(f"Next: `!mod pending {data['page']+1}`")
    lines.append("Saved incidents only. No moderation action or new AI call.")
    return "\n".join(lines)
