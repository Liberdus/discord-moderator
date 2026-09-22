# New VPS with a fresh moderation database

This procedure installs standalone 0.6.1 on another VPS using the existing
Discord bot and JEV credentials. The owner chose a fresh database: do not run
`migrate` or copy the old database. Existing Discord permissions stay with the
bot. No new bot application or invite is needed for the existing Liberdus server.

Prepare the new host first. Keep its service stopped until the old moderator is
disabled and stopped. Local instance locks cannot detect another VPS.

## 1. Prepare the new VPS

These commands target Ubuntu 24.04 LTS with sudo access and systemd. Ubuntu's
[Python virtual environment package](https://packages.ubuntu.com/noble/python3.12-venv)
provides the supported Python/venv runtime. Use an ordinary SSH terminal so
credential input can be hidden.

Run on the **new VPS**:

```bash
sudo apt-get update
sudo apt-get install -y git gh python3 python3-venv ca-certificates nano
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git --hostname github.com
git clone https://github.com/Liberdus/discord-moderator.git
cd discord-moderator
python3 -m venv .venv
.venv/bin/python -m pip install '.[discord]'
.venv/bin/python -m pip wheel --no-deps . --wheel-dir dist
sudo .venv/bin/liberdus-moderator install-service \
  --wheel dist/liberdus_discord_moderator-0.6.1-py3-none-any.whl
```

The repository is private. During `gh auth login`, open the displayed URL on your
own computer or phone, enter the one-time code, and sign in with the GitHub account
that has access to `Liberdus/discord-moderator`. GitHub authentication belongs to
your SSH setup account; it is not copied into the moderator's service account.
See [GitHub CLI browser authentication](https://cli.github.com/manual/gh_auth_login).
If SSH Git authentication is already configured for this repository, that can be
used instead. A GitHub account login is separate from the Discord/JEV key prompts.

The final command runs the interactive setup wizard and installs an encrypted
credential store, dedicated service account, and systemd unit. **Omit `--start`**
at this stage. The installer performs read-only Discord metadata checks; it does
not open a Gateway connection, post messages, delete messages, or call JEV.

Do not run the separate portable `setup` command first. The service installer
runs setup under its own account and creates its own private instance directory.
It refuses existing installations instead of overwriting their files.

## 2. Enter the Liberdus setup values

Enter only the existing `DISCORD_BOT_TOKEN` and `TYPESAFE_API_KEY` values in the
wizard's hidden prompts. Use your private credential records or retrieve them
privately from the old moderation profile; never paste them into chat, a Git
file, or a shell command. Other Hermes/GitHub/provider credentials are not needed
by the bot. On the old VPS, the profile's private credential file is
`/home/hermes/.hermes/profiles/liberdus-mod/.env`; view it only in your own private
terminal/editor if you need the saved values. Do not regenerate the Discord
token while the old instance is still serving moderation.

| Wizard prompt | Value |
| --- | --- |
| Discord bot token | Existing Liberdus Moderator token, hidden input |
| Server ID | `746426387606274199` |
| Monitored channels | Paste the comma-separated list below |
| Private staff channel | `1551252553331642558` (`bot-mod`, in Committers) |
| Authorized staff user IDs | `977263877391794217` |
| Enable JEV screening? | `y` |
| JEV API key | Existing Typesafe/JEV key, hidden input |
| Daily spending cap | `1` |
| Lifetime spending cap | `4` |
| Action option | `3` — staff-confirmed and automatic deletion |
| Save this installation? | `y`, after reviewing the displayed names and IDs |

Paste this single line for monitored channels:

```text
1293238000313958451,1318757883260964884,1453063291961212959,1479253446258458644,1486610685398876170,746426388050870282,746499160823431199
```

These are `developers`, `privacy-news`, `self-intro`, `general`, `feedback`,
`incoming`, and `help`. `bot-mod` is the staff destination, not a monitored
channel. The old `bot-test` channels remain unselected. Add any additional
authorized human staff IDs explicitly; access to the staff channel alone does
not authorize commands or buttons.

The new wizard binds monitoring to these exact channel IDs. Unlike the previous
category policy, a selected channel stays selected when moved between categories.
Retain the previous category boundaries by editing the installed settings before
starting. Run:

```bash
sudo -u liberdus-mod nano /var/lib/liberdus-moderator/moderation.toml
```

In the existing `[scope]` section, replace the empty values for these two fields
(do not add duplicate keys):

```toml
included_category_ids = ["746426387606274201", "746426387606274202"]
excluded_category_ids = ["1318586868136415333"]
```

Use your preferred installed editor if `nano` is unavailable. The explicitly
selected private staff channel remains allowed inside Committers for commands
and reports; other channels there remain outside monitoring.

Option 3 enables all four approved automatic-deletion concerns at an unrounded
score **>= 0.90**: sensitive requests, impersonation, suspicious offers, and
targeted abuse. Existing context, freshness, unchanged-message, permission, and
rate checks still apply. Timeouts stay off. JEV's `report_only` classifier label
does not mean deletion is off; the action switches are separate.

The service account is `liberdus-mod`; its private settings and state are under
`/var/lib/liberdus-moderator`. Encrypted credential files are under
`/etc/liberdus-moderator`, outside Git. Encryption uses the destination's host key;
see [systemd credential protection](https://systemd.io/CREDENTIALS/) and the
[security limits in the setup guide](standalone.md#credentials-and-retained-data).

## 3. Disable the old moderator at cutover

Run these commands on the **old VPS**, as the existing `hermes` account, only
after the new installation has completed:

```bash
hermes -p liberdus-mod config set platforms.liberdus_moderator.enabled false
hermes -p default gateway restart
hermes -p liberdus-mod config get platforms.liberdus_moderator.enabled
hermes -p default gateway status
```

The configuration value must be `false`. The shared gateway restart unloads the
moderator and briefly reconnects other Hermes integrations, including Telegram.
Wait for the restart to finish and verify the moderator is offline in Discord
while the new service is still stopped. Verify the other Hermes integrations
return. If shutdown did not complete, resolve it before starting the new bot.

Leave this profile disabled afterward, including across old-VPS reboots.
The old database can remain privately on the old host; this procedure does not
delete it or transfer it.

## 4. Start the new moderator

Run on the **new VPS**, after confirming the old moderator stopped:

```bash
sudo systemctl enable --now liberdus-moderator
sudo systemctl status liberdus-moderator --no-pager
sudo journalctl -u liberdus-moderator -n 40 --no-pager
```

Every service start runs the installation checks with its systemd credentials
before connecting. Successful output includes `Installation checks passed` and
then `connected`. If a check fails, keep the new service stopped while fixing
the reported issue. A shell `doctor` invocation does not inherit the service's
runtime credentials; use the service startup check and journal.

In Discord's **bot-mod** channel, send these individually:

```text
!mod status
!mod summary
!mod auto-delete
```

Verify connected, unpaused, JEV screening enabled, deletion ON, auto-delete ON,
timeout OFF, and the >=0.90 four-concern rule. These commands do not perform a
paid screening test or delete anything. Setup verifies a JEV key is configured;
its provider authentication is checked when normal live screening occurs.

## What starting fresh means

- Retained incidents, saved messages/authors, staff reviews, and action history
  start empty on the new VPS. Newly flagged messages retain their authors normally.
- Old cards remain visible in Discord, but their incidents and button bindings
  are unavailable to the new database. Use newly generated reviews going forward.
- Local daily/lifetime usage counters and reservations start over. The new $4
  lifetime cap applies to this installation; it does not include the old bot's
  spending or reset charges at the provider.
- Message history is not automatically rescanned. Monitoring starts with new
  eligible events after the bot connects.

For rollback, first stop and disable the new service with
`sudo systemctl disable --now liberdus-moderator`. Only then re-enable the old
profile and restart its gateway. Histories and spending counters remain separate;
neither host knows the other's post-switch activity.
