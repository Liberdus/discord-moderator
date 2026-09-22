# Moderation review cards — 0.5.10

The [0.5.16 card update](all-cards.md) extends this style to all moderation replies and replaces excerpt/detail code blocks with escaped quoted text and normal card text. The historical 0.5.10 description below records the original layout.

Deployed September 22, 2026 and retained in the current [0.5.11 release](deletion-notices.md). Do not rerun the 0.5.10 updater on the current installation.

The review message now uses Discord Components V2. The saved message and JEV result appear first, followed by a Staff assessment card with its own buttons and an Actions card with a separate button row. This places each explanation beside its controls instead of collecting both button rows at the bottom of one long text message.

The sender appears above the saved excerpt as a clickable Discord mention and a copyable author ID. This uses the author identity already retained with the incident; deleting the source message does not remove that identity or require a Discord lookup to render it. Mentions are suppressed, so showing the sender or reviewer does not notify them. Discord resolves the visible mention name; this release does not archive usernames or display-name history. The numeric ID remains available if Discord cannot resolve a name, subject to existing incident retention.

The layout reads in this order:

1. Review status, rule, message count and snapshot revision. An older snapshot explicitly shows the latest revision.
2. Sender, saved text and original-message links. Saved excerpts remain normalized, bounded and protected against injected formatting/mentions.
3. JEV suggestion, model score, evaluation revision/age and current-or-historical context.
4. Staff assessment and reviewer, then **Needs attention / Looks okay / Unsure**.
5. Action descriptions, then **Delete message(s) / Dismiss / Timeout 10 min**. Disabled actions remain disabled.
6. Copyable incident ID.

New private reports and `!mod incident ID` / `!mod explain ID` replies use the layout after deployment. Existing posted reports are not bulk edited. Saving an assessment refreshes the retained bound report/view and converts a legacy message to the new layout by clearing its former content, embeds and attachments. An old report can also be reopened with `!mod incident ID` without changing an assessment. Failed edits retain the saved assessment and give the existing best-effort display warning; there is no send fallback or action retry.

Only presentation changes. Authorization, stored message/revision bindings, current-content Delete confirmation, policy checks, public timeout prohibition, JEV decisions and accounting, retention and action limits are unchanged. The full visible review text remains bounded to 1,900 UTF-16 units. Component sections come from structured trusted fields, not from parsing member-supplied text. Component custom IDs remain compatible with existing durable interaction handling after reconnects/restarts.

## Validation and deployment

**632 tests passed:** 434 core/setup and 198 pinned-runtime integration tests. Core tests retain read-only rendering, historical evidence, Unicode/fence/mention protection and length checks. Pinned-runtime regressions exercise real discord.py 2.7.1 send/edit serialization with HTTP mocked: V2 flags, empty legacy content, distinct labelled button containers, suppressed mentions, nonces, conversion of old reports, durable command bindings, disabled public timeouts and sender identity after source deletion. No automated live Discord action or paid JEV request was used to verify this layout. The full suites ran outside the sandbox because its thread-wakeup restriction also hung a minimal `asyncio.to_thread` probe.

The code-only update helper is `scripts/apply_review_layout.py`, staged as `/tmp/liberdus-apply-0.5.10-20260922.py`. It verifies the pinned archive, disables/restarts the shared gateway, validates/installs the plugin with a rollback copy, then enables/restarts. It preserves configuration, policy, saved flags and accounting. Check the latest BUILD_PLAN checkpoint before running it; never rerun a one-time updater against its already installed version.

Release SHA-256 values:

- `/tmp/liberdus-update-0.5.10-20260922.pyz`: `132d28b9e1e7a32a7ad83f10b162f0404f19b576ee6113f8f77b33f5dd3740ee`
- `/tmp/liberdus-apply-0.5.10-20260922.py`: `4f9cc1dd207700ba23805e437be5f9d77bb79ac6f7c446a008eed216691c4e21`

The archive passed integrity and compilation checks; all 44 plugin files matched the local tested source. Previous release artifacts remain intact.

References: [Discord components](https://docs.discord.com/developers/components/overview), [component reference](https://docs.discord.com/developers/components/reference), [discord.py LayoutView](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.ui.LayoutView).
