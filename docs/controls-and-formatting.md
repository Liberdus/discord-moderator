# Private controls and message layout — 0.4.2

Run in the existing `hermes` terminal:

```bash
python3 /tmp/liberdus-apply-0.4.2-20260921.py
```

The hash-pinned helper disables the custom platform, waits for the shared gateway restart, upgrades the code, ensures role `1302455329795342377` is configured, then enables the platform and waits for the final restart. It accepts 0.4.0 or 0.4.1 (also supported older pilot releases). It preserves the moderation database, credentials, budgets, counters, and any saved exemption toggle. No live installation has been performed by the development session.

In **bot-mod**, authorized operators can use:

| Command | Effect |
| --- | --- |
| `!mod help` | Show private command help. |
| `!mod exempt-role` | Show the current exemption setting and configured roles. |
| `!mod exempt-role on` | Skip JEV for observed members of configured roles. |
| `!mod exempt-role off` | Allow JEV to check those members again. |
| `!mod status` | Show the toggle, connection, counters, and trial budget. |

The toggle defaults ON, is stored in SQLite, and survives gateway restarts. Only already-authorized operators in command channels can change it; holding the exempt role grants no command access. Repetition checks remain active in both states. Turning it off does not turn on a disabled JEV mode or override pauses, scope, queues, or budgets. Changes affect future checks; no history backfill, stored-result deletion, counter reset, or new AI call is caused by the command. Re-enabling the exemption prevents queued matching-role evidence from being sent or applied; a request already sent cannot be unbilled. Membership comes from Discord event metadata, never mentions in text.

Every outbound bot message and refreshed incident has top/bottom dividers. Status, help and plain confirmations use narrow code blocks; existing incident panels and clickable source-message links stay intact. Previously sent messages change only when refreshed. Formatting stays within Discord's message limit.

After installation, check `!mod help`, `!mod status`, then `!mod exempt-role off` and `!mod exempt-role on`. Use a nonexempt account for JEV trigger tests while ON. The helper does not send Discord test messages or make paid API calls. Updated owner configurator: `/tmp/liberdus-controls-20260921.pyz` (for `results`/`off` operations as needed).

Validation: 220 core/setup tests on Python 3.11; 65 integration tests using pinned Hermes interfaces and discord.py 2.7.1. Tests use mocked network boundaries, not live Discord. Keep the updater's backups for rollback; restore compatible code and policy together without resetting the database or budget counters.
