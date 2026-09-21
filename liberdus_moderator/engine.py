"""Serialized, restart-safe code rules. No AI client or action executor exists here."""

import json
import math
import time
from uuid import uuid4

from .config import Config
from .models import MessageEvent
from .rules import content_fingerprint, find_matches
from .storage import CapacityError, Store


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Engine:
    def __init__(self, config: Config, store: Store, clock=time.time):
        self.config, self.store, self.clock = config, store, clock
        with store.transaction():
            bound_guild = store.get_setting("guild_id")
            if bound_guild is not None and bound_guild != config.guild_id:
                raise ValueError("Database belongs to a different Discord guild")
            store.set_setting("guild_id", config.guild_id)
            previous = store.get_setting("policy_hash")
            if previous and previous != config.policy_hash:
                self._invalidate_window(store.get_setting("last_processed_at", 0), "policy_changed")
            store.set_setting("policy_hash", config.policy_hash)
            if store.get_setting("paused") is None:
                store.set_setting("paused", False)
            if store.get_setting("logs_enabled") is None:
                store.set_setting("logs_enabled", config.logs_enabled)

    def _now(self):
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Clock must return a positive finite Unix timestamp")
        return float(value)

    def status(self):
        return {
            "mode": self.config.mode,
            "paused": self.store.get_setting("paused", False),
            "logs_enabled": self.store.get_setting("logs_enabled", False),
            "coverage": "code_only",
            "live_discord_connected": False,
            "ai_enabled": self.config.ai_enabled, "actions_enabled": False,
            "classifier_mode": self.config.classifier.mode,
            "classifier_state": self.store.get_setting("classifier_state", "not_started") if self.config.ai_enabled else "off",
            "ai_attempts": self.store.get_setting("classifier_total_calls", 0),
            "ai_reserved_microusd": self.store.get_setting("classifier_total_reserved_microusd", 0),
            "message_count": self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0],
            "incident_count": self.store.db.execute("SELECT count(*) FROM incidents").fetchone()[0],
            "pending_reports": self.store.db.execute("SELECT count(*) FROM reports WHERE status='pending'").fetchone()[0],
            "capacity_errors": self.store.get_setting("capacity_errors", 0),
            "dropped_reports": self.store.get_setting("dropped_reports", 0),
            "monitored_channel_ids": list(self.config.monitored_channel_ids),
            "excluded": ["threads", "attachments", "other_bots", "webhooks", "unmonitored_channels"],
        }

    def _decision(self, event, disposition, reason, incident_ids=(), new_incident_ids=()):
        return {
            "message_id": event.message_id, "evidence_version": event.version,
            "disposition": disposition, "reason": reason,
            "incident_ids": list(incident_ids), "new_incident_ids": list(new_incident_ids),
            "coverage": "code_only", "ai_calls": 0, "public_actions": [],
            "reporting_degraded": self.store.get_setting("dropped_reports", 0) > 0,
        }

    def process(self, event: MessageEvent):
        if not isinstance(event, MessageEvent):
            raise TypeError("process expects a validated MessageEvent")
        if event.guild_id != self.config.guild_id or event.channel_id not in self.config.monitored_channel_ids:
            return self._decision(event, "ignored", "outside_monitored_scope")
        if event.author_id == self.config.bot_user_id:
            return self._decision(event, "ignored", "own_output")
        if event.is_bot or event.is_webhook:
            return self._decision(event, "ignored", "bot_or_webhook_excluded")
        now = self._now()
        if event.modified_at > now + 5:
            return self._decision(event, "ignored", "future_timestamp")
        if event.created_at < now - self.config.storage.retention_seconds:
            return self._decision(event, "ignored", "outside_retention")
        try:
            with self.store.transaction():
                if self.store.get_setting("policy_hash") != self.config.policy_hash:
                    return self._decision(event, "ignored", "configuration_changed_reload_required")
                if self.config.mode == "off" or self.store.get_setting("paused", False):
                    return self._decision(event, "ignored", "moderation_paused")
                if event.created_at < self.store.get_setting("coverage_started_at", 0):
                    return self._decision(event, "ignored", "before_current_coverage_window")
                now = max(now, self.store.get_setting("last_processed_at", 0))
                self.store.set_setting("last_processed_at", now)
                self._prune(now)
                old = self.store.db.execute(
                    "SELECT * FROM messages WHERE message_id=?", (event.message_id,)
                ).fetchone()
                if old:
                    if (old["guild_id"], old["channel_id"], old["author_id"], old["created_at"]) != (
                        event.guild_id, event.channel_id, event.author_id, event.created_at
                    ):
                        return self._decision(event, "ignored", "message_identity_conflict")
                    if event.modified_at < old["modified_at"]:
                        return self._decision(event, "ignored", "stale_event")
                    if event.version == old["version"]:
                        return self._decision(event, "ignored", "duplicate_event")
                    if event.modified_at == old["modified_at"]:
                        return self._decision(event, "ignored", "conflicting_event_version")
                elif self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0] >= self.config.storage.max_messages:
                    raise CapacityError("message_capacity")
                eligible = not event.is_thread and not event.has_attachments
                fingerprint = content_fingerprint(event.content)
                self.store.db.execute(
                    "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) "
                    "DO UPDATE SET modified_at=excluded.modified_at, version=excluded.version, "
                    "fingerprint=excluded.fingerprint, eligible=excluded.eligible, event_json=excluded.event_json",
                    (event.message_id, event.guild_id, event.channel_id, event.author_id,
                     event.created_at, event.modified_at, event.version, fingerprint, int(eligible), _json(event.to_dict())),
                )
                rows = []
                for row in self.store.db.execute(
                    "SELECT event_json, version, fingerprint FROM messages WHERE author_id=? AND eligible=1",
                    (event.author_id,),
                ):
                    rows.append({**json.loads(row["event_json"]), "version": row["version"], "fingerprint": row["fingerprint"]})
                matches = find_matches(self.config, rows, now)
                keys = {match.group_key for match in matches}
                for incident in self.store.db.execute(
                    "SELECT * FROM incidents WHERE author_id=? AND status='open'", (event.author_id,)
                ).fetchall():
                    if incident["group_key"] not in keys:
                        self._withdraw(incident, now, "withdrawn")
                incident_ids, new_ids = [], []
                for match in matches:
                    incident_id, is_new = self._record(match, now)
                    if any(item["message_id"] == event.message_id for item in match.evidence):
                        incident_ids.append(incident_id)
                        if is_new:
                            new_ids.append(incident_id)
                if not eligible:
                    return self._decision(event, "unsupported", "thread_or_attachment_not_reviewed")
                if incident_ids:
                    return self._decision(event, "review", "code_rule_match", incident_ids, new_ids)
                return self._decision(event, "no_match", "no_code_rule_match_not_ai_clearance")
        except CapacityError as error:
            # The failed transaction rolled back; neither evidence nor alerts were partially applied.
            with self.store.transaction():
                self.store.set_setting("capacity_errors", self.store.get_setting("capacity_errors", 0) + 1)
                self.store.set_setting("paused", True)
                self._invalidate_window(now, "needs_revalidation")
            return self._decision(event, "capacity", str(error))

    def _prune(self, now):
        cutoff = now - self.config.storage.retention_seconds
        self.store.db.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        self.store.db.execute("DELETE FROM incidents WHERE updated_at < ?", (cutoff,))
        expired = self.store.db.execute(
            "SELECT * FROM incidents WHERE status='open' AND expires_at <= ?", (now,)
        ).fetchall()
        for incident in expired:
            self._withdraw(incident, now, "expired")

    def _invalidate_window(self, now, status):
        # We cannot trust events missed during a pause or under a different policy.
        self.store.db.execute("DELETE FROM messages")
        self.store.set_setting("coverage_started_at", now)
        for incident in self.store.db.execute("SELECT * FROM incidents WHERE status='open'").fetchall():
            self._withdraw(incident, now, status)
        self.store.db.execute("UPDATE reports SET status='cancelled' WHERE status='pending'")

    def set_paused(self, paused: bool):
        if type(paused) is not bool or (not paused and self.config.mode == "off"):
            raise ValueError("Invalid pause transition for the configured mode")
        with self.store.transaction():
            if paused != self.store.get_setting("paused", False):
                self._invalidate_window(self._now(), "paused" if paused else "needs_revalidation")
            self.store.set_setting("paused", paused)

    def _withdraw(self, incident, now, status):
        revision = incident["revision"] + 1
        # Reserve one terminal audit revision even when evidence capacity is full.
        self.store.db.execute(
            "UPDATE incidents SET status=?, revision=?, updated_at=? WHERE id=?",
            (status, revision, now, incident["id"]),
        )
        self.store.db.execute("INSERT INTO incident_versions VALUES(?,?,?,?,?)", (
            incident["id"], revision, now, status, incident["evidence_json"],
        ))
        self.store.db.execute(
            "UPDATE reports SET status='cancelled', updated_at=? WHERE incident_id=? AND status='pending'",
            (now, incident["id"]),
        )

    def _check_versions(self, incident_id):
        count = self.store.db.execute("SELECT count(*) FROM incident_versions WHERE incident_id=?", (incident_id,)).fetchone()[0]
        if count >= self.config.storage.max_evidence_versions:
            raise CapacityError("incident_evidence_capacity")

    def _record(self, match, now):
        existing = self.store.db.execute(
            "SELECT * FROM incidents WHERE group_key=? AND status='open'", (match.group_key,)
        ).fetchone()
        evidence_json = _json(list(match.evidence))
        if existing and existing["evidence_json"] == evidence_json:
            return existing["id"], False
        if existing:
            incident_id, revision = existing["id"], existing["revision"] + 1
            self._check_versions(incident_id)
            self.store.db.execute(
                "UPDATE incidents SET reason=?, revision=?, updated_at=?, expires_at=?, evidence_json=? WHERE id=?",
                (match.reason, revision, now, match.expires_at, evidence_json, incident_id),
            )
        else:
            if self.store.db.execute("SELECT count(*) FROM incidents").fetchone()[0] >= self.config.storage.max_incidents:
                raise CapacityError("incident_capacity")
            incident_id, revision = uuid4().hex, 1
            self.store.db.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                incident_id, match.group_key, match.rule_id, match.author_id, match.reason, "open",
                revision, now, now, match.expires_at, None, self.config.policy_version,
                self.config.policy_hash, evidence_json,
            ))
        self.store.db.execute("INSERT INTO incident_versions VALUES(?,?,?,?,?)", (
            incident_id, revision, now, "open", evidence_json,
        ))
        # Update pending reports in place. Later senders must revalidate their exact revision.
        destinations = [("moderator", self.config.command_channel_ids[0])]
        if self.store.get_setting("logs_enabled", False) and self.config.log_channel_id and self.config.log_channel_id != self.config.command_channel_ids[0]:
            destinations.append(("log", self.config.log_channel_id))
        notified = False
        for kind, destination in destinations:
            pending = self.store.db.execute(
                "SELECT id FROM reports WHERE incident_id=? AND kind=? AND status='pending'", (incident_id, kind)
            ).fetchone()
            within_cooldown = existing and existing["last_notified_at"] is not None and now < existing["last_notified_at"] + self.config.rules.notification_cooldown_seconds
            if not pending and within_cooldown:
                continue
            payload = self._report_payload(incident_id, revision, match, destination)
            if pending:
                self.store.db.execute(
                    "UPDATE reports SET incident_revision=?, updated_at=?, payload_json=? WHERE id=?",
                    (revision, now, _json(payload), pending["id"]),
                )
            else:
                count = self.store.db.execute("SELECT count(*) FROM reports WHERE status='pending'").fetchone()[0]
                if count >= self.config.storage.max_pending_reports:
                    self.store.set_setting("dropped_reports", self.store.get_setting("dropped_reports", 0) + 1)
                    continue
                self.store.db.execute("INSERT INTO reports VALUES(?,?,?,?,?,?,?,?)", (
                    uuid4().hex, incident_id, revision, kind, "pending", now, now, _json(payload),
                ))
            notified = True
        if notified:
            self.store.db.execute("UPDATE incidents SET last_notified_at=? WHERE id=?", (now, incident_id))
        return incident_id, existing is None

    def _report_payload(self, incident_id, revision, match, destination):
        # Do not copy attacker text or live mentions into a notification.
        links = [f"[Open message {index}](<https://discord.com/channels/{self.config.guild_id}/{item['channel_id']}/{item['message_id']}>)"
                 for index, item in enumerate(match.evidence[:5], 1)]
        content = (
            f"Moderation review {incident_id} (revision {revision})\n"
            f"Rule: {match.rule_id}\nAuthor ID: {match.author_id}\n"
            f"Observed copies: {len(match.evidence)}\n"
            "Report-only; no public action.\n" + "\n".join(links) + "\n"
            "Staff assessment: Needs attention, Looks okay, or Unsure. Feedback only; no moderation action."
        )
        return {
            "guild_id": self.config.guild_id, "channel_id": destination,
            "content": content, "allowed_mentions": {"parse": [], "users": [], "roles": [], "replied_user": False},
            "policy_version": self.config.policy_version, "policy_hash": self.config.policy_hash,
            "incident_revision": revision,
        }
