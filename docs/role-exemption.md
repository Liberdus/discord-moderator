Current action controls and deployment: [0.5.0 moderation actions](moderation-actions.md). The older report-only instructions and checkpoints below remain historical.

# JEV role exemption — 0.4.1

The owner requested that members with Discord role **1302455329795342377** be exempt from message checks. In this JEV-screening context the exemption applies to **JEV only**. Deterministic repetition/domain rules and authorized moderator commands remain active. It does not grant moderator-command privileges or change any Discord permissions.

The profile policy gains:

```toml
[classifier]
exempt_role_ids = ["1302455329795342377"]
```

This line belongs inside the existing classifier table; the installer adds it without replacing the table or changing budgets. The default is an empty list. Existing policies with no exemptions retain their previous policy hash; applying an exemption changes the policy hash and makes earlier assessments/results historical as appropriate.

The adapter copies role IDs from Discord's author membership data into the trusted message snapshot. The single-message worker checks that snapshot before requesting JEV, with a second guard in the worker itself. Exempt messages incur no JEV call, spend or screening alert; status separately counts `Role-exempt` events instead of reporting them as failed checks. Repeated exempt messages can still trigger an ordinary repetition report. The old incident-only shadow worker also skips evidence carrying exempt roles. Role IDs are not sent to JEV.

Typing or mentioning a role ID in message text cannot grant an exemption. If role information is unavailable or there is no matching role, normal JEV screening applies. Membership is the snapshot attached to the observed message/fetched edit, not a continuous member-roster watch. Changes to roles affect subsequently observed membership; this feature does not retroactively erase earlier incidents or cancel a request already sent. It does not add a Server Members intent or a member-fetch request: the pinned Discord SDK updates the author Member roles from the message's member payload.

## Install

From the existing **hermes** terminal, run:

```bash
python3 /tmp/liberdus-apply-0.4.1-20260921.py
```

The helper verifies both bundles, disables the custom platform and finishes a shared-gateway restart, installs 0.4.1 with a code backup, adds the exemption with a policy backup, then enables the platform and finishes the final restart. Existing JEV report-only mode, $1/day and $4 total allowance, accounting, key, pause state and channel scope are preserved. Telegram may briefly reconnect on the shared gateway. It stops on a failed stage; keep the printed backups and output before retrying.

In `bot-mod`, run `!mod status`. Expect:

```text
Exempt roles: 1302455329795342377
Role-exempt: 0
```

The order of the lines may differ; the count increases after new messages from exempt members. Post one harmless message from an account with the role, then inspect status: `Role-exempt` should rise and `Screening attempts` should not rise because of that message. Other simultaneous users can still generate attempts. To test JEV triggers, use an account without the exempt role. No need to revoke the setup role or change channel permissions.

For results or subsequent JEV-off configuration, use the new configurator:

```bash
python3 /tmp/liberdus-role-exemption-20260921.pyz results
```

`exempt-role` adds this fixed role idempotently and preserves other settings. `off` keeps the normal existing disable/restart/configure/enable/restart procedure. Older 0.4.0 configurators cannot parse the added field; retain their bundles as history, and use this current configurator for the updated profile.

For rollback, disable the platform and finish the restart, restore the saved previous plugin **and policy**, then re-enable/restart. Retain the database and audit/accounting records. Removing the role from Discord is not necessary for rollback.

The developer cannot access the owner-only Hermes profile. Implementation and mocked checks do not establish live activation of this exemption.

Validation: 213 core/setup tests pass on Python 3.11.16 and 3.12.3; 64 pinned Hermes/Discord integration tests pass with network blocked/mocked. Tests verify member exemptions, nonmember role mentions, preserved repetition detection, configuration compatibility, update preservation and deployment failure handling. Live activation is not claimed.

Bundle SHA-256:

- `liberdus-update-0.4.1-20260921.pyz`: `8bd3ba7bd6a7ad24ca0f6d8eae37586338f4c181d8ebfd0867aabed0ad7c783e`
- `liberdus-role-exemption-20260921.pyz`: `ec8ed653ca37cd141c1f4c95695ed169c91bc84a34d2cc280904b90e1110dc11`
