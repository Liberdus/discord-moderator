# Standalone moderation — 0.7.2

Use native `/mod` commands for moderation and `/mod config` for approved settings.
The standalone runner registers its menu at startup and removes obsolete global
commands from its own bot application. See [commands and configuration](slash-commands.md).

The bot can run without Hermes. The standalone runner and optional Hermes wrapper
share the same Discord service, moderation engine, JEV screening, card layouts,
staff controls, deletion checks, and SQLite history. The standalone wheel does
not depend on Hermes and does not load its profiles or secrets.

This release supports Linux with Python 3.11 or newer. One installation serves
one Discord server. Windows service support and a web dashboard are not included.

The original Liberdus Hermes profile was disabled for the new-VPS cutover.
Installing or testing this repository does not start or migrate that profile.
To move to another VPS without retaining the database, use the
[fresh database VPS guide](new-vps.md). It includes the current Liberdus setup
values and keeps the destination stopped until the old moderator is disabled.

## Quick setup

From a reviewed checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install '.[discord]'
.venv/bin/liberdus-moderator setup
.venv/bin/liberdus-moderator doctor
.venv/bin/liberdus-moderator start
```

`start` runs in the foreground; Ctrl+C shuts it down. The default private instance
directory is `~/.liberdus-moderator`. To choose another location,
pass `--home /absolute/path` after each command. Setup refuses a directory inside
a Git checkout or an existing installation; it does not overwrite saved keys.

The wizard asks for:

1. The Discord bot token, using hidden terminal input.
2. A server from the bot's server list, or its server ID.
3. Explicit text channels to monitor. Select numbered entries, exact names,
   channel IDs, `<#channel_id>` mentions, or Discord channel links. Separate
   multiple choices with commas. Duplicate names require a number or ID.
4. One private staff channel for reports and `!mod` commands.
5. Authorized staff user IDs, which are resolved to server members for review.
6. Optional JEV screening, a hidden API key, and daily/lifetime spending caps.
7. Reports only (default), staff-confirmed deletion, or staff plus auto-delete.

Before saving, the wizard displays channel names, IDs, staff identities, spending
caps, and action behavior. It reads Discord metadata; it does not read message
history, post messages, delete anything, or make a paid AI test call.

Names are only selection aids. Saved scope uses channel IDs, so renames work and
new channels do not silently enter scope. Ordinary text channels are supported;
threads, forums, announcements, bot/webhook messages, and attachments remain
outside the existing moderation scope. Public and private monitored text channels
may be selected. The staff destination must remain hidden from `@everyone`.

New installations use `explicit_channel_scope = true`. Optional included/excluded
category boundaries still apply if configured, but categories are not required
when every channel is explicitly selected. Existing migrated category policies
retain their original boundaries and behavior.

## Discord preparation

Create an application at the [Discord Developer Portal](https://discord.com/developers/applications).
On its Bot page, obtain the bot token and enable Message Content intent. Leave
the Interactions Endpoint URL empty: this bot receives button interactions
through its Gateway connection. The wizard checks the application flags and
prints a bot invite link. Follow [Discord's application setup guide](https://docs.discord.com/developers/quick-start/getting-started)
and [Message Content intent instructions](https://docs.discord.com/developers/events/gateway#message-content-intent).

For copying user IDs in Discord, enable User Settings → Advanced → Developer
Mode, then use Copy User ID on a staff member. Channel links also work in setup,
so copying numeric channel IDs is optional.

Grant the bot View Channel and Read Message History on monitored channels, plus
Send Messages in the staff channel. Deletion additionally requires Manage
Messages on each monitored channel. The invite link includes these individually;
Administrator is neither requested nor accepted by the permission checks.
Timeouts remain off in the wizard and public deletion does not authorize them.

The staff channel can live in a category excluded from collection, such as
Committers. Its explicit channel ID is the exception for private staff commands
and reports; this does not add the rest of that category to monitoring.

Hiding a channel from `@everyone` does not prove only the chosen operators can
read it. Setup lists an access warning when other explicit overwrites grant
visibility. Review staff roles and membership in Discord. Moderation commands
and buttons separately require an authorized user ID.

## Always-on Linux service

The fresh installer creates its own account, code environment, state directory,
credential store, and systemd unit. It requires Python 3.11+ with `venv`, systemd,
`useradd`, and `runuser`. Encrypted storage additionally requires `systemd-creds`.
It refuses to overwrite an existing system installation or reuse an existing
`liberdus-mod` account.

Build the wheel, then run the installer from the reviewed environment:

```bash
.venv/bin/python -m pip wheel --no-deps . --wheel-dir dist
sudo .venv/bin/liberdus-moderator install-service \
  --wheel dist/liberdus_discord_moderator-0.7.2-py3-none-any.whl --start
```

The installer fetches the pinned Discord/JEV HTTP dependencies from PyPI and runs
the same interactive setup wizard under the dedicated account. It stores
encrypted credentials, removes the wizard's temporary portable credential files,
then publishes the unit. With `--start`, it enables and starts the service after
the reviewed setup; omit `--start` to leave it installed but stopped.

| Item | Location / identity |
| --- | --- |
| Service account | `liberdus-mod`, with no login shell |
| Code and virtual environment | `/opt/liberdus-moderator`, owned by root |
| Configuration and database | `/var/lib/liberdus-moderator`, private to the service account |
| Encrypted credential files | `/etc/liberdus-moderator`, private to root |
| Service | `liberdus-moderator.service` |

The service uses `NoNewPrivileges`, a read-only system filesystem with explicit
writable state/temp locations, no Linux capabilities, restricted address
families, and disabled core dumps. Discord remains the only Gateway connection;
no public HTTP dashboard or inbound web endpoint is required.

Use standard service controls:

```bash
sudo systemctl status liberdus-moderator
sudo systemctl stop liberdus-moderator
sudo systemctl start liberdus-moderator
sudo journalctl -u liberdus-moderator --since '10 minutes ago'
```

Startup runs `doctor` with the unit's credentials before opening the Gateway.
Systemd credentials exist only inside the service context; a shell invocation
of `doctor` against that data directory does not inherit them. Use the service's
startup check and journal, plus `!mod status` / `!mod summary` in the staff channel.

For a host without encrypted credential support, `install-service
--plain-credentials` explicitly selects root-only, unencrypted files supplied
through `LoadCredential`. There is no silent encryption downgrade. View a unit
without installing anything using `liberdus-moderator service-unit --jev`.

Cancelling or failing a fresh installation before unit publication removes only
the directories/account created by that attempt. Once the unit is published,
data is retained if starting it fails. Fix the reported configuration/permissions,
then use `systemctl reset-failed liberdus-moderator` and start it again.

## Credentials and retained data

Portable installations use `credentials/discord-token` and optionally
`credentials/jev-key`, with permissions 600 in a directory with permissions 700.
These are protected files, not encrypted storage. The loader rejects unsafe
ownership, broad permissions, symbolic/hard links, oversized files, and malformed
values. It does not execute dotenv text or borrow a process environment key.

System installations use [systemd credentials](https://systemd.io/CREDENTIALS/).
On the reported Ubuntu 24.04 host with systemd 255, the runtime credential mount
is root-owned with directory mode `0550` and file mode `0440`, for both encrypted
and plain `LoadCredential`. Version 0.6.1 accepts this read-only layout. Directory
and file owners must be root or the service user; groups must be root or the
service's primary group. The systemd backend requires exact directory mode `0550`
and file mode `0440`, regular single-link files, and no symlinks. It rejects other
groups, world access, write bits, and executable files. These rules are specific
to runtime systemd credentials; portable files keep their owner-only checks.
The service still runs as `liberdus-mod`, without capabilities. Do not run it as
root or try to chmod/chown the read-only credential mount.

The encrypted mode uses `systemd-creds` with the local host key. This protects an
encrypted credential copy when the host key is not also exposed. It is not a
guarantee against a compromised host, root administrator, or compromised bot
process. During operation, the bot must be able to read its own credentials.
Removing a plaintext file is not proof of erasure from disks or snapshots.

The Discord token is used only to authenticate to Discord. The JEV key is used
only in JEV's authentication header, not in the classification prompt. JEV
receives the selected message text and extracted URLs. Portable and service
commands accept secret values only through a hidden input prompt; no key-value
command-line option is provided.

Runtime logs admit only a closed set of operational events. Raw third-party
transport logs, exception bodies, interaction webhook URLs, and tracebacks are
suppressed. Existing staff diagnostics retain fixed error codes and action
outcomes without retrying uncertain deletions. Tests deliberately inject fake
keys into errors and verify they do not appear in output.

Saved evidence contains user messages and author IDs. Treat the SQLite database
and backups as private data too. The default evidence retention is seven days;
the existing caps and pruning rules still apply. A normal stopped-instance
backup should include `moderation.toml`, `instance.json`, and `state/`, and exclude
`credentials/` and `/etc/liberdus-moderator/`. Protect or encrypt backups before
moving them off the host. On a replacement machine, enter credentials again
through the hidden prompts; host-encrypted credential blobs are not portable.

To replace a portable credential, stop the bot first:

```bash
.venv/bin/liberdus-moderator key discord
.venv/bin/liberdus-moderator key jev
```

For the system service, replacement also performs a controlled restart:

```bash
sudo /opt/liberdus-moderator/venv/bin/liberdus-moderator service-key discord
sudo /opt/liberdus-moderator/venv/bin/liberdus-moderator service-key jev
```

Regenerate/revoke the old credential at the appropriate provider if it was
exposed. Replacing a file alone does not revoke a provider credential.

## Diagnostics and action policy

`doctor` checks private local files, database identity/integrity, local instance
locks, Discord bot identity, application intents, channel scope/privacy, and
required permissions. Errors identify the affected channel and missing
permission. `doctor --offline` checks local files and state without API calls.
Portable doctor requires the instance to be stopped so database/credential
checks cannot race an active instance. Same-user instances also hold a
token-specific lock across different data directories. These locks cannot detect
a second bot on another server or under an unrelated operating-system account.

Doctor verifies that a JEV credential is configured; it does not make a paid AI
request to prove the provider accepts the key. Runtime screening diagnostics
report provider authentication failures. Setup and doctor do not consume JEV
budget, create synthetic incidents, or delete test messages.

New installations default to reporting only. When explicitly selected, automatic
deletion retains the approved four concerns at unrounded score **>= 0.90**:
sensitive requests, impersonation, suspicious offers, and targeted abuse.
Warnings/unclear purpose, stale/changed evidence, completed reviews, paused state,
missing permissions, and uncertain previous attempts remain protected. Existing
freshness, rate, spending, and at-most-once action rules are reused unchanged.

## Migrate the current Hermes bot

Migration is a separate, stopped-service operation. Install the migration extra
in the new environment:

```bash
.venv/bin/python -m pip install '.[discord,migration]'
```

Disable `platforms.liberdus_moderator.enabled` in the source profile, then stop
that moderation instance through its existing gateway lifecycle. If Hermes
shares its gateway with other platforms, plan the short gateway restart and
verify those platforms afterward. The migration command requires explicit
`enabled: false` and refuses to proceed while the source database lock is held.

```bash
.venv/bin/liberdus-moderator migrate \
  --from-hermes /absolute/path/to/hermes/profiles/liberdus-mod \
  --home /absolute/path/to/new-private-moderator
.venv/bin/liberdus-moderator doctor --home /absolute/path/to/new-private-moderator
.venv/bin/liberdus-moderator start --home /absolute/path/to/new-private-moderator
```

This creates a portable standalone instance for the account running the command.
It copies only the two required credentials, rewrites only the database location
in the validated policy, and uses SQLite's backup API to include committed WAL
data. The complete database is retained, including incidents, saved authors and
messages, staff reviews, action attempts, pause/deletion flags, and spending
counters. It does not connect Discord, call JEV, start a service, or alter the
source. Do not pass this existing migrated directory to the fresh system
installer; adopting it into a different service account requires a separate
reviewed ownership/service setup.

The changed database path changes the policy hash. The existing engine treats
prior evidence as historical at first startup and resets its active window.
Saved history remains reviewable; old proposals/results are not replayed as new
actions. Keep the old moderator disabled when starting the standalone one.

For rollback, stop the standalone bot completely before re-enabling Hermes.
The source snapshot represents the cutover time: if standalone has since taken
actions, retain that newer database and reconcile its history before rollback.
Blindly restoring the older source database can lose newer audit records.

## Updates and checks

<a id="update-an-existing-060-system-service-to-061"></a>

### Update an existing 0.6.x or 0.7.x system service to 0.7.2

On the destination VPS with the existing checkout (the reported host uses
`/root/discord-moderator`), build the new wheel before stopping the service:

```bash
cd /root/discord-moderator
git pull --ff-only
.venv/bin/python -m pip wheel --no-deps . --wheel-dir dist
sudo systemctl stop liberdus-moderator
sudo /opt/liberdus-moderator/venv/bin/python -m pip --isolated install \
  --no-deps --force-reinstall \
  dist/liberdus_discord_moderator-0.7.2-py3-none-any.whl
sudo systemctl reset-failed liberdus-moderator
sudo systemctl start liberdus-moderator
sudo systemctl status liberdus-moderator --no-pager
sudo journalctl -u liberdus-moderator -n 40 --no-pager
```

Run each step only if the previous command succeeds. Adjust the checkout path
for another setup account. The pinned runtime dependencies are unchanged from
0.6.0, so this update uses `--no-deps`. Installing the wheel replaces hand-edited
package files under `/opt/liberdus-moderator`; retain any unrelated local code
changes separately before updating. Configuration, credentials and the database
remain in place. Do not rerun the fresh installer or setup wizard. Startup runs
`doctor` with the actual service credentials before connecting to Discord.
Then look for `slash_commands_registered` and use `/mod help` in the private staff
channel. See [the menu and registration troubleshooting](slash-commands.md).

Keep code separate from instance data. Stop the bot, take a private consistent
database/configuration backup, install the reviewed new wheel into its code
environment, run the startup checks, and restart. Do not run setup again or
replace the existing database to update code.

The repository tests cover secure files, hidden input, setup metadata, permission
failures, explicit channel selection, cancelled installs, key replacement,
credential-safe logs, stopped-profile migration, duplicate-instance refusal,
shutdown signals, and unchanged moderation behavior. `standalone_tests/` runs
with the Discord SDK and mocked transport without importing Hermes; existing
Hermes integration tests exercise the same shared service. Root account/service
creation is tested with external commands mocked, not by creating a live service
during development.
