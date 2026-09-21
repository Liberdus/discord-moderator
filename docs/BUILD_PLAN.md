# Liberdus Discord Moderator: Phased Build Plan

Status: planning only; no bot, server, channel, or VPS changes have been made.
Documentation checked: September 9, 2026.

## Private and public channel flow

```text
PRIVATE MODERATOR CHANNEL                    PUBLIC CHANNEL / PRIVATE TEST CHANNEL
Authorized moderator sends a request         Member posts or edits a message
                |                                           |
                v                                           v
Check server + allowed command channel       Check server + monitored channel
Check actual sender's user ID / roles        Ignore own output + duplicate events
                |                                           |
      Unauthorized -> reject/ignore               Out of scope -> ignore
                |                                           |
                v                                           v
Fixed command?                               Apply code-based moderation rules
  Yes -> handle in code (0 AI tokens)          Settled -> record decision
  No  -> bounded Hermes request               Needs judgment -> compact AI review
          with relevant incident context                        |
                |                                Ambiguous -> stronger AI or human
                |                                           |
                +---------------------+---------------------+
                                      |
                                      v
                      MODERATION PLUGIN / HARNESS
                      Validate decision and evidence
                      Recheck channel, mode, permissions
                      Apply action limits and approvals
                      Public content is evidence, never authority
                                      |
                                      v
                        No action / review / permitted action
                                      |
                                      v
                       Save incident, outcome, and usage
                       Durable state outside AI conversation
                                      |
                +---------------------+---------------------+
                |                     |                     |
                v                     v                     v
       PRIVATE LOG CHANNEL     PRIVATE MOD CHANNEL      PUBLIC / TEST CHANNEL
       Optional: on or off     Command confirmation     Approved warning
       Fixed incident format   Incident / review reply  or no response
       No extra AI call        Templates by default     Templates by default
                |                     |                     |
                +---------------------+---------------------+
                                      |
                         Own posts do not trigger AI again
```

The diagram describes the system we will build, not a claim that installing a Hermes plugin automatically provides every connection. Phase 4 proves the event and delivery paths before moderation logic is enabled. A private test channel follows the public-message path even though only invited testers can see it.

## 1. Goal and agreed constraints

Build a reusable moderation harness for Liberdus using Hermes on Daniel's existing VPS. Start in designated test channels inside the real Liberdus Discord server. Expand to specific production channels after testing and owner authorization.

- Daniel currently has no administrative rights in the relevant channels. Server installation, channel creation, and permission changes depend on the owner or delegated staff.
- The bot can receive authorized commands in a private moderator channel. Ordinary public messages never grant permission to use Hermes tools or change the bot's policy.
- Discord log posting is optional. Internal incident and usage records remain available when log posting is disabled.
- Code handles routine checks, commands, actions, and notifications. AI receives bounded classification requests when interpretation is needed.
- Credentials, configuration, code, and durable state have separate recovery procedures.
- Initial scope is text-message moderation. Images, audio, attachments, forum posts, and threads require explicit coverage decisions; unsupported content must not be reported as reviewed.
- V1 excludes automated bans, kicks, timeouts, and role changes. Those affect members beyond one test channel and require a later, separate design.
- Complete one authorized phase at a time. Record evidence and remaining blockers before moving to the next phase.

## 2. Credentials, IDs, and permissions are different

There are no separate "Liberdus channel credentials." The bot authenticates with a bot token; server membership and channel permissions determine what it can access.

| Item | Purpose | Secret? | Who supplies or controls it? |
| --- | --- | --- | --- |
| Discord application ID | Identifies the app and its installation | No | Bot application owner |
| Bot user ID | Identifies the bot account and its own messages | No | Application owner / Discord |
| Bot token | Authenticates the running bot | Yes | Application owner; entered securely on VPS |
| Liberdus server/guild ID | Restricts this deployment to Liberdus | No | Owner or member with server access |
| Test, moderator, log, and production channel IDs | Identify routing destinations | No | Owner or person who can access each channel |
| Moderator user and role IDs | Identify authorized operators | No | Owner/moderation team confirms them |
| Discord permissions | Permit viewing, posting, installing, or deleting | Not a credential | Server owner or appropriately delegated staff |
| Model provider authentication | Allows Hermes inference | Yes | Daniel's existing Hermes setup or designated payer |
| VPS SSH access | Allows deployment and maintenance | Yes | Daniel / VPS administrator |

Daniel uses his own Discord account interactively for the Developer Portal and any server setup he is authorized to perform. The running bot uses its bot identity. Do not provide Hermes with Daniel's or the owner's Discord password, personal session token, browser cookies, or two-factor recovery codes.

## 3. Responsibility and access request

Recommended division: Daniel creates or maintains the application and VPS deployment; the Liberdus owner installs the app and controls server permissions. The owner may instead assign a narrowly scoped setup role to Daniel.

| Task | Recommended actor | Access needed |
| --- | --- | --- |
| Create the application / retrieve bot token | Daniel or designated app owner | Developer Portal access to that application |
| Install the bot in Liberdus | Owner or designated administrator | Manage Server (`MANAGE_GUILD`) |
| Create test category/channels | Owner, or Daniel if delegated | Manage Channels; owner handles server-level creation if it is not delegated |
| Edit channel permission overwrites | Owner, or appropriately delegated staff | Manage Permissions / applicable Manage Roles authority |
| Create or reposition server roles | Owner or designated role administrator | Manage Roles and sufficient role hierarchy |
| Run test messages and review incidents | Daniel and selected moderators | Access to the selected test/moderator channels |
| Expand production visibility | Owner or delegated administrator | Authority to edit those production channel permissions |
| Enable production enforcement | Owner approves; Daniel deploys | Approved plugin policy plus required bot permissions |

Server installation and channel access are separate: the bot is installed once into Liberdus, then granted access to selected channels. It is not separately invited into each normal text channel. Guild installation requires Manage Server. [Discord installation guidance](https://docs.discord.com/developers/quick-start/getting-started)

Channel and role permissions interact. Administrator bypasses channel restrictions, so this plan does not request Administrator for Daniel or the bot. Effective permissions must be checked after setup. [Discord permission reference](https://docs.discord.com/developers/topics/permissions)

### Request Daniel can send to the owner

> I'd like to pilot a Hermes moderation bot in restricted test channels inside Liberdus. Could you install the bot using an installation link I provide, create a private test channel, and give me and the bot access to an approved private moderator channel? An additional private log channel is optional.
>
> You can make the server changes yourself, or delegate the necessary channel-management access to me. I do not need Administrator or your login. Initially the bot should only view the approved test/moderator/log channels, and it should not have permission to delete member messages.
>
> After the report-only tests pass, I would request Manage Messages only in the test channel for deletion tests. Before expanding to production, I'll share the results, proposed channel list, rules, model/provider, and usage limits for approval. Production starts in report-only mode, with enforcement approved separately. Automated bans, kicks, timeouts, and role changes are outside this pilot.

This is a draft request; it has not been sent.

## 4. Phase map

| Phase | Deliverable | Expected outcome |
| --- | --- | --- |
| 0 | Scope and owner responsibilities | Everyone knows what is being tested and who can authorize it |
| 1 | Discord bot application and credentials | A recoverable bot identity exists; no Liberdus changes yet |
| 2 | Liberdus permissions, channels, installation, and IDs | Bot access is limited to approved pilot channels |
| 3 | VPS inventory and isolated moderation configuration | Existing Hermes is ready for controlled development |
| 4 | Event-ingestion and delivery proof | Every required event reaches the harness without public command access |
| 5 | Code-only harness and durable state | Scope, commands, logging, and restart behavior work without AI |
| 6 | Bounded AI classification and usage accounting | Decisions are measurable and spending is limited |
| 7 | Report-only test-channel evaluation | Moderators can judge accuracy without enforcement |
| 8 | Controlled enforcement in the test channel | Approved actions work and remain confined to test targets |
| 9 | Owner-approved production observation | Real-channel quality and traffic are measured without enforcement |
| 10 | Limited production enforcement | Approved rules act only in approved channels |
| 11 | Fresh-install recovery and maintenance runbook | The same deployment can be restored and upgraded predictably |

Phases 1 and 3's read-only inventory can proceed while awaiting owner access. Offline harness work can use synthetic events. Live Liberdus tests cannot proceed until Phase 2 is complete.

## Phase 0 — Confirm scope and responsibilities

**Responsible:** Daniel and Liberdus owner/moderation lead.

**Steps**

1. Confirm the app owner, VPS maintainer, model payer, and person authorized to approve production expansion.
2. Agree on a private test channel and a private moderator command channel. Reuse the existing moderator channel only with its owner's approval; a bot-specific private command channel is also acceptable.
3. Decide whether to create a separate log channel. It may be omitted or share the moderator channel, provided routing and history filtering remain correct.
4. Select initial rule categories and examples: repeated spam, known prohibited links, impersonation/scams, and contextual abuse. Record legitimate criticism and ordinary crypto discussion as allowed examples.
5. Decide what evidence may go to the selected model provider, how long incidents are retained, and who can read them.
6. Agree that test success does not authorize production access or enforcement. Those are separate later decisions.

**Expected outcome:** a short scope record with named owners and agreed pilot boundaries.

**Exit check:** owner acknowledges the pilot scope; unresolved policy choices are listed rather than guessed.

## Phase 1 — Create the Discord bot and obtain its credentials

**Responsible:** Daniel or designated application owner. Liberdus server permissions are not required for this phase.

**Steps**

1. Sign in to the [Discord Developer Portal](https://discord.com/developers/applications) with your own account. Create an application with a recognizable name, such as `Liberdus Moderator Pilot`.
2. Record its application ID and bot user ID. On the Bot page, generate/reset the bot token and store it in a password manager. Resetting an existing token invalidates the prior one, so coordinate if reusing an application.
3. Enable Message Content Intent for ordinary public-message review. During Phase 4, verify that the code requests the matching intent. Enable any other privileged intent only when the installed adapter and intended functionality require it. Follow any access-review requirement shown in the Portal. [Discord Gateway intents](https://docs.discord.com/developers/events/gateway)
4. Configure server/Guild Install with the `bot` and `applications.commands` scopes. Request only the initial permissions agreed with the owner; leave destructive and broad administration permissions out. Create and retain the installation link. [Discord OAuth2](https://docs.discord.com/developers/topics/oauth2)
5. If installation is restricted to the application owner, coordinate application ownership/team access or the installation setting so the Liberdus owner can perform installation. Do not solve this by sharing account credentials.
6. Keep the token out of this plan, repository, screenshots, and chat. Enter it locally into the moderation deployment's protected secret configuration in Phase 3.
7. Record who can rotate the token and recover access to the application. Decide whether long-term ownership belongs to a Liberdus-controlled developer team.

**Expected outcome:** a bot application, installation link, non-secret identity record, and securely stored token. The bot need not be online yet.

**Exit check:** application ownership and recovery are clear; no actual token appears in project documentation or Git.

## Phase 2 — Obtain Liberdus access, create channels, install the bot, and collect IDs

**Responsible:** owner/delegated administrator, with Daniel supplying the installation link and requested layout.

**Prerequisite:** Phases 0–1. This phase requires the owner's server authority.

**Steps**

1. Send the owner the request above, installation link, and permission matrix below. Ask whether they will perform the changes or delegate the necessary access.
2. Have the owner create a private test category and `#bot-test-chat`, accessible to selected testers. Authorize access to a private moderator command channel. Create `#bot-mod-log` only if wanted.
3. Have the owner install the bot into Liberdus. Confirm the application ID matches the intended bot.
4. Apply channel/category permission overwrites before starting the VPS bot. Avoid granting broad server-level message-management permissions.
5. Audit effective bot access across existing categories and unsynced channels. Public channels may grant access through inherited permissions, so merely granting the test category is not sufficient to prove isolation.
6. In Discord settings, enable Developer Mode. Copy the server, channel, user, and moderator role IDs. Preserve IDs as strings. A person must have access to a private channel to obtain and validate its ID; possession of an ID does not grant access. [Discord ID instructions](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID)
7. Ask the owner to confirm that normal members cannot see the test/moderator/log content. Verify Daniel's access separately from the bot's access.
8. Leave production channel IDs as a proposed inventory only. Do not enable production ingestion yet.

**Initial bot permissions**

| Channel | View Channel | Read Message History | Send Messages | Manage Messages |
| --- | --- | --- | --- | --- |
| Private test chat | Allow | Allow | Allow for controlled test output | Deny until Phase 8 |
| Approved private moderator channel | Allow | Allow | Allow | Deny |
| Optional private log channel | Allow | Optional; unnecessary for direct log posting | Allow | Deny |
| Production channels during pilot | Deny | No access | Deny | Deny |
| Other private/staff channels | Deny | No access | Deny | Deny |

Use plain-text notifications initially. Add Embed Links, thread permissions, or other presentation permissions only for features actually implemented. Do not grant the bot Manage Channels, Manage Roles, Manage Server, Administrator, Ban Members, Kick Members, or Moderate Members for this pilot.

Private-channel configuration and category synchronization need explicit checking, especially when channels have custom overrides. [Discord permission setup FAQ](https://support.discord.com/hc/en-us/articles/206029707-Setting-Up-Permissions-FAQ)

For a practical owner walkthrough: create the test category/channel, open its Edit Channel or Edit Category permissions, add the bot and tester roles, and apply the agreed allow/deny entries. Keep the category private to ordinary members. Restrict the bot on other categories and separately configured channels. The owner should perform role creation/reordering or other changes that Daniel's delegated role cannot make.

**Expected outcome:** one installed bot with verified pilot-only visibility, an approved moderator channel, and a complete non-secret ID inventory.

**Exit check:** owner confirms the access matrix; production remains inaccessible; Daniel has either the needed delegated rights or an identified owner who will make future changes.

## Phase 3 — Prepare the existing VPS and moderation profile

**Responsible:** Daniel, with Hermes assisting after this phase is authorized.

**Steps**

1. Inspect the installed Hermes version/commit, Python version, install method, active services, current messaging integrations, and model/provider. Record findings without printing credentials.
2. Back up existing configuration before changing it. Do not upgrade Hermes automatically just because newer documentation exists.
3. Create a fresh moderation profile such as `liberdus-mod`, using the installed CLI's verified profile commands. Avoid copying unrelated personal conversations, trading tools, or broad tool access.
4. Configure the bot token in that profile's protected secret store/environment. Reuse Hermes-managed model authentication where supported; do not duplicate provider tokens into plugin files.
5. Configure private command-channel scope and approved users/roles. Disable public conversational access and DMs for this deployment. Remove unneeded tools from the moderator assistant.
6. Disable unnecessary reactions, automatic threads, and automatic channel-history backfill. Have the harness retrieve explicit incident context later.
7. Ensure only one deployment owns this bot's Gateway connection. Keep a clear service name and stop/start procedure.
8. Record exact commands and paths in the deployment notes after verifying them against the installed version.

Hermes profiles separate configuration and state, not operating-system permissions. Use a dedicated OS user/container if needed to isolate this bot from unrelated VPS secrets. Do not regard a profile name or working directory as a security boundary. [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)

Hermes's Discord allowed channels control conversational routing; history backfill and mention settings affect what reaches an AI conversation. These settings do not by themselves establish a complete public moderation collector. [Hermes Discord setup](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord)

**Expected outcome:** a documented, restricted moderation deployment prepared on the existing VPS.

**Exit check:** existing integrations still work; secrets are protected; no public user has conversational tool access; AI moderation and enforcement remain disabled.

## Phase 4 — Prove the integration before building moderation behavior

**Responsible:** Daniel/Hermes implementation work; selected testers provide live messages.

**Purpose:** determine how all required Discord events reach the harness before any general AI conversation starts.

**Steps**

1. Create a small plugin prototype with inference disabled and read-only event instrumentation. Do not implement enforcement yet.
2. Trace the installed adapter's order of operations: Discord receipt, channel filtering, mention filtering, sender authorization, plugin hooks, session loading, and AI dispatch.
3. Test a normal member's unmentioned message, a message mentioning another member, an edit, and an authorized private command. Confirm message content and IDs arrive correctly.
4. Verify that monitored public/test content can reach the moderation path while the same author remains unauthorized to run moderator commands or general Hermes tools.
5. Prove direct outbound delivery of a fixed private test notification without model inference. Specify the actual supported transport/API path.
6. Choose and document the integration implementation using the decision below. Do not widen public command authorization to make event collection work.
7. Verify plugin failure cannot fall through into a privileged public chat session. Missing required integration support should prevent that deployment from starting its moderation service.

**Integration decision**

- Prefer the stock Discord adapter plus documented plugin hooks if the installed release exposes every required message before it is discarded and supports the intended split in authorization.
- If messages are filtered upstream, use a documented, version-pinned platform-adapter plugin that owns the connection and implements the required public collection/private command split, or implement the missing upstream extension through a tracked change. Treat this as explicit integration work.
- Keep one connection owner for this bot. Do not run a second uncontrolled bot process as a workaround.

Current hooks document `pre_gateway_dispatch` before gateway authorization, but this does not prove every native Discord message reaches that point. Hooks for normalized platform events have their own authorization rules. [Hermes event hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)

The current `ctx.platform_actions` surface documents reactions and thread titles; do not assume it is a generic send/delete API. Raw SDK access is not a shipped stable contract. Outbound messages may need a narrowly scoped Discord REST wrapper using the existing bot credential. [Hermes plugin capabilities](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins) [Discord message API](https://docs.discord.com/developers/resources/message)

Hermes supports registering platform adapters through plugins. Record the chosen adapter, hook contracts, and compatible Hermes revision as part of the install recipe. [Platform adapter guide](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters)

**Expected outcome:** a written integration decision and working zero-inference event/delivery prototype.

Record how other bots and webhooks are handled. Excluding the moderator bot's own output is required for loop prevention; excluding every other bot or webhook is a separate coverage decision that must be visible to moderators.

**Exit check:** ordinary member messages and edits are observed; unauthorized commands fail; out-of-scope channels do not reach classification; fixed delivery makes zero model calls. No later live phase proceeds on an unverified assumption here.

## Phase 5 — Build the code-only harness and durable state

**Responsible:** Daniel/Hermes implementation work.

**Steps**

1. Create the plugin repository and configuration validator. Reject missing IDs, unknown modes, wrong guilds, and unresolved authorization sources.
2. Implement separate routes for private commands and monitored content. Require both an allowed private command channel and an authorized sender.
3. Implement deterministic checks: scope, own-message filtering, event deduplication, approved exact rules, and bounded spam counters. Preserve coverage for messages needing contextual judgment.
4. Implement proposed commands such as `/mod-status`, `/mod-pause`, `/mod-incident`, `/mod-logs`, and `/mod-approve`. Confirm whether they are native Discord commands or Hermes text-command handlers; document the actual syntax.
5. Store incidents, processed message versions, action attempts, approvals, counters, modes, and usage in SQLite. Use transactions and schema migrations.
6. Bind deduplication to message identity and content/version. A newly edited message requires reevaluation. Before acting, recheck that evidence and target are still current.
7. Implement fixed private/public templates and an optional log-posting toggle. Log delivery failure must not cause the action or AI review to repeat.
8. Implement a bounded queue and action executor. Check the current guild/channel, mode, target, permissions, protected-role policy, rate limits, and approval before every action.
9. Keep enforcement disabled. Validate execution with fake Discord responses and synthetic fixtures first.

Approvals must identify the incident, proposed action, target, and evidence version, expire after a defined interval, and verify the approving sender at execution. A model's claim that a moderator approved is not approval.

Capture the approved evidence needed for incident review before deletion. Keep that evidence private, suppress unintended mentions in copied text, and avoid copying private moderator discussions into public warnings.

Respect Discord's rate-limit responses and retry timing. Bound retries and queue size; do not spin on errors. [Discord rate limits](https://docs.discord.com/developers/topics/rate-limits)

**Expected outcome:** a functioning read-only harness whose routine commands, incident storage, and templated outputs require no AI.

**Exit check:** restart does not duplicate completed actions; log toggles work; unauthorized commands fail; invalid configuration fails closed; ordinary members cannot alter rules or destinations.

## Phase 6 — Add bounded AI classification and token accounting

**Responsible:** Daniel; moderators approve the policy examples; payer sets the usage budget.

**Steps**

1. Define a small, versioned policy and labeled examples. Distinguish prohibited conduct from disagreement, criticism, quotation, and reporting a scam.
2. Use a stateless classifier with no action tools. Supply policy, target message, relevant reply/context, and bounded incident facts.
3. Request structured fields such as `disposition`, `rule_id`, `evidence`, and `needs_context`. The executor determines whether a proposed action is actually allowed.
4. Use an economical model that meets the evaluation target. Permit at most one additional review with more context or a stronger model before routing to humans.
5. Register a plugin-specific model task/route if supported. Default to Hermes-managed credentials; explicitly configure any different model/provider and its required grants.
6. Apply hard call/context/output limits and account for actual provider attempts, including automatic retries/fallbacks. Validate JSON locally. An invalid result or truncated output goes to review, not enforcement.
7. Record input, output, cached, and available reasoning-token usage, plus model, policy version, latency, and actual/estimated cost. Distinguish unknown usage from zero.
8. Keep stable policy content cacheable where supported. Confirm cache behavior on the actual model and Hermes provider path.

Hermes's plugin LLM interface supports bounded structured calls and returns usage when the provider supplies it. It avoids a general tool loop, but internal retries/fallbacks still need accounting. [Hermes Plugin LLM Access](https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access)

**Proposed starting limits — tune after evaluation; these are custom harness settings, not existing Hermes flags**

| Control | Initial proposal |
| --- | --- |
| Routine code checks, fixed commands, templated logs | 0 model calls |
| Initial semantic review | 1 classification call |
| Ambiguity/context escalation | At most 1 additional call, then human review |
| Initial request size | Approximately 2,500 total input tokens, including policy/context |
| Escalated request size | Approximately 5,000 total input tokens |
| Visible classifier result | Aim for at most 200 tokens; separately budget reasoning if applicable |
| Concurrent inference | 2 requests initially, behind a bounded queue |
| Retries | At most 1 transient retry within the incident's total attempt budget |
| Total provider attempts per incident | Maximum 3, including retries and escalation |
| Daily/per-minute budget | Explicitly set before live AI; no unlimited default |
| Provider outage / budget exhausted | Continue code protections; queue bounded review; alert moderators |

Do not blindly truncate evidence to meet a cap. Mark insufficient context or unsupported media and route it for review. Token efficiency is not a reason to declare unreviewed content safe.

Caching savings vary by model and provider; stable text alone may not guarantee reuse. Cache charges, thresholds, and quota accounting must be measured. If using subscription authentication, API dollar prices may not describe the actual subscription allowance. [Current prompt caching guidance](https://developers.openai.com/api/docs/guides/prompt-caching)

**Expected outcome:** compact, measurable AI decisions under explicit budgets.

**Exit check:** a known fixture's result and usage can be inspected; errors and exhausted budgets never trigger blind enforcement; routine logging adds no inference call.

## Phase 7 — Evaluate in the test channel with report-only behavior

**Responsible:** Daniel and selected moderators.

**Steps**

1. Set only the private test chat to `report_only`. The bot may report to the private moderator/log destination; it does not delete member messages or warn them publicly in this mode.
2. Run the fixture matrix below, including ordinary user identities, edits, concurrent messages, and restart cases.
3. Label expected outcomes with moderators. Start with 30–50 varied cases for iteration, then expand to resolve concrete weaknesses. This is a starting test set, not evidence of production-wide accuracy.
4. Compare predicted flags with moderator decisions. Record false positives, missed violations, abstentions, and coverage gaps by rule.
5. Review actual token use, cache hits, escalation rate, queue delay, and cost/allowance consumption per 1,000 eligible messages.
6. Turn private log posting off and on. Confirm stored incidents remain available and toggling logs does not add model requests.
7. Test direct command handling separately from free-form private requests. Verify the latter receives only relevant evidence.

Evaluate allowed and prohibited cases together. A system can reduce token use simply by missing more violations; that is not an acceptable success criterion. [Anthropic evaluation guidance, January 2026](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

**Expected outcome:** a test report that shows quality, coverage, token use, and operational behavior.

**Exit check:** zero unauthorized/out-of-scope actions; all routing and restart checks pass; moderators agree on initial quality targets and remaining limitations before action testing.

## Phase 8 — Test limited enforcement inside the test channel

**Responsible:** owner grants the additional channel permission; Daniel operates; moderators supervise.

**Steps**

1. Present Phase 7 results and request Manage Messages only in the designated test chat. Keep the permission absent in moderator, log, and production channels.
2. Enable an explicit test-only enforcement mode and a narrow action list: approved templated warnings and single-message deletion. Start with human approval for each action.
3. Use purpose-created test messages. Verify action targets cannot be replaced with a production channel/message ID or another guild.
4. Test a command to approve an incident; verify the actual approving user's current authority and evidence version.
5. Test edits between review and execution, deletion by another moderator, permission removal, Discord API timeout, restart, and log failure.
6. Confirm protected-role behavior explicitly in the harness. A lower bot role does not inherently protect a moderator's messages from deletion.
7. Exercise pause/resume and the independent service-stop procedure. Return to report-only after the test session.

**Expected outcome:** real action behavior demonstrated only on deliberate test targets.

**Exit check:** every action has an accurate durable outcome; rejected actions remain rejected; no repeat action after restart. Message deletion cannot restore the original message ID, so do not call rollback a way to undo deletion.

## Phase 9 — Obtain production-channel access and observe only

**Responsible:** owner/moderation lead authorizes the channel list; Daniel applies the corresponding deployment change.

**Steps**

1. Share the test report, exact production channel IDs, proposed rules, data/model handling, token budget, and pause procedure with the owner.
2. Request View Channel and Read Message History for one selected production channel. Keep Send Messages and Manage Messages denied there while observation is underway, unless a specific exception is approved.
3. Add that channel to the plugin's monitored list in `report_only` mode. Keep the private command-channel configuration unchanged.
4. Confirm genuine unmentioned member messages and edits reach the classifier without opening public conversational access.
5. Observe a representative volume and time period agreed with moderators. Review a sample of both flagged and allowed messages; flagged-only review cannot estimate missed violations.
6. Tune rules and budgets from measured data. If enabling native Discord AutoMod later, coordinate its actions with this harness to avoid duplicate enforcement. [Discord AutoMod](https://docs.discord.com/developers/resources/auto-moderation)
7. Add more channels one at a time only after the owner authorizes each expansion and the previous scope meets the agreed criteria.

**Expected outcome:** real traffic measurements with no automated public enforcement.

**Exit check:** owner accepts the results and explicitly approves any next enforcement rules/channels. Test success or read access alone does not authorize deletion.

## Phase 10 — Enable limited production enforcement

**Responsible:** owner/moderation lead approves; Daniel implements the approved configuration.

**Steps**

1. Record exact rule IDs, channels, actions, approval requirements, and rate limits being authorized. Begin with the clearest validated rules.
2. Have the owner grant only the necessary production channel permissions: Send Messages for warnings and Manage Messages for deletions.
3. Switch only approved channels/rules to enforcement. Leave ambiguous categories in review/report-only behavior.
4. Recheck permission and policy changes before acting. A pending approval for old evidence or an old policy must not silently authorize a new action.
5. Monitor errors, mistaken actions, event lag, token usage, and escalation. Cap actions independently of AI calls so a raid cannot generate an unbounded response stream.
6. Keep an immediate pause command in the private channel and a VPS service-stop method. The owner can also revoke bot permissions independently.
7. On regression, return the affected channel to report-only and revoke action permissions as needed. Preserve incident evidence for review.

**Expected outcome:** approved moderation behavior in a deliberately limited production scope.

**Exit check:** moderators can inspect decisions, pause actions, and identify incomplete reviews. No hidden escalation into bans, kicks, timeouts, or role changes.

## Phase 11 — Prove fresh-install recovery and document maintenance

**Responsible:** Daniel; owner assists only if Discord membership/permissions or application ownership must change.

**Steps**

1. Tag a tested plugin release and record the exact compatible Hermes revision and dependency lock. Keep installation scripts repeatable and non-secret configuration versioned.
2. Back up the database using a SQLite-consistent method. Keep an encrypted off-VPS copy of required state/configuration with a separately recoverable key.
3. Record which authentication can be restored securely and which needs a fresh login. Do not assume copying live OAuth refresh-token files creates independent credentials.
4. Restore to a fresh environment in offline/paused mode first. Install the pinned code, validate migrations, load configuration and state, and run synthetic checks.
5. Stop the old bot before enabling a replacement connection. Start the restored deployment in report-only mode and verify guild/channel permissions.
6. Reconcile pending actions with Discord before retrying. Treat uncertain API outcomes as uncertain; persist an action ledger and use delivery idempotency features where available. Do not claim perfect exactly-once delivery across crashes.
7. Bound any missed-message scan by channels, age, count, and budget. Report gaps that cannot be recovered; reconnecting alone does not prove every outage message was reviewed.
8. Re-enable enforcement only after restored configuration, evidence, and current owner authorization have been checked.
9. Document health checks, rotation, backup restore, upgrades, rollback, dependency changes, and who receives operational alerts. Rerun targeted regression cases after model, policy, adapter, or Hermes changes.

Durable records should live outside the model's context window. This supports both lower prompt costs and recovery without replaying the complete incident history through AI. [Anthropic architecture guidance, April 2026](https://www.anthropic.com/engineering/managed-agents)

**Expected outcome:** a demonstrated recovery procedure that another authorized maintainer can follow.

**Exit check:** a clean environment can restore the same bot identity, rules, scope, incident state, and usage accounting; no duplicate completed action occurs in the recovery exercise. Reinstallation alone does not require inviting the same bot to the same server again if membership remains intact.

## 5. One source of truth for configuration

Avoid manually maintaining a second copy of Hermes authentication settings. The plugin should consume the resolved host configuration through a supported interface where semantics match. If that interface is unavailable, implement one canonical deployment configuration that generates the required host/plugin views, with validation for drift.

| Configuration | Canonical owner | How the harness uses it |
| --- | --- | --- |
| Bot token | Moderation deployment's protected secrets | Shared credential reference for ingress and direct delivery |
| Model authentication | Hermes authentication/provider configuration | Host-owned inference calls |
| Private command channel IDs | Moderation profile's private conversational scope | Enforce the same scope before handling commands |
| Authorized moderator users/roles | Resolved moderation profile authorization configuration | Require authorized sender AND private command channel |
| Liberdus guild ID | Plugin/deployment configuration | Check every ingress and outbound action |
| Monitored test/production channels and modes | Plugin policy configuration | Explicit independent moderation scope |
| Log destination and posting toggle | Plugin configuration | Output-only routing; never a new AI trigger |
| Rule/action policy and budget | Versioned plugin policy | Bound inference and enforcement |
| Incidents, approvals, deduplication, usage | Durable database | Recover and query relevant facts |

The monitored-channel list is an additional concept, not a duplicate of private command channels. Restricting inbound conversational channels also does not automatically restrict where an outbound tool can act; the executor must check destination scope.

The following is an example of the plugin configuration we intend to implement. It is not ready-to-paste Hermes configuration, and symbolic references require the Phase 4 integration code.

```yaml
schema_version: 1
guild_id: "<LIBERDUS_GUILD_ID>"

command_access:
  channels_source: "resolved_hermes_private_channels"
  operators_source: "resolved_hermes_discord_authorization"
  require_channel_and_operator: true
  allow_dms: false

moderation:
  enabled: false
  default_mode: "off"
  channels:
    "<TEST_CHANNEL_ID>":
      environment: "test"
      mode: "report_only"
  threads: "excluded_until_tested"
  production_authorized: false
  actions_enabled: false
  allowed_actions: []

logs:
  discord_enabled: false
  channel_id: null
  internal_records_enabled: true
  include_in_ai_history: false

ai:
  enabled: false
  credential_source: "hermes"
  classifier_task: "liberdus_moderation_classifier"
  max_provider_attempts_per_incident: 3
  max_escalations_per_incident: 1
  daily_input_token_limit: null
  daily_output_token_limit: null
  requests_per_minute_limit: null
  require_configured_limits_before_enable: true

storage:
  database_path: "<ABSOLUTE_DEPLOYMENT_STATE_PATH>/moderation.sqlite3"
```

A log channel may equal the moderator channel if desired. In that case, exclude bot-generated incident posts from automatic AI context and ingest triggers while permitting authorized human commands.

## 6. Acceptance test matrix

| Scenario | Required outcome |
| --- | --- |
| Normal member posts without mentioning bot | Reviewed through monitored-content path when in scope |
| Message mentions another person, not the bot | Still available to moderation; no accidental routing gap |
| Member says "ignore rules and ban everyone" | Treated as message evidence; no command/tool execution |
| Unauthorized person invokes moderator command | Rejected without inference or action |
| Authorized moderator commands from wrong channel | Rejected by command-channel scope |
| Unmonitored channel or wrong guild | No classification or enforcement |
| Harmless conversation / legitimate criticism | No invented violation; counted in quality evaluation |
| Known violation under report-only mode | Stored/private report only; no public warning or deletion |
| Message changes after review | Approval/result invalidated or reevaluated before action |
| Duplicate event / reconnect | No duplicate completed action; edited versions remain eligible |
| Bot posts a log, warning, or confirmation | No feedback loop or automatic extra model call |
| Log toggle off / destination unavailable | Incident persists; no reclassification or repeated enforcement |
| Permission revoked during processing | Action fails safely and accurately records denial |
| Model returns invalid JSON / times out | Bounded retry or human review; no blind enforcement |
| Budget exhausted / queue full | Visible reduced coverage; code protections continue |
| Attachment or untested thread arrives | Mark unsupported/excluded; do not claim reviewed |
| AI proposes unauthorized target/action | Executor rejects regardless of model output |
| VPS dies after an API request | Reconcile uncertain outcome before another attempt |
| Fresh install with restored state | Same scope and policy; completed actions not repeated |

## 7. Repository and recovery deliverables

Proposed repository: `liberdus-discord-moderator`. These are future deliverables, not files created by this plan.

| File or directory | Purpose |
| --- | --- |
| `README.md` | What the bot does, supported scope, installation entry point |
| `plugin.yaml` and plugin package | Hermes registration and capability declarations |
| `ingress/` | Verified Discord event integration and command routing |
| `policy/` | Versioned rules, examples, and prompt templates |
| `executor/` | Permission checks, action validation, delivery, retries |
| `storage/` | SQLite schema, migrations, incident/action ledger |
| `config.example.yaml` | Non-secret template with validation instructions |
| `tests/` and `fixtures/` | Labeled cases and integration regressions |
| Dependency lock / compatibility record | Exact tested runtime and Hermes/plugin revisions |
| `docs/permissions.md` | Owner access matrix and approved channel IDs |
| `docs/install.md` | Verified installation and secret-entry procedure |
| `docs/recovery.md` | Clean restore, outage gaps, uncertain action handling |
| `docs/operations.md` | Pause, budget controls, logs, upgrades, rollback |

Exclude actual tokens, provider authentication files, SSH keys, and incident databases from Git. A plugin installer cannot grant Discord permissions or recover credentials that were never backed up.

## 8. Working with Hermes one phase at a time

Give Hermes this plan and the non-secret inventory. Enter credentials through local protected configuration. Start with the phase appropriate to your actual access; mark owner-dependent work blocked if permission has not been granted.

Suggested instruction to Hermes:

```text
Use liberdus-discord-moderator-plan.md as the project plan.

Work on Phase <NUMBER> only. First inspect the actual installed Hermes version,
existing configuration, and completed phase evidence relevant to this phase.
I already have Hermes installed on a VPS; preserve unrelated integrations.

Complete the authorized work for this phase and produce its expected outcome
and exit-check evidence. Prefer documented plugin interfaces. Do not assume
the installed Discord adapter delivers all public messages to plugin hooks.

Reuse configured credentials without printing them. Do not use a personal
Discord session or another person's login. Liberdus installation, channel,
role, and production-scope changes require the owner's actual authorization.
If that access is missing, complete useful offline work and list the exact
owner action still needed. Do not bypass the missing permission.

Preserve the private-command/public-evidence separation. Enforce budgets and
action scope in code. Do not enable production access, inference, or actions
beyond the authorized phase. If a documented feature is unavailable, record
the compatibility finding and implement the plan's supported alternative
within the authorized scope.

At the end, report: changes, verification results, versions, remaining blockers,
rollback/pause method, and the next phase's prerequisites. Update the phase
record. Do not automatically move to the next phase.
```

### Non-secret inventory to fill in

| Field | Value |
| --- | --- |
| Application owner / recovery contact | TBD |
| Liberdus owner or approving administrator | TBD |
| Bot application ID | TBD |
| Bot user ID | TBD |
| Bot installation link | TBD |
| Liberdus guild ID | TBD |
| Test category/channel IDs | TBD |
| Private command channel ID | TBD |
| Optional log channel ID / disabled | TBD |
| Authorized moderator user/role IDs | TBD |
| Protected user/role IDs | TBD |
| Proposed production channel IDs | TBD; disabled initially |
| Delegated permissions Daniel actually has | None assumed; confirm with owner |
| Token storage location, never token value | TBD |
| VPS service user / profile / deployment path | TBD |
| Hermes version/commit and install method | TBD from VPS |
| Model/provider and API vs subscription authentication | TBD from VPS |
| Daily budget and requests-per-minute cap | TBD before AI enablement |
| Incident retention and backup destination | TBD |

### Phase completion record

```text
Phase:
Status: not started / in progress / blocked / passed
Authorized by and scope:
Owner permissions confirmed:
Hermes/plugin/policy/model versions:
Files/configuration changed:
Verification evidence:
Token usage and action count, if applicable:
Remaining limitations/blockers:
Pause or rollback procedure:
Next phase prerequisites:
```

## 9. Documentation and compatibility note

The linked official documentation was checked on September 9, 2026. Hermes documentation changes quickly; the VPS version and the prototype determine which interfaces are actually available. The plan deliberately separates documented platform capabilities from the custom policy, command names, configuration schema, and budgets that we still need to implement.

Additional reference for later offline evaluations: provider batch APIs can reduce evaluation costs, but delayed batch processing should not sit in the urgent live moderation path. [Claude batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing)

---

## 10. Useful additions from existing moderation projects — September 14, 2026

**Direction:** continue with the custom Hermes moderation harness described above, using existing Discord libraries and Hermes-managed model access. Incorporate the useful features below into the relevant phases. Sentinel AI and Defender/Warden are design references and possible sources of selected reusable components; installing either complete bot is not a prerequisite.

The additions refine the existing plan. They do not enable production access, expand permitted actions, or mark any build phase complete. All proposed commands, schemas, and integrations below still require implementation and verification.

### 10.1 Features to incorporate from the code-reviewed options

| Useful feature | Existing example | How to use it in our plan | Phase / priority |
| --- | --- | --- | --- |
| Declarative moderation rules | Warden has YAML rules combining events, conditions, and actions. | Start with a small validated rule format: stable rule ID, enabled flag, matching conditions, and permitted outcome. Support export/import for backup and review. Require the executor to enforce global scope and action limits even when a rule requests something broader. Avoid implementing Warden's entire scripting language for V1. | Phase 5 — core |
| Explicit channel and role conditions | Warden implements channel/category matching and user ranks/roles. | Match Discord IDs rather than mutable names. Apply protected-role rules consistently. Keep monitored-content scope separate from command authorization; every command requires both an authorized operator and an approved private command channel. | Phases 4–5 — core |
| Counters for repeated behavior | Warden's heat system counts events within expiring windows. | Use bounded counters for repeated messages, links, and notifications. Store expiry timestamps and any counter state needed for enforcement/recovery in SQLite. Define restart behavior explicitly; Warden's temporary heat dictionaries alone do not satisfy our recovery requirement. | Phase 5 — core |
| Explainable rule simulation | Warden can simulate events and show which rule conditions passed. | Add a proposed private `/mod-explain` or equivalent command that reports scope checks, matched rule IDs, and proposed outcomes without executing them. Use separate simulation counters. Code-only explanations use zero AI calls; any optional AI simulation must be explicit and count against its evaluation budget. | Phases 5 and 7 — core |
| Moderator feedback on missed or mistaken decisions | Sentinel supports flagging a message for moderation and generating candidate patterns. | Provide an authorized message context-menu action or private incident command to record a missed violation, false positive, or allowed example. Save the evidence and policy version. AI may draft a candidate rule, but a moderator must review it and offline tests must pass before it becomes active. Public messages and dry-run classification cannot modify active policy. | Phases 6–7 — useful next step |
| Bounded context from approved rule sources | Sentinel maintains configured context channels and summaries. | Use designated policy sources and a bounded reply/recent-message window for classification. Version the approved policy snapshot. Updates become reviewable candidates rather than automatically changing live rules; exclude unrelated private discussion and the bot's own logs from classification context. | Phase 6 — core refinement |
| Private review controls | Defender includes staff-only quick-action controls. | Adapt the interaction pattern to incident review: view evidence, mark allowed, request context, or approve an already-permitted action. Bind each approval to the incident, target, evidence version, policy version, and expiry. Recheck operator access and current mode at execution. V1 controls exclude bans, kicks, timeouts, and role changes. | Phases 7–8 — useful next step |
| Internal records plus optional Discord logs | Sentinel separates database records from a configured log-channel destination. | Keep SQLite records authoritative and Discord posting optional. Send fixed private incident templates, suppress unintended mentions, and distinguish proposed, attempted, succeeded, denied, failed, and uncertain outcomes. Retrying log delivery must never repeat classification or enforcement. | Phase 5 — core refinement |
| Curated pattern examples and regression fixtures | Sentinel ships scam patterns and tests for matching; Warden tests rule parsing and conditions. | Use patterns as candidates for our labeled dataset. Include allowed discussion, quotations, scam reports, crypto conversation, and Discord setup instructions. Evaluate every adopted pattern against allowed and prohibited examples before enabling it. A pattern's supplied confidence score is not approval to delete. | Phases 5–7 — core |

These are feature-level reuse decisions. Existing host support should be reused where it fits; a component must still satisfy the plan's authorization, spending, and recovery requirements before adoption.

### 10.2 Other candidates to revisit only when they solve a specific gap

These projects were reviewed at the documentation level, not subjected to the Sentinel/Warden source probes. Treat their features as optional ideas pending a focused code review.

| Candidate | Potentially useful addition | Proposed place in this project |
| --- | --- | --- |
| [Zeppelin](https://github.com/ZeppelinBot/Zeppelin) | Incident cases, moderator notes, and configurable event logs. | Borrow the case-history interface: one incident ID, evidence, reviewer notes, decision history, and links between repeated incidents. Keep our SQLite records and permissions canonical. |
| [Modcord](https://github.com/HoneyBerries/Modcord) | Short message batches, contextual review, and appeals. | Evaluate small, bounded batches if measured traffic makes them useful. Preserve each message's identity/version and impose maximum wait, message count, and token limits. Add an appeal/review record later if moderators need it. |
| [Omnicord](https://github.com/OrygnsCode/Omnicord) | Discord operation tools, permission preflight, and previews before destructive actions. | Consider a narrowly scoped transport component only if it reduces verified integration work. Its preview-token mechanism alone does not prove approval by an authorized human. Keep our operator verification, target limits, action ledger, and single connection owner. |

A separate Red/Defender deployment remains an alternative for a simpler fixed-rule moderation project. Integrating a second full bot framework into this Hermes harness needs a concrete benefit; it is not part of the initial implementation. If an independently owned Discord bot is introduced later, document separate bot identities, permissions, and responsibility for each event/action to avoid duplicate enforcement.

### 10.3 Implementation order and acceptance additions

1. **Phase 4:** finish the zero-inference ingress/delivery prototype and verify the installed Hermes interfaces. None of the candidate projects removes this prerequisite.
2. **Phase 5:** add the small rule schema, scope/role checks, durable counters, simulation command, and structured incident/action records. Begin with a few validated rules and report-only output.
3. **Phases 6–7:** add bounded contextual classification and moderator feedback. Keep suggested rules inactive until reviewed and evaluated; record usage from every actual provider attempt.
4. **Phase 8 and later:** introduce review buttons for approved actions, then consider case notes, batching, or appeals when an observed need justifies them.

Add these cases to the existing acceptance matrix:

| Scenario | Required outcome |
| --- | --- |
| A normal setup message says “Enable Developer Mode to copy the channel ID.” | No automatic deletion merely because a prompt-injection pattern matches the phrase. |
| A member quotes a scam or reports a suspicious crypto message | Context-sensitive review distinguishes the report from promotion; uncertain cases remain for review. |
| AI proposes a new pattern during live review or a simulation | Save it only as an inactive candidate; active policy remains unchanged. |
| A rule is simulated against a real incident | Show the reasons and proposed outcome without public output, enforcement, or changes to live counters/policy. |
| A moderator uses review controls after the message, policy, or channel mode changes | Reject or invalidate the stale approval and request review of current evidence. |
| Discord refuses a deletion | Record denial, not success; preserve the incident for inspection. |
| The VPS restarts while rate counters or pending actions exist | Restore required state and expiry times; reconcile uncertain actions before retrying. |

### 10.4 Reuse provenance and review evidence

The September 14 code review inspected [Sentinel AI 1.2.3 at `c655b7d`](https://github.com/lukeocodes/sentinel-ai/tree/c655b7d1fe50254c0f32a2576af9b39a1f3f896c) and [Defender 2.1.2 at `aa6b8bb`](https://github.com/Twentysix26/x26-Cogs/tree/aa6b8bb7753d4f3c3f04b35f8f675ef02afb6faf). Ten targeted observations were reproduced with fake Discord, database, and model objects. Full upstream suites, installation compatibility, and live Discord behavior remain unverified.

Useful code references: [Warden rules and actions](https://github.com/Twentysix26/x26-Cogs/blob/aa6b8bb7753d4f3c3f04b35f8f675ef02afb6faf/defender/core/warden/rule.py), [Warden simulation command](https://github.com/Twentysix26/x26-Cogs/blob/aa6b8bb7753d4f3c3f04b35f8f675ef02afb6faf/defender/commands/stafftools.py#L787-L851), [Sentinel moderator feedback](https://github.com/lukeocodes/sentinel-ai/blob/c655b7d1fe50254c0f32a2576af9b39a1f3f896c/sentinel/commands/context_menu.py), and [Sentinel private log routing](https://github.com/lukeocodes/sentinel-ai/blob/c655b7d1fe50254c0f32a2576af9b39a1f3f896c/sentinel/services/moderation.py#L1757-L1811).

Sentinel's dry-run output can go to a private log channel. Its automatic deletion before AI review, live rule writes from model output, missing hard usage budgets, and misleading success logs are behaviors to replace, not inherit. Warden's transient heat state also needs a durable counterpart where the plan requires restart recovery.

Before copying a component, record its source, pinned commit, license, dependencies, local changes, and targeted verification. Sentinel carries an MIT license; Defender carries GPL-3.0 licensing. Preserve applicable notices and account for the selected component's license when deciding whether to copy code, integrate a package, or implement the feature independently.

Local supporting artifacts: [full comparison](/home/developer/Documents/Codex/2026-09-14/moderation-code-review/COMPARISON.md) and [offline probes](/home/developer/Documents/Codex/2026-09-14/moderation-code-review/offline_probes.py).


### 10.5 Cross-channel repeat-spam detection — September 15, 2026

**Requested behavior:** detect a member posting the same or nearly identical message across multiple channels, including a repeated promotional or scam message that looks harmless when each channel is considered separately.

**Proposed rule:** `cross_channel_repeat`. Implement the code-only detector in Phase 5 and evaluate it in report-only mode in Phase 7. This is a planned addition, not an enabled bot rule.

**Starting trigger for evaluation:** the same member posts matching content in **3 distinct monitored channels within 2 minutes** in the same server. Make the channel threshold and rolling time window configurable; tune them using labeled examples before enforcement. This identifies a pattern for review, not proof that every repeated announcement is spam.

Detection and state:

- Correlate messages across the approved monitored-channel list, keyed by server ID, author ID, and a content fingerprint. Count distinct channel IDs, not just the number of messages. Repeated posts within one channel remain covered by the existing local spam counters.
- Begin with exact matches and conservative text normalization, such as extra whitespace and capitalization in prose. Preserve original evidence and meaningful URL differences. Add tested near-duplicate matching for small wording or formatting changes; a shared domain, common link, or short greeting alone is insufficient evidence.
- Store contributing message IDs, channel IDs, original timestamps, current evidence versions, fingerprints, and expiry times in SQLite. Bound the window and retained state. Replayed events must not increase counts; edits replace the message's previous contribution rather than counting as another post. Use the original posting timestamp for window membership so edits cannot refresh an old post indefinitely.
- Observe only channels the deployment is authorized and able to monitor. Exclude private moderator/log channels and the bot's own output. Keep other-bot/webhook coverage explicit. During the pilot, use approved test channels or synthetic events; this rule does not grant access to every server channel. Reports must state the observed scope and any known coverage gaps.

Decision and response:

- Once the threshold is reached, open one incident containing the matched messages, channel links, timestamps, similarity reason, and rule/policy version. Further matches update that incident. Use a configurable notification cooldown so a spam burst does not create a private alert for every copy.
- Handle matching, counting, and fixed reports in code with zero AI calls. If context is needed to distinguish spam from a legitimate announcement, use one bounded incident-level review under the existing AI attempt and usage limits; do not classify every identical copy separately.
- Evaluate legitimate announcements, approved cross-posts, event reminders, quoted scam reports, and short common phrases. Any configured exception must have an explicit scope, such as approved sender, channels, and announcement pattern; it exempts only this rule. Uncertain cases stay with moderators.
- Start with internal records and a private moderator report, with optional private log posting. Report-only mode sends no public warning and deletes nothing.
- If enforcement is authorized later, use only the existing permitted warning/single-message deletion path. Recheck each target's channel mode, current evidence, permissions, protected-role policy, rate limits, and required approval. A grouped incident is not permission to purge other channels. An edit affecting the pattern must trigger reevaluation and invalidate affected approvals. Bans, kicks, timeouts, and role changes remain outside V1.

Acceptance additions:

| Scenario | Required outcome |
| --- | --- |
| One member posts the same promotional message in 3 monitored channels within 2 minutes | One private review incident lists all observed copies; report-only mode takes no public action. |
| The same text appears with extra spaces or minor tested variations | Matching groups the relevant copies and preserves the original evidence; uncertain similarity stays for review. |
| One member posts 3 copies in only 1 channel | Cross-channel threshold is not met; the local spam rule may still apply. |
| Different members post a common greeting or link | Do not combine them into this same-author rule. Coordinated multi-account spam needs a separate design. |
| An explicitly approved announcement is cross-posted | Apply the scoped exception and record the reason; unrelated moderation rules still apply. |
| Copies are outside the rolling window or in unmonitored channels | They do not contribute; reports do not claim those channels were reviewed. |
| Discord repeats an event, a message is edited, or the VPS restarts | Restore unexpired state, deduplicate IDs, update the pattern evidence, and avoid duplicate incidents/actions. |
| An incident spans channels with different modes or permissions | Validate each proposed target independently; no action in a report-only or unauthorized channel. |


### 10.6 Optional JEV classifier — discussion proposal, September 20, 2026

**Status:** researched and proposed; not implemented, configured, installed, or enabled. The operator paused the pending plugin installation to discuss this addition. The existing installer remains the code-only pilot. This proposal extends Phase 6 and does not bypass the Phase 4 live transport acceptance tests.

**Recommendation:** add JEV behind a default-off classifier mode, evaluate it in shadow mode, and use measured results to decide whether it should annotate private moderation reports. Keep Discord collection, authorization, repeat counting, timestamps, budgets, and all action decisions in code. Hermes remains the host agent framework; JEV would be a separate hosted AI provider used by this plugin, not a second Discord bot or a replacement gateway.

#### Verified product facts and limits

- TypeSafe AI's official JEV API accepts input state and typed questions at `https://api.typesafe.ai/v1/systemone`. Choice selects among defined labels, Score rates an ordered descriptive rubric, and Noul returns a yes/no probability. A normal generative model can also return structured output; JEV's potential benefit here is the cost/latency of narrowly scoped judgments. [Official introduction](https://docs.typesafe.ai/introduction), [API reference](https://docs.typesafe.ai/api)
- The listed model is `jev-1.13.0`, priced at **$0.042 per million input tokens**, with output tokens free. Pin the model rather than the moving `jev-latest` alias. Example estimate: 100,000 evaluations at 1,000 total input tokens each costs $4.20 at that rate, before retries, gateway markup, or other model calls. This is arithmetic from the published price, not a usage measurement. Our present code-only pilot has zero model cost; JEV adds cost to it and may reduce future generative-review costs. [Official model reference](https://docs.typesafe.ai/models)
- The vendor describes 70–500 ms end-to-end latency; this is not a guarantee of under 150 ms from our VPS. Measure actual median/p95 latency, failures, and queue delay. [Vendor launch report](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- Choice/Score confidence is derived from the returned probability distribution; `confidence = 0.98` is not a measured 98% accuracy on Liberdus messages. Noul has no separate confidence field. Score is a probability-weighted position on a zero-based rubric and can be fractional, so do not copy a `score === 5` branch from an illustrative example. Validate all returned data and test policy-specific thresholds. [Confidence](https://docs.typesafe.ai/confidence), [Score semantics](https://docs.typesafe.ai/primitives/score)
- The vendor documents literal interpretation, numerical/counting errors, irrelevant-context sensitivity, and adversarial-content failures. Structured output prevents some format errors; it does not make classification immune to manipulation or mistakes. [Known limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- This integration would send selected content to a hosted provider. TypeSafe's policy says inputs are not used for training/fine-tuning, but it describes input collection and retention; do not assume zero retention. Confirm the chosen account/provider's applicable handling before sending Discord content. Use the official provider or a deliberately selected gateway, not an unreviewed community proxy. [Privacy policy](https://typesafe.ai/legal/privacy-policy)

#### Proposed responsibilities

| Component | Responsibility |
| --- | --- |
| Existing code rules | Count repeated messages/channels and windows; detect configured domains; enforce scope, authorization, pause state, and delivery checks. No model needed. |
| Optional JEV adapter | Judge bounded semantic questions: apparent solicitation, quoted scam reporting, harassment context, or promotional intent. Return probabilities and labels as review evidence. It cannot establish whether a URL is actually malicious without additional evidence. |
| Moderators | Review ambiguous/disputed cases and label evaluation examples. Initially all decisions remain report-only. |
| Future optional generative reviewer | Explain difficult cases using a separate bounded, tool-free request if later selected. JEV must not automatically launch a general Hermes agent with tools. |

Proposed flow, keeping the existing code report path independent:

```text
Approved Discord test channels
              |
       Scope + code rules
              |
       Existing incident
          /         \
 Fixed private     JEV mode gate
 code report            |
                  Bounded queue
                        |
                  JEV evaluation
                        |
               Save typed result
                /             \
          shadow mode      report_only
          local record     private note
```

Start with one deduplicated evaluation per selected incident revision, not one per repeated copy. The first experiment can compare solicitation with a legitimate announcement or a quoted scam warning. If we evaluate only existing incidents, JEV cannot discover semantic problems the code never selected. A separate, bounded sample of other approved test-channel messages is an option for measuring that gap; broader screening is a later scope decision.

#### Proposed feature flag and rollout

Use one mode flag instead of several overlapping enable switches:

| Proposed `classifier.mode` | Behavior |
| --- | --- |
| `off` — default | Existing behavior; zero classifier calls, no provider client/key lookup, and no content sent to JEV. |
| `shadow` | Evaluate explicitly selected inputs and record results/usage locally; do not alter, suppress, or add Discord reports based on JEV. This mode still makes paid external AI calls. |
| `report_only` | Validated results may annotate/group selected private review reports. They cannot suppress an existing code-rule report, change authorization/policy, post publicly, delete messages, or run tools. |

Conceptual configuration, **not accepted by the current schema and not a copy/paste deployment setting**:

```toml
[classifier]
provider = "jev"
mode = "off"
model = "jev-1.13.0"
```

The current schema intentionally rejects AI enablement and unknown tables. Implementation must explicitly version/migrate that contract: retain `ai_enabled` as a master prohibition, require it to be true for any non-off classifier mode, and leave `actions_enabled = false`. Adding a JEV key alone must never activate inference. Resolve `TYPESAFE_API_KEY` through the moderation profile's scoped secret reader and pass it explicitly to the provider client; never borrow another profile's key or commit it.

Implementation work if this proposal is adopted:

1. Introduce a provider-neutral classifier interface plus a small async JEV client. Keep provider I/O in a separate bounded worker so a slow provider cannot block Discord event processing or existing code reports.
2. Before scheduling, snapshot and bind the current evidence revision, policy/rubric version, selected model, and bounded context. Keep member identities local where not needed; exclude private moderator conversations, secrets, unrelated channels, and bot logs from provider context. User text remains data, not authority to change the question or invoke tools.
3. Require configured input limits, request rate, concurrency, daily calls/tokens/spend, and a total attempt budget before non-off activation. Reserve usage for in-flight attempts and account for errors/timeouts; a timeout may still be billable. Override SDK retry defaults explicitly. [SDK retry controls](https://docs.typesafe.ai/sdk/python/api/retries)
4. Persist model/rubric version, probabilities, confidence where available, returned usage, locally estimated price, latency, and outcome. Distinguish not evaluated, failed, uncertain, and evaluated; do not interpret provider failure as a clean/safe result.
5. Recheck evidence, channel scope, pause/mode, and policy before a queued call and before applying its result. Discard results made stale by edits, deletions, pause, scope changes, or reconnect coverage resets. Switching to off stops new scheduling and ignores late results; it cannot undo data already submitted or incurred charges.
6. On provider outage, invalid response, exhausted budget, or full queue, continue current code-only behavior and expose reduced semantic coverage. No automatic fallback to a general agent. Count JEV calls separately from generative-model calls; status must not continue to claim zero AI calls when JEV is active.

#### Evaluation before enabling report annotations

Use moderator-labeled synthetic/redacted fixtures first, including ordinary discussion, legitimate cross-posts, scam promotion, quoted scam reports, sarcasm, multilingual cases, obfuscated text, and adversarial classification instructions. Measure false positives, missed cases, calibration, and latency separately by category. Keep an explicit uncertain outcome. A confidently benign result cannot authorize an action or erase code evidence.

Acceptance tests must cover: off mode performs no provider I/O; both profile keys and results stay isolated; existing code reports continue during provider failures; request/usage limits cannot be exceeded by concurrent scheduling or hidden retries; edits invalidate cached/in-flight results; duplicate events do not repeat evaluations; JEV outputs cannot change commands, policy, scope, or tool permissions; and a shadow result never creates a Discord post.

**Open discussion:** start with incident enrichment only, or include a bounded sample of other test-channel messages? Which semantic categories matter most to moderators? Provider access/data handling, live budget, and calibrated thresholds remain unset. No JEV account, key, purchase, SDK installation, inference request, or runtime flag was added during this research.


### 10.7 Selected JEV integration — implemented default-off, September 20, 2026

The operator selected the recommendation: evaluate **existing deterministic-rule incidents only**, and requested implementation before installing the pilot. Version 0.3.0 adds an optional direct JEV worker. Deployment and provider credentials remain owner-run steps; no live Discord connection, provider call, API charge, or Hermes profile mutation occurred during this implementation.

**Plugin/MCP review was completed before code changes.** The official TypeSafe skill is API-development guidance, useful while building the repository. Community MCPs `itsmostafa/typesafe-mcp` and `y0usaf/typesafe-mcp` expose evaluation tools for conversational agents. Hermes supports profile-scoped MCP tools, but our dedicated moderation adapter never enters that loop. Installing either MCP in `liberdus-mod` would not wire it into incident processing. We selected a direct async HTTP client using Hermes's existing aiohttp library, with no new MCP, SDK, skill, or background process in the profile. Research and source links are documented in the repository's `docs/jev.md`.

Implemented flow:

```text
Discord test messages
         |
Scope + deterministic rules
         |
   Existing incident
      /       \
     v         v
bot-mod     JEV flag on?
report          |
          Bound + budget
                |
           TypeSafe API
                |
          Local shadow
           result only
```

- Default is **off**: no TypeSafe client, key lookup, or API call. The installer uses schema 2 with an explicit disabled classifier table and zero budgets. Legacy schema 1 stays code-only and keeps its original policy hashes.
- Supported opt-in is **shadow**: requires `ai_enabled = true`, `classifier.mode = "shadow"`, and positive finite limits. JEV classifies apparent message purpose as promotion, announcement, quoted warning, other, or unclear. It cannot establish whether a crosspost was authorized or change a rule's decision. Classifier `report_only` annotations from the earlier proposal remain future work and are rejected today.
- One async worker, at most one provider attempt per incident across copies, revisions and restarts. Strict typed-response validation; model `jev-1.13.0` pinned; no automatic retries, redirects, general Hermes inference, or fallback tools.
- Only bounded incident text and counts leave the VPS. Metadata identities and surrounding/private conversations are omitted; embedded Discord IDs are redacted. Free-form text can still identify people. An explicit shadow opt-in sends that text to TypeSafe.
- Before and after a call, verify the incident revision, evidence hash, policy, scope, pause state, connection and expiry. Discard stale judgments after edits/deletes/gaps/config changes, retain valid usage, and leave deterministic private reports unchanged.
- Local SQLite audit records include model/rubric/policy bindings, result probabilities and confidence, token usage, estimated cost, latency and fixed error outcomes. Unknown outcomes are retained and never automatically retried. Incident retention also removes associated results; lifetime budget counters remain.
- Default shadow setup caps: $0.05/day and $0.25 total, plus 100/day and 1,000 total attempt ceilings. Every attempt reserves $0.002753 (65,536 input tokens to cover the documented 64k model context), including failures, with no refund. This permits at most 18 attempts/day and 90 total under those local accounting limits. Actual token charges may be lower; these local caps do not guarantee the provider invoice. A reported usage overrun latches evaluation off for review.
- Input cap 12,000 UTF-8 request bytes / 32 evidence messages; queue 20; concurrency one; timeout three seconds; minimum interval six seconds. Limits can skip evaluation, so no complete AI coverage is claimed.

**Next steps:** install the disabled pilot and verify the original live code-only test. Obtain a TypeSafe API key from its console, store it with the hidden-prompt helper in the `liberdus-mod` profile, and explicitly enable shadow mode after checking account billing/data handling. The helper performs a protected schema migration without restarting anything. Owner instructions are in `docs/jev.md`; staged artifacts are `/tmp/liberdus-install-pilot-20260920.pyz` and `/tmp/liberdus-jev-20260920.pyz`. Stock Discord stays disabled in both profiles.

Offline automated tests cover default-off isolation, schema migration, response validation, budgets and restarts, failures, stale evidence, setup helpers and unaffected private-report delivery while JEV waits. They do not establish JEV moderation accuracy or complete live Discord acceptance. Evaluate labeled spam, legitimate announcements, quoted warnings, multilingual messages and adversarial text before considering report annotations. Broader scanning, feedback learning and enforcement remain separate future work.


### 10.8 Live code-only baseline passed; JEV deferred — September 20, 2026

**Operator decision:** TypeSafe access is not available yet. Continue the Discord moderation pilot without JEV and return to JEV later. Keep `ai_enabled = false`, `classifier.mode = "off"`, and `actions_enabled = false`; the classifier budgets remain zero in the staged pilot policy. Do not run the JEV key/shadow setup steps during this stage. Later account access does not authorize automatic activation. No configuration change or service restart is needed to preserve the already-off state.

**Operator-supplied live evidence:**

- The owner ran the installer successfully (`installed: true`, installed-runtime import passed), enabled only `platforms.liberdus_moderator`, and restarted the shared gateway to PID `876774` at that checkpoint. This PID is historical evidence, not a permanent service identifier.
- `!mod status` in `bot-mod` returned `report_only`, `Connected: True`, `JEV: off / off`, `AI attempts: 0`, and enforcement disabled.
- Repeating qualifying text across the three test channels produced incident `1ce20048a42442bc92784aaaa9184d69`, revision 1, rule `cross_channel_repeat`, author `977263877391794217`, and three observed copies, with a private report linking the three test channels. This confirms the basic message collection → rule match → private report path in the installed pilot. It does not complete all live acceptance checks.
- The earlier phrase `testing out the bot` has 19 characters, below the repeat rule's 20-character minimum. Use substantive text of at least 20 characters for repeat tests. Short received messages still count; the earlier zero message count was not explained by that length threshold. The subsequent fresh test succeeded.

**Next work, with JEV off and monitoring restricted to the existing test channels:**

1. Verify `!mod pause` and `!mod resume` from `bot-mod`. While paused, a fresh three-channel pattern must not create a new report. After resume, post a different fresh pattern; pre-resume messages are not backfilled. Wait for each command reply before continuing.
2. Verify same-channel repetition: post identical substantive text four times in one test channel within 30 seconds. Expect one `same_channel_repeat` incident/report. Use a new phrase for each test so existing grouping/cooldowns do not confuse the result.
3. Verify current evidence: create a fresh three-channel incident, edit one copy to different text while the 120-second window is still active, and inspect that new incident ID. Expect withdrawal when fewer than three matching channels remain (or expiry if the window elapsed). Deleting a test message triggers a coverage reset; already-posted reports stay as historical snapshots.
4. Verify command boundaries using existing approved access: an operator's `!mod pause` in a test channel must not pause the bot; a non-operator's command in `bot-mod` must not change state or receive a privileged reply; DMs must not open a conversation. Do not widen channel access or use public/committee channels for these tests.
5. Verify restart behavior during a quiet test period: pause, restart the shared gateway, and check that pause persists and old reports are not replayed; then resume and verify a new pattern. A shared gateway restart can briefly reconnect Telegram. A changed coverage-reset count is expected; this is not historical catch-up.
6. Review report clarity, duplicate handling and false positives during a short observation period in the approved test channels. Tune repeat thresholds and narrowly scoped approved-crosspost exceptions only from observed cases. Record results before considering a separately scoped production-channel pilot.

The detailed owner-run checks and expected outputs are in repository `docs/live-pilot.md`. JEV activation, broader semantic scanning, general Hermes chat, and enforcement remain separate later work. This checkpoint records the user's supplied output; no Discord messages, profile changes, provider calls, or restarts were performed while updating the documentation.


### 10.9 Pause/resume command results — September 20, 2026

The operator supplied this live sequence from `bot-mod`:

- `!mod pause` returned `paused`, `Connected: True`, zero working messages, one retained incident, and no pending or uncertain reports.
- `!mod resume` returned `report_only`, still connected, with zero working messages and the same retained incident before new detection.
- A subsequent private report recorded incident `715a93e021a74959a6e812bfa9ed256a`, revision 1, `cross_channel_repeat`, author `977263877391794217`, and three observed copies linked to the three approved test channels.
- Both command responses showed `JEV: off / off`, `AI attempts: 0`, and enforcement disabled. Coverage resets stayed at 2 with `reconnect` as the last recorded gap.

**Confirmed from the supplied output:** authorized pause/resume state transitions and successful reporting after resume. Zero working messages at the transitions and one retained incident are expected: pause/resume resets current detection evidence while keeping incident history. Manual pause/resume does not increment the transport coverage-gap counter.

No report appears between the two commands in the supplied excerpt. The test-channel posts made during the paused interval were not included, so suppression of a deliberately posted paused pattern is not separately confirmed by this excerpt. Keep that distinction in the acceptance record; it does not block proceeding with the other independent tests.

**Next test:** post `Liberdus single-channel spam test.` four times in `bot-test-1` within 30 seconds, using the same account and plain text. Expect one new private report with `Rule: same_channel_repeat` and four observed copies. JEV remains deferred/off. Remaining cases include edits/deletes, authorization boundaries, and restart recovery. This update records operator evidence only; no profile changes, restart, Discord posts or provider calls were made.


### 10.10 Single-channel repetition passed — September 20, 2026

The operator supplied a private report for incident `9b8820010d384c94ac575a2c97a31ce1`, revision 1, with `Rule: same_channel_repeat`, author `977263877391794217`, four observed copies and four evidence links in `bot-test-1`. This matches the instructed four-copy, single-channel test and confirms its detection/private-report path from operator evidence. The report states report-only with no public action. JEV remains deferred/off; no configuration or runtime change was made while recording this result.

**Next test: edited evidence.** Use a fresh three-channel incident to have the 120-second cross-channel window available. Post `Liberdus message editing test.` once in each of `bot-test-1`, `bot-test-2` and `bot-test-3`. After the new private report arrives, promptly edit the copy in `bot-test-3` to `This message has been corrected.`. Complete the edit within 120 seconds of the first test post, then allow a few seconds for processing. In `bot-mod`, run `!mod incident NEW_ID`, replacing `NEW_ID` with this new report's incident ID. Expect `State: withdrawn` because only two channels still contain matching text. If the incident expired before the edit, `expired` does not prove edit handling; repeat with a fresh phrase. Already-posted reports remain historical snapshots and are not edited or deleted by this plugin.

The live edit test remains pending. Deletion handling, command authorization, restart recovery and explicit paused-pattern suppression confirmation also remain pending. These test instructions do not expand the approved channel scope or enable AI/enforcement.

### 10.11 Edited evidence withdrawal passed — September 20, 2026

The operator corrected the earlier pasted report with the current incident lookup:

```text
Incident 0e4677d84316465bb19dbe2c788bb283 | revision 2
Rule: cross_channel_repeat | State: withdrawn
Author ID: 977263877391794217 | Evidence items: 3
Saved code-rule evidence; enforcement disabled. Reports are snapshots and may be superseded.
```

This is the expected outcome of the instructed edit test: changing one of the three matching copies leaves fewer than three matching channels, so the incident advances to revision 2 and is withdrawn. Mark this live edit-withdrawal case passed from operator evidence. `Evidence items: 3` preserves the last triggering evidence snapshot for audit history; it does not mean three current messages still match. The original Discord report remains a historical snapshot.

**Next test: deletion handling.** In `bot-mod`, run `!mod status` and note `Coverage resets`. Post `Liberdus deletion handling test.` once in `bot-test-1`, wait a few seconds, then delete that disposable test-channel message. Wait a few seconds and run `!mod status` again in `bot-mod`, without posting other test messages in between. Expect the reset count to increase, `Last: deleted_message`, and `Messages: 0`. The bot should remain connected and in `report_only`, with JEV off, zero AI attempts and enforcement disabled. A new spam report is not required for this test.

The current implementation conservatively clears the whole working detection window on a monitored-channel deletion, cancels pending reports and marks open incidents `needs_revalidation`. It retains incident history and delivered reports. This already-withdrawn edit incident is not reopened or revised by that reset. Deletion handling remains pending until live output is supplied; authorization, restart recovery and explicit paused-pattern suppression confirmation also remain pending. JEV stays deferred/off. This checkpoint changes documentation only.

### 10.12 Private automated self-test — September 20, 2026

The operator asked whether the moderator bot could perform the remaining tests. Its own output and other bot/webhook messages are excluded, and sending is denied in the test channels, so having it post to itself would not exercise ordinary member-message detection. Version 0.3.1 adds an authorized private `!mod selftest` command to reduce repeated manual logic checks while preserving those boundaries.

The command runs nine fixed cases through the real moderation core and command handlers: cross-channel and same-channel repetition, edit withdrawal, deletion reset logic, pause/resume suppression, command authorization, bot/scope exclusions, duplicate delivery prevention, and simulated session recovery. Each case uses a separate in-memory database, synthetic IDs/text, a fixed clock and the default test policy. The result is one private pass/fail summary. It accepts no user-supplied fixtures, does not use the live policy or evidence, makes no AI/provider calls, and takes no public action. Live pause state and incidents are preserved; the normal command receipt is the only live database write from the command itself.

**Coverage distinction:** these are synthetic checks, including simulated deletion and session recreation. They do not verify real Discord events, current permissions/intents, DM behavior, disk durability or an actual gateway restart. Earlier operator-confirmed live successes remain recorded; pending live cases remain pending. Do not disable bot filters or automate a member account to make the moderator test itself.

**Deployment:** source and owner-run update instructions are in repository `docs/selftest.md`. The staged `/tmp/liberdus-update-selftest-20260920.pyz` updates only a disabled, stopped 0.3.0 pilot to 0.3.1, verifies the pinned runtime and staged self-test, and retains the previous plugin directory for rollback. It preserves configuration, policy, credentials and SQLite files. The owner must disable the custom platform and restart, run the updater, then re-enable and restart before using `!mod selftest` in `bot-mod`. Shared restarts may briefly reconnect Telegram and start fresh coverage windows. No live profile update, gateway restart or Discord post was performed while implementing this feature. JEV remains deferred/off.

The nine checks pass locally. Automated regression checks cover authorization, temporary-state isolation, paused live-state preservation, failed-check reporting, duplicate commands and private adapter delivery, plus updater refusal while active, blocked-network import validation, profile preservation and rollback after a publish failure. These checks do not claim live deployment of version 0.3.1.

### 10.13 Later live checks and authorized JEV shadow trial — September 20, 2026

**Current operator decision:** TypeSafe access and an API key are now available, and the operator explicitly chose to try the recommended small JEV shadow trial. This supersedes section 10.8's deferral. Activation and the first successful provider evaluation remain pending; the last supplied live status still shows JEV off, zero AI attempts and enforcement disabled. The existing 0.3.1 pilot already contains the JEV implementation, so no plugin reinstall or additional MCP/SDK is needed.

**Additional operator evidence:**

- The installed `!mod selftest` command returned 9/9 passed, with zero self-test AI calls and public actions. These remain synthetic checks, not live provider/transport tests.
- A monitored-message deletion increased coverage resets from 4 to 5 with `Last: deleted_message`; working messages were zero, four historical incidents remained, and the bot stayed connected/report-only.
- The operator confirmed a test-channel `!mod pause` did not pause the bot, and a DM received no response. Authorized status remained report-only.
- Following the instructed paused gateway restart, status showed `paused`, connected, four retained incidents and no pending/uncertain reports. Resume returned report-only and produced fresh cross-channel incident `dfc2c81d0c584ef6b04c9bb33713f04c`, revision 1, with three copies. This confirms pause persistence and resumed detection across an actual restart.
- The live command test from another unauthorized member was explicitly deferred. Unobserved cases remain identified in repository `docs/live-pilot.md`; do not repeat completed tests or claim exhaustive live acceptance.

**Trial scope and flow:** keep the same Liberdus guild, three private test channels, private `bot-mod` channel and operator allowlist. Enable only existing-incident shadow evaluation: code rules still decide which incidents exist and send their usual private reports; JEV classifies apparent purpose into local records. Selected incident text is sent to TypeSafe. JEV does not annotate/suppress Discord reports, scan unselected messages, launch Hermes tools or enforce actions. Installation defaults remain off and `actions_enabled` stays false.

**Owner-run activation:** as `hermes`, disable the custom moderation platform and wait for the shared gateway restart to complete; keep stock Discord disabled in both profiles. Run `/tmp/liberdus-jev-20260920.pyz key` via Python and enter the TypeSafe key at its hidden prompt. After key setup succeeds, run the same helper's `shadow` operation with `--daily-microusd 50000 --total-microusd 250000`, then re-enable the custom platform and restart. These are $0.05/day and $0.25 total local accounting limits, with conservative nonrefundable reservations, not a provider invoice guarantee. Setup commands themselves make no provider call. The complete copyable steps and rollback procedure are in repository `docs/jev.md`.

**First evaluation:** from the human operator account, post one fresh fictional announcement identically across the three test channels within 120 seconds. Wait for the normal rule report and JEV processing before changing the messages. Use the helper's read-only `results` operation to match the incident ID and confirm `outcome: ok` plus the typed label/probabilities/usage. `shadow / ready` and an increased attempt counter alone do not prove API success. After the first successful call, compare one promotional example and one quoted-warning example, separately. Bot posts and `!mod selftest` cannot exercise JEV; they remain excluded/offline respectively. Labels are comparison data, not proof of abuse or authorization.

The setup helper's relevant packaged source matches the repository. This checkpoint and runbook preparation did not access the real profile/key, activate JEV, call TypeSafe, restart the gateway or send Discord messages. Record the owner-run status and first result before claiming the integration works live. Broader scanning, report annotations, production channels and enforcement remain future decisions.

### 10.14 First live JEV shadow evaluation passed — September 20, 2026

The operator supplied a connected `report_only` status with `JEV: shadow / ready` and enforcement disabled, followed by a three-channel repeat report and its matching local JEV result. This supplies the activation and first-provider-success evidence pending in section 10.13.

- Incident `9a7c69df8eb7447188e7ebfd09d2209b`, revision 1, rule `cross_channel_repeat`, author `977263877391794217`, three copies in the approved test channels. The private report still states report-only with no public action.
- Provider outcome `ok`, model `jev-1.13.0`, purpose `announcement`, model confidence 0.98. Probabilities: announcement 0.98, quoted warning 0.02, other/promotion/unclear 0.0. This matches the announcement comparison; confidence is not measured accuracy.
- Recorded latency 275 ms, usage 543 input tokens and 54 output tokens. Local estimated cost 23 microusd ($0.000023). Lifetime accounting records one attempt and 2,753 microusd ($0.002753) conservatively reserved against local caps, with worker state `ok`. Reservation and estimate are distinct; neither authenticates a provider invoice.

The earlier zero-attempt status was the snapshot before evaluation; the subsequent result records the completed first call. `Coverage: code only` remains the fixed incident-detection label: code rules select incidents and the separate shadow worker classifies them afterward. The matching successful record confirms that the configured credentials were accepted for this request, the client accepted the typed response, and the result was persisted with the incident. This single example does not establish general classification accuracy, calibrated confidence, guaranteed latency or broader message coverage.

**Next test:** as the human operator, post `JEV test B: Try our new premium plan today and use code DEMO for a discount. Sign up now!` identically once in each of `bot-test-1`, `bot-test-2` and `bot-test-3` within 120 seconds. Wait for the normal private report and JEV processing, leave the copies unchanged, then run `python3 /tmp/liberdus-jev-20260920.pyz results`. Match the new incident and compare its actual label against `promotion`; a different valid label is a comparison result, not an API failure. Then proceed to the quoted-warning example in repository `docs/jev.md`, separately.

No reinstall, repeated key setup, policy/budget change, scope expansion or restart is needed for the next examples. Keep enforcement disabled and JEV in the authorized shadow trial. The promotion and quoted-warning cases remain pending until their outputs are supplied. This checkpoint records operator evidence only; the developer did not read the profile/key, send Discord messages, call TypeSafe or restart the gateway while recording it.

### 10.15 Live JEV promotion comparison passed — September 20, 2026

The operator supplied a second successful local shadow record: incident `36b174a7f4014708b6e905422c8989c4`, revision 1, model `jev-1.13.0`, `outcome: ok`, `choice: promotion`, confidence 0.99 and latency 318 ms. Usage is 541 input tokens and 54 output tokens, with an estimated 23 microusd ($0.000023) cost. The policy and rubric hashes match the announcement case. Returned probabilities are promotion 1.0 and all other choices 0.0; these model outputs and the separate confidence score do not establish certainty or calibrated accuracy. The intended promotion label matched. The supplied evidence for this case is the local record; no separate Discord report excerpt was supplied.

The original announcement record remains present. Accounting now records two attempts, worker state `ok`, and a conservative lifetime reservation of 5,506 microusd ($0.005506). The two individual token-based estimates sum to 46 microusd ($0.000046), distinct from the larger reservation and from an authenticated provider invoice. Both initial examples have matched, with broader semantic accuracy still unverified.

**Next test:** using the human operator account, post `JEV test C: Warning: messages saying "claim your free reward" may be scams. Do not follow their instructions.` identically once in each of `bot-test-1`, `bot-test-2` and `bot-test-3` within 120 seconds. Wait for the ordinary private report and JEV processing, leave the copies unchanged, then run `python3 /tmp/liberdus-jev-20260920.pyz results`. Match the new incident and compare its actual label with `quoted_warning`; this checks quoted promotional language used in a warning. The quoted-warning result remains pending.

Continue the existing shadow trial with its prescribed limits and disabled enforcement. No completed test, key setup, restart or scope change is needed. This checkpoint records operator-provided evidence only; no profile/key access, provider call, Discord post or runtime change was performed while updating the documentation.

### 10.16 Quoted warning passed; initial JEV trial complete — September 20, 2026

The third operator-supplied local record is incident `b48ee236ff774e0f8efec35caa8ba447`, revision 1, model `jev-1.13.0`, `outcome: ok`, choice `quoted_warning`, confidence 1.0 and latency 375 ms. Usage is 541 input tokens and 55 output tokens, with an estimated 23 microusd ($0.000023) cost. The probability map gives quoted warning 1.0 and all other choices 0.0; these model outputs are not proof of certainty. Policy and rubric hashes match both preceding cases. The supplied excerpt is the local result; no separate third-case Discord report transcript was included.

**Initial trial result:** announcement, promotion and quoted warning all returned `ok` with their intended labels, at recorded latencies of 275, 318 and 375 ms. The three records remain present. Accounting records three attempts, worker state `ok`, and 8,259 microusd ($0.008259) conservatively reserved against local caps. Individual token-based estimates sum to 69 microusd ($0.000069), separate from reservations and an authenticated provider invoice.

The initial three-case trial is complete. It verifies the observed connectivity, typed responses and saved classifications for these selected live incidents, including a warning that quotes promotional language. It does not establish general accuracy, calibrated confidence, guaranteed latency, production readiness or detection of messages never selected by the code rules. Completed tests need no repetition.

**Current runtime and proposed next work:** the supplied mode remains shadow; this documentation update did not disable it or change the live profile. Eligible future test-scope incidents may still generate calls under existing limits. The existing stop procedure is in repository `docs/jev.md`. A recommended next improvement is an on-demand saved JEV result in the authorized private `!mod incident <id>` lookup, showing typed results and their revision/current-or-historical context without making a new provider call. This would reduce repeated SSH checks. It is a proposal for separate code work, not implemented or enabled by this trial; fixed reports and enforcement behavior remain unchanged.

Before JEV influences report selection or actions, review broader manually labeled examples and disagreements, including ordinary discussion, mixed intent, multilingual text and adversarial instructions. Broader scanning, production-channel access and enforcement remain separate decisions. No profile/key access, provider request, Discord post or gateway restart was performed while recording this operator evidence.

### 10.17 Saved JEV results in private incident replies — implemented September 20, 2026

The operator selected the proposed private lookup and requested an explanation of the next steps and JEV's role. Version **0.3.2** adds a stored JEV section to authorized `!mod incident ID` and `!mod explain ID` replies in `bot-mod`: outcome/label, model confidence, model, evaluated revision, age and current/historical evidence status. Missing, running, uncertain, failed or unsupported records receive fixed bounded responses. The lookup performs no provider request, secret access, worker startup, retry, budget change or evidence pruning. Existing command authorization, mention suppression, report generation and disabled enforcement remain intact.

**Evidence status:** current requires matching revision, evidence hash, policy, model/rubric, unexpired open status and eligible stored message versions. Expiry, edits, new revisions, pause/reset, disabled classification or changed policy/model/rubric make a stored label historical. The earlier three examples will normally be historical after the update restart; their saved labels remain inspectable. Confidence is a model score, not a moderation verdict. No arbitrary message/provider prose is copied into the JEV section.

**Deployment:** `/tmp/liberdus-update-0.3.2-20260920.pyz` is staged for the `hermes` owner. Disable the custom moderation platform and complete the shared gateway restart; keep stock Discord disabled in both profiles. Run the updater, then re-enable the custom platform and restart after success. The updater accepts 0.3.0/0.3.1, requires the process lock to be free, validates staged code in an isolated runtime, preserves `.env`, policy, SQLite and budgets, and retains the previous plugin tree. Existing shadow mode is preserved; no key setup or additional paid test is needed. The full copyable steps, rollback and known quoted-warning incident ID are in repository `docs/incident-review.md`. Deployment and a live 0.3.2 command reply remain pending owner evidence.

**Validation:** 130 core/setup tests pass on Python 3.11.16 and 3.12.3; 32 integration tests pass against the reviewed c1488 Hermes source and discord.py 2.7.1 with external network edges blocked/mocked. New checks cover authorization before lookup, no worker/provider/secret access, unchanged state/accounting, current/historical evidence, missing/malformed records, private transport rendering and shadow-preserving updates. The staged updater's packaged source matches the repository. No real profile/key was accessed, gateway restarted, Discord message sent or live provider call made during development.

**How JEV will be used:** deterministic code finds repeat/domain incidents; JEV provides apparent-purpose context for those selected incidents, helping moderators distinguish promotion, announcements, quoted warnings, other discussion and unclear cases. After verifying the private lookup using an existing saved result, review a varied, manually labeled set of rule-triggering examples and record disagreements. If useful, a later selected change can add context to automatic private reports or assist review prioritization. JEV does not currently evaluate ordinary unselected messages, prove abuse/permission, suppress reports, enforce actions or invoke the general Hermes agent. Wider scanning, production-channel access and enforcement remain separate decisions.

### 10.18 Private lookup confirmed; Discord reply layout ready — September 20, 2026

The owner supplied a working private `!mod incident b48ee236ff774e0f8efec35caa8ba447` reply from version 0.3.2. It showed incident revision 2, `needs_revalidation`, author `977263877391794217`, three evidence items, and the saved JEV `quoted_warning` result: confidence 1.00, outcome `ok`, model `jev-1.13.0`, evaluated revision 1, age 40m. Evidence was marked historical after closure/window reset and the reply stated no new AI call. This confirms the live saved-result lookup and retained evaluation; no separate before/after accounting transcript was supplied. The pending lookup status in section 10.17 is superseded.

**Requested presentation change:** version **0.3.3** formats authorized `!mod incident` and `!mod explain` replies with a bold heading, a single-column ASCII code block and full copyable IDs below. Panel lines are at most 32 characters, with aligned labels and wrapped values for a portrait phone screen. Code-rule metadata and saved JEV context have separate headings. Internal names become readable labels, such as `Needs recheck` and `Quoted warning`; `HISTORICAL`, evaluated revision and the reason remain explicit. The formatter uses Discord-supported Markdown, with no embed or permission changes. Status replies and automatic reports keep their existing presentation; classification, authorization, provider calls, policy, budgets and enforcement behavior are unchanged.

**Validation:** the existing regression suite passes: 130 core/setup tests on Python 3.11.16 and 3.12.3, plus 32 integration tests against the pinned Hermes/discord.py interfaces with external network edges blocked/mocked. Rendering assertions cover ASCII panel width, balanced code fences, preserved IDs and absence of raw message/provider text. The local example is 593 characters. Both staged bundles match the repository source. No real profile/key access, live provider request, Discord post or gateway restart was performed during implementation; a live iPhone display check remains pending.

**Owner next step:** as `hermes`, disable `platforms.liberdus_moderator.enabled`, finish the shared gateway restart, run `/tmp/liberdus-update-0.3.3-20260920.pyz`, then re-enable the custom platform and restart after success. Keep stock Discord disabled. The updater accepts 0.3.0/0.3.1/0.3.2, requires the pilot stopped, preserves existing off/shadow policy, credentials, SQLite and budgets, and keeps the previous plugin tree for rollback. Repository `docs/incident-review.md` contains copyable commands, the preview and rollback steps. Repeat the same saved incident lookup in `bot-mod` to check the narrow layout and copyable IDs. No new key or paid classification is needed. The 0.3.3 code is ready; deployment remains pending owner execution.

### 10.19 Formatted incident reply confirmed live — September 20, 2026

The owner supplied the new 0.3.3-style private reply for incident `b48ee236ff774e0f8efec35caa8ba447`. The pasted text contains the separate `CODE RULE` and `JEV - SAVED RESULT` panels, aligned fields, readable labels, wrapped reason and full incident/author IDs. It shows `Cross-channel repeat`, `Needs recheck`, incident revision 2 and three saved evidence items; JEV remains the saved `Quoted warning` result, confidence 1.00, outcome `OK`, model `jev-1.13.0`, evaluated revision 1 and age 1h. Evidence is explicitly `HISTORICAL`, with reason `Incident closed or window reset`. The footer states enforcement is disabled and the saved lookup makes no new AI call.

This confirms the live formatted incident response and retention of the earlier evaluation. Historical/recheck status is expected after the update's restart and does not indicate a failed formatting update. The supplied evidence is operator-pasted reply text; no separate deployment log, accounting delta or device screenshot was supplied. The deployment-pending status in section 10.18 is superseded for this display task. No repeat update or repetition of the three completed provider examples is needed.

**Next work:** review varied, manually labeled rule-triggering examples in the existing approved test channels and record disagreements while retaining shadow mode, spending limits and disabled enforcement. Automatic report annotations remain a later change. This checkpoint updates documentation only; no live profile, credentials, provider call, Discord post or service restart was accessed or performed by the developer.

### 10.20 Standalone JEV batch runner implemented — September 20, 2026

The operator selected the recommended batch evaluation to replace repetitive manual message posting for JEV comparisons. The context-v1 runner contains ten labeled synthetic examples: announcements, promotions, quoted warnings, ordinary/support discussion, mixed intent and adversarial instructions, including Spanish and Vietnamese text. It creates disposable three-channel incidents in memory and reuses the plugin's request builder, pinned model/rubric, transport and typed-response validator. Expected labels and test metadata never enter provider requests. No real Discord messages are read, sent or changed, and bot exclusions remain intact.

**One-command workflow:** the owner can run `python3 /tmp/liberdus-jev-batch-20260920.pyz run` from the existing `hermes` terminal. No plugin replacement, new key or gateway restart is needed. The helper uses only the profile-local TypeSafe key. `preview` is offline and profile-free; `results` is read-only and makes no key lookup/provider request. A narrow terminal summary shows expected/actual labels, match/review, confidence, latency, new attempts and cost accounting; `--json` provides typed detail and evaluation provenance. These are synthetic-input, live-provider comparisons, not live Discord event tests or a general accuracy benchmark.

**Shared limits and saved state:** each attempt atomically reserves 2,753 microusd against the same daily/lifetime counters and limits as the live classifier. Ten new attempts reserve at most 27,530 microusd ($0.027530), distinct from token-based estimates and provider billing. Batch attempts are included in the existing AI-attempt total. The separate `jev_batch_attempts_v1` table in the profile database stores expected labels, hashes, outcomes, usage and timing without adding moderation incidents or modifying live evidence/reports. A separate batch lock prevents concurrent batch runs. Policy/pause/billing guards and shared rate limits remain effective; no database transaction spans a provider request.

The default run name is `baseline`. Repeating it reuses saved attempts, including failures and uncertain outcomes; only previously unattempted cases can spend more. A deliberately new `--run-id` starts a fresh comparison under the same shared caps. No automatic retry/refund or budget reset occurs. Provider/validation failures stop that invocation, label disagreements remain review items, and calls stop when a cap or the bounded run time is reached. The existing helper instructions remain available in repository `docs/jev-batch.md`.

**Validation and remaining step:** 148 core/setup tests pass on Python 3.11.16 and 3.12.3; 35 integration tests pass with the pinned runtime and external network edges blocked/mocked. Batch-specific tests cover request reuse without expected-label leakage, shared caps in both directions, saved-run reuse, read-only results, failure/interruption accounting, policy and key isolation, unchanged live moderation state and standalone packaging. The developer has not accessed the real profile/key, called JEV, sent Discord messages or restarted the gateway. Real batch results remain pending owner execution; no additional live Discord automation was implemented.

### 10.21 JEV baseline completed; mixed-purpose case retained for review — September 20, 2026

The owner ran the standalone context-v1 baseline and supplied ten `ok` outcomes, nine expected-label matches and one review case. Community event, referral offer, quoted scam warning, support question, adversarial instructions, Spanish update, Vietnamese warning, ordinary conversation and Spanish promotion matched their initial labels. Mixed purpose returned `promotion` with displayed model score 0.61 instead of expected `unclear`. The adversarial example returned `unclear` at displayed score 0.46; neither score is a calibrated probability of correctness.

The recorded latencies were 472, 309, 275, 300, 286, 340, 299, 280, 288 and 325 ms: mean 317.4 ms, median 299.5 ms, range 275–472 ms. The summary reports ten new attempts, a $0.027530 conservative reservation and $0.000232 known token estimate. Shared accounting reports 13 calls, consistent with the three previous live incident evaluations plus this ten-case batch. The source is the operator-pasted terminal summary, not a provider invoice or full raw result export.

**Interpretation and next decision:** the mixed example contains both a maintenance announcement and an explicit unrelated sales pitch. The rubric's promotion and mixed/unclear criteria overlap. Retain the case as REVIEW and the original 9/10 baseline; no expected label, rubric, code or live policy was changed. A recommended future clarification is to prioritize an explicit endorsed sales pitch as promotion even with announcement context, while keeping quoted/negated offers in warnings distinct. If selected, evaluate that versioned change with a small targeted comparison. No full baseline rerun or installation is needed now.

This confirms the efficient live-provider batch workflow for these synthetic examples, not general accuracy, confidence calibration, prompt-injection resistance or Discord event coverage. Keep shadow mode, existing caps and disabled enforcement. The pending live-batch status in section 10.20 is superseded; automatic report annotations and a dedicated Discord test bot remain separate future work. The detailed results are in repository `docs/jev-batch.md`. This checkpoint records supplied evidence only; the developer made no provider call, Discord post, profile/key access or service restart.

### 10.22 Mixed-content precedence candidate and focused comparison — September 20, 2026

The owner selected the next step proposed after the baseline review. The candidate rubric **precedence-v2** now gives an explicit sales pitch endorsed by the author the promotion label even within an announcement or warning. Merely quoting, describing or negating an offer without endorsement does not count as promotion; warnings stay quoted_warning. Community notices without an endorsed offer stay announcement, and indeterminate context stays unclear. The untrusted-evidence and unknown-authorization instructions remain. The candidate hash is `73adf752254deaf5cbfc57b33a7411d0680cefde8a8c50106a36cbc127a7e626`; the model remains jev-1.13.0.

**Comparison scope:** the separate five-case suite reuses the original mixed notice, community event and quoted warning, and adds a warning containing an endorsed offer plus an ambiguous forwarded fragment. The revised mixed expectation is promotion only in the new suite. The original ten payload hashes, context-v1 rubric and mixed expectation remain unchanged, preserving the 9/10 baseline. This tests the selected definition, not an improvement claim based on rescoring history. The live 0.3.3 worker still selects the original rubric; adoption follows review of candidate results.

**Owner step:** `python3 /tmp/liberdus-jev-batch-v2-20260920.pyz run --suite precedence-v2`. No plugin update, gateway restart, new key or Discord posting is required. The default run name is precedence-v2. At most five new calls reserve $0.013765 under the existing shared $0.05/day and $0.25 total caps. Reusing the same command resumes unattempted cases and reuses saved attempts, including failed or uncertain ones. It never resets budgets or automatically retries paid work. Use results with the same suite to inspect history without key access or calls. The detailed runbook is repository `docs/jev-batch.md`.

**Implementation and validation:** suite selection is explicit; the CLI identifies the suite and preserves case-count-aware exit statuses. Request validation binds supported fixtures to the selected rubric before live state writes or I/O. Records retain suite, rubric/request/policy hashes and the expected label used at evaluation; mismatched provenance is unavailable without a retry. The expanded candidate request still obeys the input-size limit. Tests cover baseline preservation, no label leakage, cache separation, shared budget exhaustion and next-day resume, unchanged live evidence/worker rubric, and readonly results after disabling JEV. All 155 core/setup tests pass on Python 3.11.16 and 3.12.3; 35 pinned Hermes integration tests pass with external network edges blocked/mocked.

Real precedence-v2 provider results remain pending the owner's run. The developer used synthetic profiles and mocked provider replies; no real key/profile access, provider call, Discord message, policy change or service restart occurred. Shadow mode and disabled enforcement remain the running pilot behavior. Broader scanning, report annotations and Discord test-bot automation are still separate work.

### 10.23 Precedence-v2 comparison completed; ambiguous fragment remains under review — September 20, 2026

The owner supplied the five-case precedence-v2 summary: all five provider responses were valid, with four expected-label matches and one review case. Announcement plus offer returned promotion (displayed score 1.00, 376 ms); community event returned announcement (1.00, 285 ms); quoted scam warning returned quoted_warning (1.00, 268 ms); warning plus an endorsed offer returned promotion (1.00, 269 ms). The forwarded fragment returned promotion (0.65, 286 ms) instead of expected unclear. Preserve the candidate result as 4/5 and the original context-v1 baseline as 9/10; no label or rubric was changed.

**Recorded performance and accounting:** mean latency 296.8 ms, median 285 ms, range 268–376 ms; five new attempts; $0.013765 reserved; $0.000144 known token estimate; 18 shared calls, consistent with the previous 13 plus five. If all 18 occurred in one UTC day, $0.049554 is reserved against the $0.05 daily allowance, leaving less than one attempt's reservation. The summary does not expose daily counters or provider invoices. Code-rule moderation remains independent of JEV spending limits.

**Interpretation:** the unresolved text is `Friday maintenance. Premium signals. Referral code. Passing this along as received.` It mentions commercial terms without a clear endorsement or invitation. Promotion is a disagreement with the intended unclear label; the returned score does not establish correctness or reveal the model's reasoning. The original rubric already called the mixed announcement promotion, so that case's new MATCH follows a changed definition rather than a demonstrated correction of the predicted label. The different suites and expectations do not support a 9/10-to-4/5 accuracy comparison.

**Recommended next decision:** retain the candidate as experimental while reviewing the boundary between explicit endorsement and insufficient context. A future refinement can clarify that commercial terms and forwarding alone do not establish an offer, then compare additional previously unseen fragments alongside clear offers and warnings. No confidence cutoff, new rubric, automatic report influence or live promotion is selected by this checkpoint. No repeat paid run or restart is needed now. Live 0.3.3 continues with context-v1 in shadow mode and enforcement disabled.

The pending real-provider status in section 10.22 is superseded. Detailed results are in repository `docs/jev-batch.md`. This documentation-only update records the owner's supplied output; no source, profile, key, cap, Discord message or gateway state was changed, and the developer made no provider call.

### 10.24 Human review buttons and saved message evidence — September 21, 2026

The operator selected **Promotion / Not promotion / Unsure buttons** in private bot-mod reports and requested message links and saved text in incident replies. Version **0.3.4** implements that workflow. New automatic reports and valid incident/explain views have three buttons; the handler accepts only the configured numeric operator in the approved private command channel. A successful click receives a private confirmation. A new incident lookup displays the latest human judgment, reviewer ID, UTC time and reviewed revision separately from JEV's saved label.

**Evidence and revision binding:** incident replies retain the 32-column ASCII metadata panel, then show an escaped, bounded preview of the first distinct saved text and up to three explicit clickable Discord source links. Omitted text/link counts and saved-snapshot caveats are visible. Automatic reports show up to five links. Original evidence is not rewritten; mentions and text URLs are neutralized and embeds suppressed. Reports/views map their successful Discord message ID to the displayed incident revision; clicks cannot select a newer revision by quoting text. Bindings survive restart. Changed/expired/closed/reset evidence makes the human review historical, and missing retained snapshots cannot be reviewed.

**Review scope:** this records a human context label, not permission to post or an enforcement verdict. An endorsed offer can be promotion while still being allowed by server policy. Not promotion does not dismiss a repetition incident. Corrections append bounded history (32 entries per incident); original rule evidence and JEV responses remain intact. Reviews and view bindings follow incident retention, with at most 5,000 view bindings. Authenticated text-command fallback is available. Duplicate interaction receipts, deferred acknowledgements, private-channel rechecks, bounded queue age and the existing operator rate limit control clicks. No review starts an AI call, adjusts budgets, trains a model, suppresses reports or enables moderation actions.

**JEV direction:** keep context-v1 live in shadow mode under existing caps; precedence-v2 remains experimental. Preserve the 9/10 baseline, 4/5 candidate comparison and unresolved fragment. The next useful evidence is human review of actual saved incidents before selecting a further rubric change. No new paid batch or repeated spam posting is needed for this feature.

**Validation and deployment:** 169 core/setup tests pass on Python 3.11.16 and 3.12.3. Another 44 integration tests pass with the pinned c1488 Hermes source and discord.py 2.7.1, with external network edges blocked/mocked. Checks cover labels, authorization, known-message binding, duplicate clicks, private confirmations, failure paths, revision/restart recovery, escaping/Discord size bounds, retained JEV data and configuration-preserving updates. Source version 0.3.4 and the owner-run updater are prepared; the last owner-confirmed live plugin remains 0.3.3. Deployment and live button rendering/interaction remain pending, not claimed complete.

Run `python3 /tmp/liberdus-apply-0.3.4-20260921.py` from the existing hermes terminal. The helper verifies its updater hash, disables the custom platform, confirms the shared-gateway restart, installs the code, enables the custom platform and confirms the final restart. Policy, credentials, database, pause state and counters remain intact. The developer account cannot access the live profile; no real profile/key access, Discord post, JEV call or service restart occurred during implementation. After owner deployment, use `!mod incident b48ee236ff774e0f8efec35caa8ba447`, inspect saved text/links, click the appropriate label and look up the incident again. Historical evidence is expected after restarts. Full instructions and limits are in repository `docs/moderator-review.md` and `/tmp/liberdus-moderator-review-20260921.md`.

### 10.25 Staff assessment workflow and two-box display — September 21, 2026

After discussing the 0.3.4 display, the operator approved broader **Needs attention / Looks okay / Unsure** staff assessments, a visible update to the bot report, a pending-review list, and clearer mobile formatting. Version **0.3.5** implements these. Needs attention and Unsure remain pending; Looks okay completes only the staff review of that evidence. The code-rule incident, JEV result, evidence and enforcement state remain unchanged. No review automatically trains a model, tunes a threshold or authorizes moderation action.

**Staff workflow:** authorized clicks append choice, reviewer ID, time, revision, evidence hash and policy hash to the separate `staff_assessments_v1` table. Old promotion labels stay in `moderator_reviews_v1`, are not reinterpreted, and can be inspected in the technical view. Old buttons direct staff to a fresh incident lookup. A successful assessment attempts to update the clicked bot message and latest delivered private report (at most two edits), showing the saved assessment and non-notifying reviewer mention. A failed/missing/unavailable report edit leaves the saved assessment intact, reports the display failure, and never sends a replacement report or repeats the mutation.

**Queue and evidence:** `!mod pending [PAGE]` reads five scoped pending incidents per page, prioritizing Needs attention and Unsure and including unreviewed retained incidents. `!mod assess ID REV LABEL` and reply commands provide a text fallback. Completion persists across expiry, pause or reconnect when saved evidence and the policy hash are unchanged. Changed evidence/configuration requires a new assessment. Clicking an older report cannot override a newer assessment of different evidence. Historical/current status still describes freshness, not whether staff completed review. The new audit is bounded to 32 entries per incident and follows retention via foreign keys; no automatic conversion of old content labels occurs.

**Display:** reports and incident lookups now use a summary code box plus a saved-text/reference-ID box, with clickable message links outside. Plain punctuation replaces Markdown escaping inside the literal preview. Backticks, mentions, control characters and text URLs are neutralized without changing evidence. The high-level review question and short button definitions explain purpose; a footer says no moderation action or new AI call. The default view trims technical JEV fields; explain retains detail within the same 1,900 UTF-16-unit limit. An old report keeps its exact displayed revision, identifies a newer incident revision if present, and never shows a newer JEV evaluation as matching older displayed text.

**Validation and deployment:** 182 core/setup tests pass on Python 3.11.16 and 3.12.3; 50 integration tests pass against pinned Hermes c1488 and discord.py 2.7.1 with external network blocked/mocked. Coverage includes authorization, queue transitions/pagination, legacy separation, restart/retention, stale evidence and policy, duplicate clicks, private/non-notifying confirmations, report edits/failures and configuration-preserving updates. The owner supplied the prior 0.3.4 incident display; 0.3.5 installation and live verification remain pending.

Run `python3 /tmp/liberdus-apply-0.3.5-20260921.py` from the existing hermes terminal. It verifies its bundle hash, disables the custom platform, confirms restart, updates code, enables the platform and confirms the final restart. Policy, secrets, database, budgets and pause state are preserved. Use an existing incident and `!mod pending` to verify the new workflow without new spam posts or paid calls. Full instructions are in repository `docs/staff-assessment.md` and `/tmp/liberdus-staff-assessment-20260921.md`. The developer account could not access the live profile and performed no real profile/key access, Discord post, JEV call or gateway restart. Context-v1 remains the live shadow rubric; existing baseline/candidate comparisons and limits are unchanged. Later analysis of completed staff reviews remains a deliberate next step, not automatic model training.

### 10.26 Staff review confirmed live; deferred TODOs and current triggers — September 21, 2026

**Owner-supplied live result:** incident `b48ee236ff774e0f8efec35caa8ba447`, revision 2, first showed Needs attention and remained among eight pending reviews. The owner then selected Looks okay. The supplied incident view shows `Review: Complete`, the named reviewer and `2026-09-21 13:46 UTC`; the private acknowledgement reports the assessment saved and the report display updated. The next `!mod pending` shows seven pending reviews and omits that incident. This confirms the staff assessment, completion and queue-removal path for the supplied case. Historical evidence and Needs recheck remain compatible with a completed staff review. The 0.3.5 installation/live-verification pending statement in section 10.25 is superseded for these observed features; other unobserved live cases remain unverified. No repeat installation or repetition of this completed test is needed.

**Deferred TODOs — saved at the owner's request, not started:**

- [ ] Review the remaining test incidents using `!mod pending` and `!mod incident ID`. Seven were pending in the latest supplied output; use the current queue when resuming. Assess each saved example in context and retain its evidence/history.
- [ ] Plan a small staff pilot: agree on participants, approved channels, who follows up on pending reviews, the observation period and what feedback to record before changing access or scope.
- [ ] Gather feedback on report clarity, useful alerts, unnecessary alerts and JEV disagreements during that pilot. Keep staff assessments separate from JEV content labels; use the findings to choose any later rule or rubric change explicitly.

While these TODOs are worked, retain **report-only moderation, JEV shadow mode, existing spending limits and disabled enforcement**. The owner has deferred this work in favor of understanding current message triggers.

**Current trigger reference:** the reviewed code and prepared Liberdus pilot policy have the following behavior. This reference does not claim a fresh read of the owner-only live profile.

- **Single channel:** the same human account posts matching text four times in one of `bot-test-1`, `bot-test-2` or `bot-test-3` within 30 seconds. The normalized text must be at least 20 characters and include a letter or number outside any URL. Expect a private `same_channel_repeat` report in `bot-mod`.
- **Across channels:** the same human account posts matching qualifying text in all three monitored channels within 120 seconds. This is the already-tested `cross_channel_repeat` rule.
- **Blocked domain:** the code supports a report from one message containing an exact configured blocked domain, but the prepared pilot policy has `blocked_domains = []`. No domain trigger is currently configured in that policy.

For an optional single-channel check, first confirm `!mod status` in `bot-mod` is connected and `report_only`. Then post `Liberdus single-channel test 2026-09-21 B.` as four separate identical plain-text messages in `bot-test-1` within 30 seconds. Use a fresh phrase for a separate trial to avoid the five-minute notification cooldown for a repeated pattern. Use the human account in the normal channel with no attachments; bot/webhook posts and threads do not qualify. Read the resulting report in `bot-mod`; the bot does not reply in the test channel.

**JEV's current role:** eligible new code-rule incidents can be classified in shadow mode under the existing freshness, rate and budget limits. JEV does not scan every standalone message. One-off promotions, suspected scams or abuse do not currently produce semantic alerts on their own, and mentioning the bot or sending it a test-channel command does not start a Hermes chat. Authorized `!mod` commands belong in `bot-mod`. Adding a new non-repetition trigger is separate work; no such rule or configuration change is made by this checkpoint.

### 10.27 Single-message JEV screening approved and implemented — September 21, 2026

The owner authorized going directly to private report-only JEV screening in the three existing test channels, with **$1/day and $4 total** for this feature's trial against their stated $5 credit. Version **0.4.0** adds `classifier.mode = "report_only"`; a separate shadow phase is not required. This supersedes the instruction to retain JEV shadow mode in section 10.26 for the newly authorized screening trial. The remaining incident-review, staff-pilot planning and feedback TODOs there remain deferred.

**Behavior:** each new eligible human plain-text message can receive one JEV request per message version, policy and rubric, without waiting for repetition. Typed purpose and concern questions separate ordinary promotion/warnings from suspected credential requests, impersonation tied to risky requests, suspicious offers and targeted abuse. The four specific concerns create private `jev_message` incidents with the existing saved-text/link display and staff buttons. None/unclear choices do not generate an AI alert. Model scores are suggestions; actual semantic behavior requires the owner's live examples. Code rules continue independently. No enforcement, general Hermes chat, URL fetching or public-channel expansion is enabled.

**Accounting and coverage:** screening uses separate durable counters; old shadow/batch history is preserved. Each request first reserves $0.002753, then a fully validated usage result replaces that reservation with its rounded token estimate. Failed, interrupted or unknown-charge requests retain their reservation and are not automatically retried. UTC daily and lifetime caps, secondary call limits, queue/input/time limits, evidence-version checks, edit withdrawal, pause/deletion/reconnect invalidation and credential-failure suspension bound the worker. Status reports checked/flagged/unchecked counts and used/reserved allowance; a skipped or failed check is not clearance. Different messages can each receive an alert, and code-rule incidents remain separate.

**Deployment:** use `python3 /tmp/liberdus-apply-0.4.0-20260921.py` from the existing hermes terminal. The hash-pinned helper disables/restarts, installs the update with a plugin backup, configures the exact approved test scope with a policy backup, then enables/restarts. Existing TypeSafe credentials are reused. The new schema requires restoring the previous policy as well as previous code for rollback; accounting and audit state must be retained. The developer account cannot access the owner-only runtime. Live installation and provider results for this new rubric remain pending; no real profile/key access, Discord post, paid evaluation or gateway restart was performed during implementation.

Detailed behavior, cost accounting, limits, activation, rollback and three single-message live checks are in repository `docs/message-screening.md` and `/tmp/liberdus-message-screening-20260921.md`. Existing JEV baselines and the experimental precedence comparison remain separate records; the new concern rubric does not overwrite them.

**0.4.0 validation:** 206 core/setup tests pass on Python 3.11.16 and 3.12.3, and 61 integration tests pass with pinned Hermes c1488/discord.py 2.7.1 and external network blocked/mocked. Deployment-helper failure paths and the exact-scope activation guard are covered with synthetic profiles; these checks made no paid request or live Discord/service mutation.

### 10.28 JEV role exemption — September 21, 2026

The owner requested an exemption for Discord role `1302455329795342377`. Version **0.4.1** adds `classifier.exempt_role_ids` and trusted author-role snapshots from Discord messages. In the current JEV context, this skips single-message and legacy incident-shadow JEV checks for evidence carrying that role; deterministic repetition/domain checks remain active. It does not grant moderator-command access. Mentioning a role in text cannot exempt the author. The role IDs are not sent to JEV.

The dedicated owner setup adds that role without resetting screening mode, limits, counters, key, pause state or scope. Default-empty exemptions preserve old policy hashes; enabling the role changes the policy hash and leaves earlier evidence/assessments as retained history. Membership is taken from the observed message or fetched edit rather than a continuous roster subscription. Missing role data does not grant an exemption. Existing incidents and already-sent requests are not retroactively erased.

`!mod status` displays the configured exempt role and a separate Role-exempt event count. An exempt account should not be used for subsequent JEV-trigger examples; a non-exempt account can continue those checks. Install with `python3 /tmp/liberdus-apply-0.4.1-20260921.py` in the existing hermes terminal. The helper disables/restarts, installs the backed-up update, adds the exemption to the backed-up policy and enables/restarts. The developer cannot access the owner-only runtime; live role-exemption verification remains pending. Instructions and rollback are in `docs/role-exemption.md` and `/tmp/liberdus-role-exemption-20260921.md`.

**Latest live screening evidence:** the owner supplied 0.4.0 `JEV: report_only / ready` with $1/day and $4 total, followed by single-message incident `02a6e65c2b594100a821a4e28156f329`, revision 1. The message asking for a wallet recovery phrase returned Sensitive request at model score 0.99 and purpose Other. The owner saved Needs attention; the report refreshed and remained pending with current evidence. This confirms that single-message report/staff path for the supplied example, not general semantic accuracy. The initial zero counters were shown before the test. Role exemption and the warning/benign examples are separate checks, not yet claimed live-passed.

**0.4.1 validation:** 213 core/setup tests pass on Python 3.11.16 and 3.12.3; 64 pinned Hermes/Discord integration tests pass with external network blocked/mocked. The role exemption is implemented and packaged, not yet verified in the live owner profile.


### 10.29 — Private exemption controls and consistent display (2026-09-21)

Implemented 0.4.2: `!mod exempt-role on|off` toggles the configured JEV-only role exemption; no arguments displays it. Default ON, durable database setting, authorized private staff commands only. Code repetition rules remain active; no backfill, budget reset, or enforcement. Queued/provider results recheck membership exemption before admission/application. Already-sent calls cannot be recalled.

Added real `!mod help`, mobile-width status/help/plain response panels, and visible top/bottom message dividers on delivery, ephemeral confirmations, and report refreshes. Incident links remain clickable. See `docs/controls-and-formatting.md` for owner installation. 220 core/setup tests and 65 pinned-runtime integration tests passed; live owner activation remains pending. No Discord messages, paid JEV calls, runtime restart, or GitHub push performed for this release.


### 10.30 — Opt-in deletion, Dismiss and staff timeout (2026-09-21)

The owner approved implementation following discussion: narrow automatic deletion, explicit Delete and Dismiss buttons, plus a timeout feature with a command/flag. Version 0.5.0 implements separate durable `!mod deletion`, `!mod auto-delete` and `!mod timeout` switches (on/off/query), all default OFF behind explicit schema-2 `actions_enabled` policy capability. Timeout is staff-confirmed for 10 minutes, server-wide; automatic account penalties, bans, kicks, warnings and strike escalation remain deferred.

Deletion confirmations identify exact source messages (at most 8; one-message selection is available). Timeout confirmations identify the member and server-wide duration. Tokens bind to operator, channel, confirmation message, policy and evidence; they expire in 60 seconds and after restart. Dismiss closes staff review for that snapshot and grants no future exemption. Automatic deletion requires a newly reported, current sensitive_request above 0.90, excludes quoted_warning/unclear purposes and exempt-role messages, caps attempts at 5/minute and never scans historical incidents. Other concerns remain private staff reports.

Durable attempts prevent duplicate mutation or uncertain retry; the adapter refetches evidence, rechecks permissions/scope/settings, protects privileged and configured-exempt-role members from timeout, preserves existing timeouts, and retains incoming off/pause commands during its own deletion reset. There is no atomic Discord conditional-delete operation, so a final fetch-to-mutation race remains. `!mod actions ID` exposes saved attempts. No actual Discord actions or live runtime changes occurred during development.

Validation: 235 core/setup tests on Python 3.11 and 80 pinned-runtime integration tests, with Discord/JEV network boundaries mocked. New runbook: `docs/moderation-actions.md`; owner helper: `/tmp/liberdus-apply-0.5.0-20260921.py`. GitHub push and owner installation are pending. Retain prior code/policy backups and the moderation database; do not reset budgets.

TODO: grant only the required bot permissions; install; verify all switches OFF; test manual deletion confirmation and duplicate rejection; test Dismiss; opt into the narrow auto-delete test; test staff timeout with a consenting ordinary nonprotected test member and turn the feature OFF; verify no new timeout is allowed and remove any test timeout through Discord. Keep the older deferred unauthorized-member check on the list. Public-channel expansion and automatic account sanctions need separate policy work.


### 10.31 — Fix false changed-message refusal before deletion (2026-09-21)

The owner confirmed 0.5.0 installed with action policy enabled, deletion and auto-delete ON, timeout OFF and role exemption OFF. Fresh sensitive-request reports at score 1.00 were followed by “Message changed since the report,” with no visible deletion (incidents `42f5ab6c274f48d9b762b7f11a131205` and `b1f9e585dfc1456ba9f1ff3ccd792208`). This supersedes 10.30's pending-installation status; successful live deletion and staff timeout remain unverified.

Version 0.5.1 fixes the action preflight comparison: the full persisted event hash includes member roles, but REST message fetches may omit those roles. The action now compares all message fields except role metadata while retaining unchanged historical hashes and strict content/identity/edit checks. Automatic deletion with role exemption ON separately fetches current membership and refuses exempt or unverifiable members. Manual deletion remains staff-confirmed; timeout's separate membership/protected-role checks remain intact. No threshold, scope or action policy expansion was made.

Validation: 241 core/setup tests and 90 pinned Hermes/Discord integration tests passed on Python 3.11.16, including real SDK Gateway-Member versus REST-User construction, missing-role automatic/manual deletion, real edit refusal, fresh exemption/membership failures, pause during membership fetch, timeout and stopped 0.5.0 upgrade preservation. No live Discord mutation, real provider call, owner-profile access or gateway restart was performed during this fix.

Owner installation: `python3 /tmp/liberdus-apply-0.5.1-20260921.py`. This code-only, hash-pinned helper disables/restarts, backs up and updates the plugin, then enables/restarts. It preserves current policy, runtime flags, key and accounting; it does not rerun action configuration. Details, fresh-message verification and rollback: `docs/deletion-fix.md` (also `/tmp/liberdus-deletion-fix-20260921.md`). Old release bundles are retained. GitHub push is pending.

TODO: install 0.5.1, check status, send a fresh unedited sensitive-request test in bot-test-1, and confirm source deletion plus a `done` automatic attempt using `!mod actions ID`. Old incidents are historical after restart and must not be used to test automatic retry. Retain the separate manual confirmation, Dismiss, consenting-member timeout and unauthorized-member checks from 10.30; no additional live checks are claimed passed.
