"""Serialized live-session state and durable, at-most-once report attempts.

No Discord or Hermes imports: a transport provides trusted events and delivery.
"""

from dataclasses import replace
import hashlib
import json
import re

from .commands import CommandRequest, handle_command


class ReviewableText(str):
    """Text plus the exact displayed revision, captured before transport awaits."""
    def __new__(cls, content, identity, revision):
        value = super().__new__(cls, content)
        value.review_target = (identity, revision)
        return value


class AssessmentText(str):
    """Confirmation plus a saved result, used for best-effort report refreshes."""
    def __new__(cls, content, assessment):
        value = super().__new__(cls, content)
        value.assessment = assessment
        return value


class LiveSession:
    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store
        self.config = engine.config
        from .actions import initialize
        initialize(engine)
        with self.store.transaction():
            version = self.store.get_setting("transport_schema_version", 1)
            if version != 1:
                raise ValueError("Unsupported transport schema")
            self.store.db.execute("CREATE TABLE IF NOT EXISTS deliveries("
                                  "report_id TEXT PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,"
                                  "message_id TEXT, outcome TEXT NOT NULL, updated_at REAL NOT NULL)")
            self.store.db.execute("CREATE TABLE IF NOT EXISTS command_receipts("
                                  "message_id TEXT PRIMARY KEY, created_at REAL NOT NULL)")
            self.store.db.execute("CREATE TABLE IF NOT EXISTS review_prompt_links("
                                  "message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,"
                                  "incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,"
                                  "revision INTEGER NOT NULL, created_at REAL NOT NULL)")
            self.store.set_setting("transport_schema_version", 1)
            # A crashed process may have sent successfully before saving the response.
            self.store.db.execute("UPDATE reports SET status='uncertain' WHERE status='sending'")
            self.store.db.execute("UPDATE deliveries SET outcome='uncertain' WHERE outcome='sending'")

    def gap(self, reason):
        if reason not in {"startup", "disconnect", "reconnect", "queue_full", "unavailable_edit",
                          "deleted_message", "invalid_event", "worker_failure"}:
            raise ValueError("Unknown coverage gap")
        now = self.engine._now()
        with self.store.transaction():
            self.engine._invalidate_window(now, "needs_revalidation")
            self.store.set_setting("coverage_gaps", self.store.get_setting("coverage_gaps", 0) + 1)
            self.store.set_setting("last_coverage_gap", {"reason": reason, "at": now})

    def status(self, connected):
        return {**self.engine.status(), "live_discord_connected": connected,
                "coverage_gaps": self.store.get_setting("coverage_gaps", 0),
                "last_coverage_gap": self.store.get_setting("last_coverage_gap"),
                "uncertain_reports": self.store.db.execute(
                    "SELECT count(*) FROM reports WHERE status='uncertain'").fetchone()[0]}

    def command(self, event, message_id, connected):
        """Return a bounded fixed response, or None for unauthorized/duplicate input."""
        if not isinstance(event, CommandRequest):
            raise TypeError("Expected trusted command metadata")
        c = self.config
        if (event.guild_id != c.guild_id or event.channel_id not in c.command_channel_ids
                or not (event.user_id in c.operator_user_ids or set(event.role_ids) & set(c.operator_role_ids))):
            return None
        now = self.engine._now()
        with self.store.transaction():
            if self.store.db.execute("SELECT 1 FROM command_receipts WHERE message_id=?", (message_id,)).fetchone():
                return None
            # Reserve before any command mutation. Crashed/uncertain commands are never replayed.
            self.store.db.execute("INSERT INTO command_receipts VALUES(?,?)", (message_id, now))
            self.store.db.execute("DELETE FROM command_receipts WHERE message_id IN ("
                                  "SELECT message_id FROM command_receipts ORDER BY created_at DESC LIMIT -1 OFFSET 5000)")
        if event.command in ("review", "assess") and event.reply_to_message_id is not None:
            target = self.review_reply_target(event)
            if target is None or len(event.arguments) != 1:
                return "Review not saved. Use a recorded report or incident view, or !mod assess ID REV needs-attention, looks-okay, or unsure."
            event = replace(event, arguments=(*target, event.arguments[0]))
        if event.command in ("delete", "dismiss", "timeout") and event.reply_to_message_id is not None:
            target = self.review_reply_target(event)
            if target is None or event.arguments:
                return "Action unavailable. Open !mod incident ID and use its buttons."
            event = replace(event, arguments=target)
        response = handle_command(self.engine, event)
        if not response.get("authorized"):
            return None
        if not response["ok"]:
            # Error identifiers are generated by the code, not copied from message text.
            return "Moderation command: " + response["error"] + ". Use !mod help for commands."
        if event.command in ("delete", "timeout") and len(event.arguments) >= 2:
            return response["data"]
        if event.command in ("deletion", "auto-delete", "timeout"):
            from .display import panel
            data = response["data"]
            return panel("Moderation action settings", [
                "Policy actions: " + ("ON" if data["actions_enabled"] else "OFF"),
                "Deletion: " + ("ON" if data["deletion_enabled"] else "OFF"),
                "Auto-delete: " + ("ON" if data["auto_delete_enabled"] else "OFF"),
                "Timeout: " + ("ON" if data["timeout_enabled"] else "OFF"),
                "Auto-delete needs deletion ON.", "Timeout is staff-confirmed,",
                "10 minutes, SERVER-WIDE.", "Off blocks new timeouts; it",
                "does not lift existing ones.", "Settings survive restarts."])
        if event.command == "help":
            from .display import panel
            return panel("Moderation help", [
                "PRIVATE STAFF COMMANDS", "--------------------------------",
                "!mod status", "Show connection and JEV usage.", "",
                "!mod exempt-role", "Show role exemption setting.",
                "!mod exempt-role on", "Skip JEV for configured roles.",
                "!mod exempt-role off", "Check those members with JEV.",
                "Repetition checks stay active.", "",
                "!mod pending [PAGE]", "!mod incident ID", "!mod explain ID",
                "Use incident buttons to assess.", "!mod assess ID REV LABEL",
                "LABEL: needs-attention,", "looks-okay, or unsure.", "",
                "!mod pause / !mod resume", "Pause / resume moderation.",
                "!mod selftest", "Run isolated synthetic tests.", "",
                "!mod deletion on / off", "!mod auto-delete on / off", "!mod timeout on / off",
                "!mod delete ID REV [MESSAGE_ID]", "!mod timeout ID REV", "!mod actions ID", "Confirm with the shown button.",
                "!mod dismiss ID REV", "Close review; no account action.",
                "Timeout: 10 min, SERVER-WIDE.", "Authorized staff in bot-mod."])
        if event.command == "exempt-role":
            from .display import panel
            enabled = response["data"]["role_exemption_enabled"]
            return panel("JEV role exemption", [
                "Setting: " + ("ON - skip role members" if enabled else "OFF - check role members"),
                "Configured roles:", *(self.config.classifier.exempt_role_ids[:3] or ("none",)),
                "Applies to future JEV checks.", "Saved across gateway restarts.",
                "Repetition checks stay active.", "Existing results are retained.",
                "!mod exempt-role on / off"])
        if event.command == "selftest":
            from .selftest import format_summary
            return format_summary(response["data"])
        if event.command in ("status", "pause", "resume"):
            status = self.status(connected)
            gap = status["last_coverage_gap"] or {"reason": "none"}
            from .display import panel
            from . import __version__
            lines = [
                "STATUS", "--------------------------------", "Version: " + __version__,
                "Mode: " + ('paused' if status['paused'] else status['mode']),
                f"Connected: {status['live_discord_connected']}",
                "Coverage: " + ("code + JEV (best effort)" if status['classifier_mode'] == 'report_only' else "code only"),
                f"Messages: {status['message_count']}", f"Incidents: {status['incident_count']}",
                f"Pending reports: {status['pending_reports']}", f"Uncertain: {status['uncertain_reports']}",
                f"Coverage resets: {status['coverage_gaps']}", f"Last: {gap['reason']}", "",
                "JEV", "--------------------------------",
                f"Mode: {status['classifier_mode']}", f"State: {status['classifier_state']}",
                f"AI attempts: {status['ai_attempts']}",
                "Role exemption: " + ("ON" if status['role_exemption_enabled'] else "OFF"),
                "Exempt roles:", *(status['screening_exempt_role_ids'][:3] or ['none'])]
            if status['classifier_mode'] == 'report_only':
                lines += [f"Screened: {status['screening_checked']}", f"Flagged: {status['screening_flagged']}",
                          f"Not checked: {status['screening_unchecked']}", f"Role-exempt: {status['screening_exempt']}",
                          f"Screening attempts: {status['screening_attempts']}",
                          f"Trial used/reserved: ${status['screening_reserved_microusd'] / 1000000:.6f}",
                          f"Trial cap: ${self.config.classifier.daily_budget_microusd / 1000000:g}/day, ${self.config.classifier.total_budget_microusd / 1000000:g} total"]
            lines += ["", "ACTIONS", "--------------------------------",
                      "Policy: " + ("enabled" if status["actions_enabled"] else "disabled"),
                      "Deletion: " + ("ON" if status["deletion_enabled"] else "OFF"),
                      "Auto-delete: " + ("ON" if status["auto_delete_enabled"] else "OFF"),
                      "Timeout (staff): " + ("ON" if status["timeout_enabled"] else "OFF")]
            return panel("Liberdus moderation", lines + ["Commands: !mod help"])
        if event.command in ("incident", "explain"):
            from .classification_view import format_incident
            # Saved text is separately validated and escaped; metadata keeps the narrow panel.
            incident = response["data"]
            text = format_incident(incident, details=event.command == "explain")
            if incident["evidence_view"]["available"]:
                return ReviewableText(text, incident["id"], incident["revision"])
            return text
        if event.command == "actions":
            from .display import panel
            from datetime import datetime, timezone
            data = response["data"]
            rows = ["Incident:", data["id"], "Last 8 action attempts", ""]
            for item in data["history"]:
                rows += [("Automatic " if item["automatic"] else "Staff ") + item["kind"] + ": " + item["outcome"],
                         "Target: " + item["target_id"], "By: " + item["actor"],
                         "Revision: " + str(item["revision"]),
                         datetime.fromtimestamp(item["started_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), ""]
            return panel("Moderation action history", rows + ([] if data["history"] else ["No action attempts."]))
        if event.command == "pending":
            from .staff_review import format_pending
            return format_pending(response["data"])
        if event.command in ("assess", "dismiss"):
            from .staff_review import LABELS
            assessment = response["data"]
            from .display import panel
            text = panel("Staff assessment saved", [
                "REVIEW", "--------------------------------", LABELS[assessment['label']],
                f"Reviewed revision: {assessment['revision']} ({assessment['state']})",
                f"Latest evidence: {'review complete' if assessment['latest_complete'] else 'still pending'}",
                "", "REFERENCE", "--------------------------------", "Incident ID", assessment['incident_id'],
                "Reviewer ID", assessment['reviewer_id'], "",
                "No moderation action or new AI call.", "Pending reviews: !mod pending"])
            return AssessmentText(text, assessment)
        if event.command == "review":
            from .moderator_review import LABELS
            review = response["data"]
            label = LABELS[review["label"]]
            return (f"**Moderator review saved**\nLabel: {label}\n"
                    f"Reviewed revision: {review['revision']} ({review['state']})\n"
                    f"Incident: `{review['incident_id']}`\nReviewer: `{review['reviewer_id']}`\n"
                    "Legacy content label only; staff review unchanged. JEV retained; no enforcement action taken.\n"
                    "Use !mod incident ID to see the saved review.")
        return "Moderation setting updated. No enforcement action taken."

    def save_review_prompt(self, message_id, channel_id, target):
        if not re.fullmatch(r"[1-9][0-9]{0,19}", message_id) or channel_id not in self.config.command_channel_ids:
            raise ValueError("Invalid private review prompt")
        identity, revision = target
        with self.store.transaction():
            self.store.db.execute("INSERT INTO review_prompt_links VALUES(?,?,?,?,?)",
                                  (message_id, channel_id, identity, revision, self.engine._now()))
            self.store.db.execute("DELETE FROM review_prompt_links WHERE message_id IN ("
                                  "SELECT message_id FROM review_prompt_links ORDER BY created_at DESC, message_id DESC "
                                  "LIMIT -1 OFFSET 5000)")

    def review_reply_target(self, event):
        """Resolve only a delivery recorded by this bot, never a quoted message body."""
        prompt = self.store.db.execute("SELECT incident_id,revision,channel_id FROM review_prompt_links WHERE message_id=?",
                                       (event.reply_to_message_id,)).fetchone()
        if prompt is not None:
            return (prompt["incident_id"], str(prompt["revision"])) if prompt["channel_id"] == event.channel_id else None
        row = self.store.db.execute(
            "SELECT r.incident_id,r.incident_revision,r.payload_json FROM reports r "
            "JOIN deliveries d ON d.report_id=r.id WHERE d.message_id=? AND d.outcome='sent' "
            "AND r.kind='moderator' AND r.status='sent'", (event.reply_to_message_id,)).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
            if (payload["guild_id"] != event.guild_id or payload["channel_id"] != event.channel_id
                    or payload["incident_revision"] != row["incident_revision"]):
                return None
            return row["incident_id"], str(row["incident_revision"])
        except (KeyError, TypeError, ValueError):
            return None

    def render_snapshot(self, identity, revision):
        from .classification_view import incident_view, format_incident
        incident = self.store.incident(identity)
        if incident is None:
            raise ValueError("Saved incident unavailable")
        return format_incident(incident_view(self.engine, incident, int(revision)))

    def assessment_messages(self, event, identity):
        """At most the clicked bot message and latest delivered private report."""
        candidates = []
        if event.reply_to_message_id:
            target = self.review_reply_target(event)
            if target and target[0] == identity:
                candidates.append((event.reply_to_message_id, target[0], int(target[1])))
        rows = self.store.db.execute(
            "SELECT d.message_id FROM reports r JOIN deliveries d ON r.id=d.report_id "
            "WHERE r.incident_id=? AND r.kind='moderator' AND r.status='sent' AND d.outcome='sent' "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT 1", (identity,))
        for row in rows:
            reference = replace(event, reply_to_message_id=row["message_id"])
            target = self.review_reply_target(reference)
            if target and target[0] == identity and row["message_id"] not in [item[0] for item in candidates]:
                candidates.append((row["message_id"], target[0], int(target[1])))
        return candidates

    def claim_report(self):
        """Validate current evidence and persist intent before handing a report to I/O."""
        now = self.engine._now()
        with self.store.transaction():
            self.engine._prune(now)
            if (self.config.mode != "report_only" or self.store.get_setting("paused", False)
                    or self.store.get_setting("policy_hash") != self.config.policy_hash):
                return None
            for report in self.store.reports():
                incident = self.store.incident(report["incident_id"])
                p = report["payload"]
                destination = (self.config.command_channel_ids[0] if report["kind"] == "moderator"
                               else self.config.log_channel_id if report["kind"] == "log" and self.store.get_setting("logs_enabled", False)
                               else None)
                valid = (incident is not None and incident["status"] == "open"
                         and incident["revision"] == report["incident_revision"] == p.get("incident_revision")
                         and incident["policy_hash"] == p.get("policy_hash") == self.config.policy_hash
                         and incident["expires_at"] > now
                         and p.get("guild_id") == self.config.guild_id and p.get("channel_id") == destination
                         and destination is not None and destination not in self.config.monitored_channel_ids
                         and p.get("allowed_mentions") == {"parse": [], "users": [], "roles": [], "replied_user": False}
                         and isinstance(p.get("content"), str) and 0 < len(p["content"]) <= 1900)
                if not valid:
                    self.store.db.execute("UPDATE reports SET status='cancelled', updated_at=? WHERE id=?", (now, report["id"]))
                    continue
                self.store.db.execute("UPDATE reports SET status='sending', updated_at=? WHERE id=?", (now, report["id"]))
                self.store.db.execute("INSERT INTO deliveries VALUES(?,NULL,'sending',?)", (report["id"], now))
                return report
        return None

    def finish_report(self, report_id, message_id=None):
        if message_id is not None and not re.fullmatch(r"[1-9][0-9]{0,19}", message_id):
            raise ValueError("Invalid Discord delivery ID")
        outcome = "sent" if message_id is not None else "uncertain"
        with self.store.transaction():
            self.store.db.execute("UPDATE reports SET status=?, updated_at=? WHERE id=? AND status='sending'",
                                  (outcome, self.engine._now(), report_id))
            self.store.db.execute("UPDATE deliveries SET message_id=?, outcome=?, updated_at=? WHERE report_id=?",
                                  (message_id, outcome, self.engine._now(), report_id))


def delivery_nonce(identity):
    # discord.py 2.7.1 sends enforce_nonce=True whenever a nonce is supplied.
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def parse_command(content, guild_id, channel_id, user_id, roles=(), reply_to_message_id=None):
    if not isinstance(content, str) or len(content) > 1000:
        return None
    parts = content.strip().split()
    if len(parts) < 2 or parts[0] != "!mod":
        return None
    try:
        return CommandRequest(guild_id, channel_id, user_id, parts[1], tuple(roles), tuple(parts[2:]), reply_to_message_id)
    except ValueError:
        return None
