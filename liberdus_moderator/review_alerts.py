"""Opt-in role notifications for current low-score screening reports only."""

from .classification_view import saved_classification

COOLDOWN = 300
CONCERNS = frozenset(("sensitive_request", "impersonation", "suspicious_offer", "targeted_abuse"))


def eligible_role(engine, report):
    role = engine.config.rules.review_alert_role_id
    if (not role or role == engine.config.guild_id or report["kind"] != "moderator"
            or report["payload"]["channel_id"] != engine.config.command_channel_ids[0]):
        return None
    incident = engine.store.incident(report["incident_id"])
    if (not incident or incident["rule_id"] != "jev_message" or incident["status"] != "open"
            or incident["revision"] != report["incident_revision"]):
        return None
    result = saved_classification(engine, incident)
    if (result.get("outcome") != "ok" or result.get("evidence_state") != "current"
            or result.get("choice") not in CONCERNS or not 0 <= result.get("confidence", 1) < 0.90):
        return None
    return role


def reserve(engine):
    """Reserve before sending; uncertain delivery must not cause a ping retry."""
    now = engine._now()
    with engine.store.transaction():
        previous = engine.store.get_setting("review_alert_last_attempt", None)
        if previous is not None and now - previous < COOLDOWN:
            return False
        engine.store.set_setting("review_alert_last_attempt", now)
    return True
