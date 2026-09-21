"""Explicit human context labels, separate from rule/JEV decisions and enforcement."""

import hashlib
import re
import sqlite3

from .classifier import encoded
from .evidence_view import validated_evidence
from .models import validate_id, validate_timestamp

LABELS = {"promotion": "Promotion", "not_promotion": "Not promotion", "unsure": "Unsure"}
TABLE = "moderator_reviews_v1"
MAX_REVIEWS_PER_INCIDENT = 32


def _exists(store):
    return store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def _snapshot(engine, incident, revision):
    version = next((item for item in incident["history"] if item["revision"] == revision), None)
    if version is None:
        raise ValueError("review_revision_unavailable")
    validated_evidence(engine.config, incident, version["evidence"])
    return hashlib.sha256(encoded(version["evidence"])).hexdigest()


def saved_review(engine, incident):
    """Read-only latest human annotation, retaining the revision it actually covers."""
    store = engine.store
    try:
        if not _exists(store):
            return {"state": "not_reviewed"}
        if store.get_setting("moderator_review_schema_version") != 1:
            return {"state": "unavailable"}
        row = store.db.execute(f"SELECT * FROM {TABLE} WHERE incident_id=? ORDER BY sequence DESC LIMIT 1",
                               (incident["id"],)).fetchone()
        if row is None:
            return {"state": "not_reviewed"}
        if (row["label"] not in LABELS or type(row["revision"]) is not int or row["revision"] < 1
                or not all(isinstance(row[key], str) and re.fullmatch(r"[a-f0-9]{64}", row[key])
                           for key in ("evidence_hash", "policy_hash"))):
            raise ValueError
        validate_id(row["reviewer_id"], "reviewer_id")
        at = validate_timestamp(row["reviewed_at"], "reviewed_at")
        if _snapshot(engine, incident, row["revision"]) != row["evidence_hash"]:
            raise ValueError
        current = (row["revision"] == incident["revision"]
                   and row["evidence_hash"] == hashlib.sha256(encoded(incident["evidence"])).hexdigest()
                   and row["policy_hash"] == incident["policy_hash"] == engine.config.policy_hash
                   and incident["status"] == "open" and incident["expires_at"] > engine._now()
                   and at <= engine._now())
        return {"state": "current" if current else "historical", "label": row["label"],
                "reviewer_id": row["reviewer_id"], "reviewed_at": at, "revision": row["revision"],
                "sequence": row["sequence"]}
    except (sqlite3.Error, KeyError, ValueError, TypeError, AttributeError, UnicodeError):
        return {"state": "unavailable"}


def record_review(engine, identity, revision, label, reviewer_id):
    """Call only after command authorization; append bounded audit history atomically."""
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("incident_not_found")
    if not re.fullmatch(r"[1-9][0-9]{0,8}", revision):
        raise ValueError("review_requires_saved_revision")
    revision = int(revision)
    label = label.replace("-", "_")
    if label not in LABELS:
        raise ValueError("review_label_must_be_promotion_not_promotion_or_unsure")
    validate_id(reviewer_id, "reviewer_id")
    store = engine.store
    with store.transaction():
        incident = store.incident(identity)
        if incident is None:
            raise ValueError("incident_not_found")
        digest = _snapshot(engine, incident, revision)
        if _exists(store) and store.get_setting("moderator_review_schema_version") != 1:
            raise ValueError("review_storage_unavailable")
        store.db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
            incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL, revision INTEGER NOT NULL,
            evidence_hash TEXT NOT NULL, policy_hash TEXT NOT NULL,
            label TEXT NOT NULL, reviewer_id TEXT NOT NULL, reviewed_at REAL NOT NULL,
            PRIMARY KEY(incident_id, sequence))""")
        store.set_setting("moderator_review_schema_version", 1)
        count = store.db.execute(f"SELECT count(*) FROM {TABLE} WHERE incident_id=?", (identity,)).fetchone()[0]
        if count >= MAX_REVIEWS_PER_INCIDENT:
            raise ValueError("review_history_full")
        store.db.execute(f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?,?)",
                         (identity, count + 1, revision, digest, incident["policy_hash"],
                          label, reviewer_id, engine._now()))
    return {"incident_id": identity, **saved_review(engine, incident)}
