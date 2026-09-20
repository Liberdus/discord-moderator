# Read-only Discord setup verification

Run this as the OS user who owns the `liberdus-mod` profile, using Python 3.11 or newer. The utility is a separate setup command; it is not a gateway adapter and does not start moderation.

From an accessible checkout with your validated deployment configuration:

```bash
python3 -m liberdus_moderator.preflight --config config.local.toml
```

By default it reads only the `DISCORD_BOT_TOKEN` entry from `~/.hermes/profiles/liberdus-mod/.env`. `--token-file` can select another private, user-owned dotenv file. It does not execute or interpolate that file, accept duplicate token entries, or print the token. Never pass a token as a command-line argument.

The command makes GET requests to Discord API v10 for the bot identity, configured guild and bot membership, and each configured test/command channel. It does not fetch messages, send messages, join a gateway, restart services, or edit configuration. Redirects are refused, request timeouts and response sizes are bounded, and HTTP errors are reported without response bodies or credentials. A rate-limit response stops the check; it does not repeatedly retry.

`checks_passed: true` means:

- The token identifies the configured bot, which is a member of the configured server.
- Every configured channel is a normal text channel in that server.
- The bot can view and read history in monitored channels, and can additionally send in command/report channels.
- An `@everyone`-only member cannot view those channels, and the bot has no Administrator permission.

Permission calculation follows Discord's [documented precedence](https://docs.discord.com/developers/topics/permissions): base roles, everyone overwrite, aggregate role overwrites, then member overwrite; owners and Administrator bypass channel overwrites.

This is not a complete membership/privacy audit. Other roles and member-specific permissions may allow access; `additional_view_overwrite_ids` identifies explicit additional view grants for review. It does not enumerate all members or prove that only approved testers/moderators can access the channels. The check does not establish Message Content Intent, ordinary-message ingestion, private-command authorization in a running adapter, delivery, or zero-inference behavior. These still require the integration tests in [integration.md](integration.md).

Exit status is 0 for passed checks or 2 for failed/incomplete checks. Share the printed result to resolve remaining setup issues. Do not enable stock Hermes Discord in response to a successful preflight: the dedicated moderation adapter remains a separate implementation milestone.
