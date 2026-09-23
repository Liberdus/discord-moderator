# Brief resumed connections — 0.7.2

Version 0.8.0 adds [bounded catch-up](catch-up.md) separately. An incomplete
recovery can produce a coverage warning even when a brief-resume notice was
suppressed. The disconnect-notice behavior described below remains in place.

The owner reported repeated sub-second gateway disconnects that successfully
resumed but still posted "Discord connection restored" in bot-mod. The health
monitor queued a disconnect notice immediately, before its duration was known.
This release changes staff-notice eligibility only. It does not change gateway
reconnection, add message-history catch-up, or claim that every missed message
was screened.

After recording a successful resume, the service withdraws that disconnect's
pending notice contribution when the recorded duration is under five seconds
and no other gap reason is pending. It does this before marking itself online.
Exactly five seconds or longer, new sessions, unknown timing and mixed gaps
retain their notices, subject to the existing five-minute notice cooldown.
Connection timing is the existing duration rounded to milliseconds; nothing
changes its recording or display.

The health monitor tracks the current disconnect contribution in memory and
removes at most one count. A longer outage pending during the cooldown survives
a subsequent brief resume. A ticket cannot be reused, survive a restart, remove
a claimed delivery, or decrement a saturated count. Suppression requires both
the disconnect and recovery metadata writes to succeed; uncertainty keeps the
notice. Other health state, warning/recovery episodes and cooldown timestamps
are untouched.

Every disconnect and reconnect still invalidates screening evidence and deletion
confirmations. Coverage counters and connection history remain visible through
`/mod status` and `/mod connection` (also their `!mod` equivalents). JEV failures,
budget/storage warnings, scope changes, queue overflow and worker failures retain
their existing behavior. Committers review alerts and their cooldown are separate
and unchanged.

Install the wheel and restart the active standalone VPS using the
[existing-service update instructions](slash-commands.md#update-the-existing-vps-service).
There is no new setting, credential change or database migration. Keep the old
Hermes moderation profile disabled. Publication of the repository does not update
the destination VPS.
