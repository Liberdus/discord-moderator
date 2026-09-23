# Native Discord commands — 0.8.0

`/mod status` also shows the progress of [bounded message catch-up](catch-up.md).
The 0.8.0 update keeps the existing command menu and settings.

Discord stores application commands separately from the bot's installed code.
Reusing the same application/token does not remove the Hermes menu. The standalone
runner now publishes a guild-scoped `/mod` menu for its configured server, then
removes obsolete global commands from that same application. See Discord's
[application command registration](https://docs.discord.com/developers/interactions/application-commands).

Registration checks the logged-in bot identity. It publishes the new guild menu
before clearing global commands and skips writes when the menu already matches.
It does not change other applications or other guilds' registrations. This assumes
the dedicated bot application is used only for this moderator; keep the old Hermes
moderation profile disabled. The Hermes plugin does not run this registration.
Existing `!mod` commands remain available as a compatibility fallback.

## Update the existing VPS service

Run on the VPS hosting the standalone bot. Build first, then stop the service and
replace its installed wheel. Run each step only after the previous one succeeds:

```bash
cd /root/discord-moderator
git pull --ff-only
.venv/bin/python -m pip wheel --no-deps . --wheel-dir dist
sudo systemctl stop liberdus-moderator
sudo /opt/liberdus-moderator/venv/bin/python -m pip --isolated install \
  --no-deps --force-reinstall \
  dist/liberdus_discord_moderator-0.8.0-py3-none-any.whl
sudo systemctl reset-failed liberdus-moderator
sudo systemctl start liberdus-moderator
sudo systemctl status liberdus-moderator --no-pager
sudo journalctl -u liberdus-moderator -n 40 --no-pager
```

Adjust the checkout path for your setup account. Pinned runtime dependencies are
unchanged from 0.6.x. The wheel includes the 0.6.1 systemd credential fix and
replaces the previously hand-edited package files. Preserve any unrelated local
source changes separately. Configuration, keys, history and spending counters
remain in place. Do not rerun setup or the fresh service installer.

Look for `slash_commands_registered` and `connected`, then type `/mod help` in
the private staff channel. Refresh Discord if it still displays its cached menu.
If the journal shows `slash_registration_failed`, moderation continues and
`!mod status` reports the registration failure. Check the application's
`applications.commands` authorization and network access, then restart the service.
The setup wizard's invite URL includes both `bot` and `applications.commands`.
Errors in the journal omit credentials and raw HTTP error details.

## Moderation menu

| Command | Behavior |
| --- | --- |
| `/mod help` | Show the native command guide |
| `/mod status`, `/mod summary`, `/mod connection` | Status, activity/costs, and saved connection diagnostics |
| `/mod pending` | Pending reviews; optional page |
| `/mod incident`, `/mod explain` | Open a saved incident as a shared staff card with buttons |
| `/mod actions` | Saved action attempts for an incident |
| `/mod assess` | Save an assessment of the supplied incident revision |
| `/mod dismiss` | Close that review without deleting messages |
| `/mod delete` | Fetch current messages and ask for deletion confirmation |
| `/mod pause`, `/mod resume` | Pause immediately or resume with a fresh window |
| `/mod deletion`, `/mod auto-delete`, `/mod exempt-role`, `/mod timeout` | Show a switch, or select On/Off |
| `/mod timeout-user` | Request a staff timeout where policy permits; blocked by the current public policy |
| `/mod selftest` | Local synthetic checks without a provider call or Discord action |

Discord prompts for each command's fields. Incident commands use the ID shown on
the review; revision-sensitive commands also ask for its revision. Delete accepts
an optional individual message ID. Its confirmation remains actor-bound, checks
fresh content, and expires after 60 seconds. Pause and disabling switches take
priority over queued enable commands.

Incident/explain cards are shared in the private staff channel so staff can use
their buttons. Other slash responses are private. Confirmed deletions still
produce the shared staff receipt. Reading status, configuration or saved evidence
does not call JEV. Existing action, cost, scope and at-most-once safeguards apply.

All commands require an explicitly configured operator ID and the configured
private staff channel. Administrator permission alone does not grant bot operator
access. Discord may show the command picker to other members; unauthorized
requests receive only a private refusal. To restrict picker access too, use
Discord's Server Settings → Integrations → this bot's command permissions. Those
UI permissions do not replace the bot's own authorization checks.

## Configuration menu

Reading `/mod config show` and `/mod config history` requires operator access.
**Changes also require a fresh Discord Manage Server permission check or server
ownership.** Keys and arbitrary file/settings paths are never accepted.

| Command | Editable value |
| --- | --- |
| `/mod config monitor` | Add/remove a monitored text channel |
| `/mod config staff-channel` | Change the private staff command/report channel |
| `/mod config operator` | Add/remove an authorized human staff member |
| `/mod config category` | Add/remove an included or excluded category boundary |
| `/mod config exempt-role` | Add/remove a role eligible for the exemption switch |
| `/mod config alert-role` | Set/off a role ping for flagged reviews below 0.90; five-minute cooldown |
| `/mod config budget` | Daily and lifetime JEV caps in USD |
| `/mod config call-limits` | Daily and lifetime JEV call caps |

For IDs, choose the channel/member/category/role **or** supply the raw `id` field.
Use exactly one. Raw IDs also allow removal of deleted channels/roles or departed
members. Newly added targets must still exist and pass the server/access checks.

Examples using Discord's option fields:

```text
/mod config monitor action:add channel:#general
/mod config monitor action:remove id:1479253446258458644
/mod config staff-channel channel:#bot-mod
/mod config operator action:add user:@StaffMember
/mod config category action:add boundary:excluded category:Committers
/mod config exempt-role action:add role:@Trusted
/mod config budget daily:1 lifetime:4
/mod config alert-role action:set role:@committers
```

Valid edits save the private policy file, record the actor, and reconnect the bot
automatically. Both the original and proposed staff channels must remain private;
the editor must be able to view/send there, and new operators need access too.
The bot rechecks channel scope and its required permissions. You cannot remove
your own operator access or the last monitored channel. Category boundaries must
remain consistent with the selected channels; remove affected monitored IDs
before excluding their category. Concurrent disk edits or connection changes
during validation prevent that request from overwriting the settings.

The reconnect invalidates the previous evidence window and deletion confirmations.
Retained history, action switches and daily/lifetime spending counters stay intact.
Lowering a cap below usage stops further spending; it does not reset usage.
The audit retains the latest 50 edit requests and shows the latest 10. `requested`
means the save was not confirmed; `saved` means the policy write completed. After
an interrupted reply, check `/mod config show` and history before retrying.

Discord cannot edit credentials, bot/server identity, provider endpoints/models,
storage paths, or the fixed automatic-deletion threshold. Manage secrets using
the VPS's private setup/credential tools. The approved four-concern **≥0.90** rule,
context exclusions and fresh-evidence checks remain unchanged.

See [Committers review alerts](review-alerts.md) for setup, role permissions and
cooldown behavior. Alerts are off until a role is selected.
