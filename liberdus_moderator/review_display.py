"""Bounded review sections shared by plain text and Discord component layouts."""

from datetime import datetime, timezone

from .evidence_view import excerpt, units


class ReviewMessage(str):
    """Keep transport sections explicit; never parse headings from member text."""

    def __new__(cls, overview, assessment, actions, reference):
        result = super().__new__(cls, "\n\n".join((overview, assessment, actions, reference)))
        result.overview = overview
        result.staff_section = assessment
        result.action_section = actions
        result.reference = reference
        return result


def format_review(incident, *, details=False):
    from .classification_view import REASONS, _age
    from .staff_review import LABELS

    rules = {"cross_channel_repeat": "Cross-channel repeat", "same_channel_repeat": "Same-channel repeat",
             "blocked_domain": "Blocked domain", "jev_message": "JEV screening"}
    states = {"open": "Open", "withdrawn": "Withdrawn", "expired": "Expired", "paused": "Paused",
              "needs_revalidation": "Needs recheck", "policy_changed": "Policy changed"}
    assessment = incident.get("staff_assessment", {})
    revision = incident["revision"]
    latest = incident.get("latest_revision", revision)
    count = len(incident["evidence"])
    review_state = "Complete" if assessment.get("complete") else "Pending"
    overview = ("## Moderation review\n"
                f"**{states.get(incident['status'], 'Unknown state')} · {review_state} review**\n"
                f"{rules.get(incident['rule_id'], 'Unknown rule')} · {count} saved message{'s' if count != 1 else ''}\n")
    if latest != revision:
        overview += f"**Older snapshot:** revision {revision} · latest revision {latest}\n"
    else:
        overview += f"Revision {revision}\n"

    classification = incident["classification"]
    suggestion = "### JEV suggestion\n"
    if classification["outcome"] == "ok":
        suggestion += (f"**{classification['choice'].replace('_', ' ').capitalize()}** · "
                       f"Score **{classification['confidence']:.2f}** (model score)\n")
        if classification.get("purpose"):
            suggestion += "Purpose: " + classification["purpose"].replace("_", " ").capitalize() + "\n"
    else:
        suggestion += "Result: " + {"not_evaluated": "No saved evaluation", "unavailable": "Saved evaluation unavailable"}.get(
            classification["outcome"], classification["outcome"].replace("_", " ").capitalize()) + "\n"
    if "revision" in classification:
        suggestion += f"Evaluated revision {classification['revision']} · {_age(classification['age_seconds'])}\n"
    if details:
        if classification.get('model'):
            suggestion += f"Model: `{classification['model']}`\nOutcome: {classification['outcome']}\n"
        if classification.get('latency_ms') is not None:
            suggestion += f"Response time: {classification['latency_ms']} ms\n"
        legacy = incident.get("moderator_review", {})
        if legacy.get("label"):
            from .moderator_review import LABELS as OLD_LABELS
            suggestion += f"Legacy content label: {OLD_LABELS[legacy['label']]} · revision {legacy['revision']}\n"
    if classification.get("evidence_state") == "historical":
        suggestion += "**Historical result:** " + REASONS[classification["reason"]] + ".\n"
    elif classification.get("evidence_state") == "current":
        suggestion += "Evaluation record matches the current saved evidence.\n"
    if incident["rule_id"] == "jev_message":
        suggestion += "-# Possible concern only; link destinations not checked.\n"
    action_rows = incident.get("action_history", [])
    if action_rows:
        suggestion += "### Recent actions\n"
        for row in action_rows[:2]:
            suggestion += ("Auto " if row["automatic"] else "Staff ") + row["kind"] + ": " + row["outcome"] + "\n"

    staff = (LABELS[assessment["label"]] if assessment.get("applies_to_snapshot") else
             "Review changed evidence" if assessment.get("label") else
             "Review unavailable" if assessment.get("state") == "unavailable" else "Not reviewed")
    staff_text = f"### Staff assessment\n**{staff}** · {review_state}\n"
    if assessment.get("label"):
        staff_text += f"Assessment of revision {assessment['revision']} · {assessment['state'].capitalize()}\n"
    if assessment.get("reviewer_id"):
        at = datetime.fromtimestamp(assessment["reviewed_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prefix = "Reviewed by" if assessment.get("applies_to_snapshot") else "Previous review by"
        staff_text += f"{prefix} <@{assessment['reviewer_id']}> · {at}\n"
    staff_text += (f"Assess revision {revision}: possible issue, acceptable, or needs context.\n"
                   "-# Feedback only; no action; no new AI call. Pending: `!mod pending`")

    action_text = "### Actions\n"
    if incident.get("actions_enabled") is False:
        action_text += "Deletion and timeouts are disabled.\n"
    else:
        action_text += "Delete checks current content, then asks for confirmation.\n"
        action_text += ("Timeout is disabled by policy.\n" if incident.get("public_deletion_allowed") else
                        "Timeout restricts the member for 10 min server-wide; confirmation required.\n")
    action_text += "Dismiss closes this review without deleting messages."
    reference = f"-# Incident ID: `{incident['id']}`"

    evidence = incident.get("evidence_view", {})
    texts, links = [], ""
    if evidence.get("available"):
        items = evidence["items"]
        texts = list(dict.fromkeys(item["content"] for item in items))
        links = " · ".join(f"[Open message {index}](<{item['url']}>)" for index, item in enumerate(items[:3], 1)) + "\n"
        if len(items) > 3:
            links += f"Showing 3 of {len(items)} source links.\n"
    note = "-# Saved excerpt; messages may have changed.\n"
    if len(texts) > 1:
        note += f"-# First of {len(texts)} distinct saved texts.\n"
    before = (overview + f"\n**Sender:** <@{incident['author_id']}>\n"
              f"-# Author ID: `{incident['author_id']}`\n\n### Saved message\n> ")
    after = "\n\n" + links + note + "\n" + suggestion.rstrip()
    fixed = str(ReviewMessage(before + after, staff_text, action_text, reference))
    remaining = 1900 - units(fixed)
    if remaining < 50 and details:
        return format_review(incident)
    if remaining < 0:
        raise ValueError("Incident display exceeds its fixed limit")
    preview = excerpt(texts[0], min(500, remaining))[0] if texts else "Unavailable for this scope."
    result = ReviewMessage(before + preview + after, staff_text, action_text, reference)
    if units(result) > 1900:
        raise ValueError("Incident display exceeds its fixed limit")
    return result
