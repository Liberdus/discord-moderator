"""Durable evidence and private-report outbox; never performs network calls."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3


class CapacityError(RuntimeError):
    """Bounded state is full; callers must expose incomplete coverage."""


class Store:
    SCHEMA_VERSION = 1
    APPLICATION_ID = 0x4C444D31

    def __init__(self, database_path: str):
        self.path = str(database_path)
        if self.path != ":memory:":
            path = Path(self.path)
            if path.is_symlink():
                raise ValueError("Database path must not be a symlink")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            path.chmod(0o600)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        application = self.db.execute("PRAGMA application_id").fetchone()[0]
        tables = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if version not in (0, self.SCHEMA_VERSION):
            raise ValueError(f"Unsupported database schema: {version}")
        if application not in (0, self.APPLICATION_ID) or (tables and application == 0):
            raise ValueError("Database is not a Liberdus moderation database")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        if version == 0:
            self.db.executescript(f"""
                BEGIN IMMEDIATE;
                CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE messages(
                    message_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
                    author_id TEXT NOT NULL, created_at REAL NOT NULL,
                    modified_at REAL NOT NULL, version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, eligible INTEGER NOT NULL,
                    event_json TEXT NOT NULL
                );
                CREATE INDEX messages_author_time ON messages(author_id, created_at);
                CREATE TABLE incidents(
                    id TEXT PRIMARY KEY, group_key TEXT NOT NULL,
                    rule_id TEXT NOT NULL, author_id TEXT NOT NULL,
                    reason TEXT NOT NULL, status TEXT NOT NULL,
                    revision INTEGER NOT NULL, first_seen REAL NOT NULL,
                    updated_at REAL NOT NULL, expires_at REAL NOT NULL,
                    last_notified_at REAL, policy_version TEXT NOT NULL,
                    policy_hash TEXT NOT NULL, evidence_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX incidents_open_group ON incidents(group_key)
                    WHERE status='open';
                CREATE INDEX incidents_author ON incidents(author_id, status);
                CREATE TABLE incident_versions(
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, recorded_at REAL NOT NULL,
                    status TEXT NOT NULL, evidence_json TEXT NOT NULL,
                    PRIMARY KEY(incident_id, revision)
                );
                CREATE TABLE reports(
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    incident_revision INTEGER NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX reports_pending ON reports(incident_id, kind)
                    WHERE status='pending';
                PRAGMA application_id={self.APPLICATION_ID};
                PRAGMA user_version={self.SCHEMA_VERSION};
                COMMIT;
            """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.rollback()
            raise
        else:
            self.db.commit()

    def get_setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        self.db.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    @staticmethod
    def incident_record(row):
        record = dict(row)
        record["evidence"] = json.loads(record.pop("evidence_json"))
        return record

    def incident(self, incident_id: str):
        row = self.db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if row is None:
            return None
        record = self.incident_record(row)
        record["history"] = [
            {**dict(version), "evidence": json.loads(version["evidence_json"])}
            for version in self.db.execute(
                "SELECT revision, recorded_at, status, evidence_json FROM incident_versions "
                "WHERE incident_id=? ORDER BY revision", (incident_id,)
            )
        ]
        for version in record["history"]:
            del version["evidence_json"]
        return record

    def incidents(self, limit=100):
        return [self.incident_record(row) for row in self.db.execute(
            "SELECT * FROM incidents ORDER BY updated_at DESC, id LIMIT ?", (limit,)
        )]

    def reports(self, limit=100):
        result = []
        for row in self.db.execute(
            "SELECT * FROM reports WHERE status='pending' ORDER BY created_at, id LIMIT ?", (limit,)
        ):
            record = dict(row)
            record["payload"] = json.loads(record.pop("payload_json"))
            result.append(record)
        return result

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
