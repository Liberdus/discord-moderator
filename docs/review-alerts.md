# Committers review alerts — 0.7.1

After updating the standalone VPS using [the wheel update guide](slash-commands.md#update-the-existing-vps-service),
run these in the private bot-mod channel using your authorized administrator account:

```text
/mod config alert-role action:set role:@committers
/mod config show
```

Choose the actual Committers role in Discord's role selector. Its name alone is
not an ID, and the Committers category is a different object. The setting stores
the selected role ID, so renaming the role does not break alerts. Existing setups
start with role alerts off until a role is selected. You can instead supply `id`.

New flagged JEV reviews with a current score **below 0.90** mention that role in
bot-mod. The four specific concerns are sensitive requests, impersonation,
suspicious offers and targeted abuse. Benign messages, unavailable/unclear results,
stale evidence, and scores of exactly 0.90 or higher do not trigger this alert.
Comparisons use the original score, before display rounding. Automatic deletion
continues to use the approved ≥0.90 rule and its existing context/freshness checks.

A durable five-minute cooldown limits role pings across incidents. Reviews still
appear during the cooldown, without a ping. Reopening a review or editing its
assessment does not ping. Failed/uncertain sends consume the cooldown and are not
retried. A reconnect preserves the cooldown. Notifications do not change screening
results or authorize moderation actions.

Only the selected role is allowed in the outgoing mention list; saved message
text cannot ping other roles, users or @everyone. The ping is part of the review
card message and enables normal notifications. Discord members' own notification
settings can still suppress push notifications.

The role needs View Channel in bot-mod. Discord also requires either a mentionable
role or the bot's **Mention @everyone, @here, and All Roles** permission in that
channel. The configuration command checks both before saving. If needed, edit
bot-mod → Edit Channel → Permissions → Liberdus Moderator and allow that permission
there. The bot's outgoing allowed-mentions list remains restricted to the selected
role. See [Discord's mention rules](https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx).

To disable pings while keeping the review reports:

```text
/mod config alert-role action:off
```

The setting is private `[rules].review_alert_role_id` in `moderation.toml`.
Configuration edits require operator access plus Manage Server/server ownership,
save an audit record, and reconnect automatically. This release does not select
or change any role/permissions on the active VPS or Discord server by itself.
