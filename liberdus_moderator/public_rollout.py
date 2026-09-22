"""Owner-run metadata inventory and stopped, report-only public-channel rollout.

Discord calls are GETs for identity, guild, membership and channel metadata only.
No message history, provider call, Discord mutation or service restart is made here.
"""
import argparse
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from .config import Config
from .configure_jev import atomic_write, policy_text, regular_owned
from .models import validate_ids
from .preflight import ADMIN, HISTORY, SEND, VIEW, DiscordReader, PreflightError, permissions, read_token

GUILD = '746426387606274199'
BOT = '1548537340870533150'
COMMAND = '1551252553331642558'
COMMITTERS = '1318586868136415333'
INCLUDED_CATEGORIES = ('746426387606274201', '746426387606274202')
MANAGE_MESSAGES = 1 << 13
TEST_CHANNELS = frozenset({'1551249559819264030', '1551249642216357908', '1551249693399584818'})
PLAN_NAME = '.liberdus-category-rollout-plan.json'
PLAN_AGE = 86400


class RolloutError(ValueError):
    """Fixed messages only; never expose API bodies or credentials."""


def load_policy(profile):
    for directory in (profile.parent.parent, profile.parent, profile, profile / 'state'):
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.getuid():
            raise RolloutError('Expected the owned, existing Hermes moderation profile.')
    path = profile / 'moderation.toml'
    regular_owned(path)
    config = Config.from_file(path)
    if (profile.name != 'liberdus-mod' or config.guild_id != GUILD or config.bot_user_id != BOT
            or config.command_channel_ids != (COMMAND,) or config.logs_enabled or config.log_channel_id
            or config.operator_role_ids or not config.operator_user_ids
            or Path(config.storage.database_path) != profile / 'state/moderation.sqlite3'
            or config.mode != 'report_only' or not config.ai_enabled or config.classifier.mode != 'report_only'):
        raise RolloutError('Expected the existing Liberdus single-message screening profile.')
    return config


def inventory(config, get):
    me = get('/users/@me')
    if me.get('id') != config.bot_user_id or me.get('bot') is not True:
        raise RolloutError('Bot identity did not match the configured moderator.')
    guild = get('/guilds/' + config.guild_id)
    member = get('/guilds/' + config.guild_id + '/members/' + config.bot_user_id)
    channels = get('/guilds/' + config.guild_id + '/channels')
    if (guild.get('id') != config.guild_id or member.get('user', {}).get('id') != config.bot_user_id
            or not isinstance(channels, list) or len(channels) > 500):
        raise RolloutError('Guild, bot membership or channel metadata did not match.')
    ids = validate_ids([item['id'] for item in channels], 'channel inventory', maximum=500)
    by_id = dict(zip(ids, channels))
    categories = {identity: item for identity, item in by_id.items() if type(item.get('type')) is int and item['type'] == 4}
    output = []
    for identity, channel in sorted(by_id.items()):
        if (channel.get('guild_id', config.guild_id) != config.guild_id
                or type(channel.get('type')) is not int
                or not isinstance(channel.get('name'), str) or len(channel['name']) > 100):
            raise RolloutError('Channel metadata is incomplete or belongs to another server.')
        parent = channel.get('parent_id')
        if parent is not None and parent not in categories:
            raise RolloutError('A channel category is missing from the inventory.')
        row = dict(channel_id=identity, name=channel['name'], type=channel['type'],
                   category_id=parent, category_name=categories[parent]['name'] if parent else None,
                   current_monitoring=identity in config.monitored_channel_ids)
        if channel['type'] == 0:
            if 'parent_id' not in channel or not isinstance(channel.get('permission_overwrites'), list):
                raise RolloutError('Text channel category or permission metadata is incomplete.')
            bot = permissions(guild, member['roles'], config.bot_user_id, channel)
            everyone = permissions(guild, [], None, channel)
            row.update(everyone_visible=bool(everyone & VIEW), bot_view=bool(bot & VIEW),
                       bot_history=bool(bot & VIEW and bot & HISTORY), bot_send=bool(bot & VIEW and bot & SEND),
                       bot_administrator=bool(bot & ADMIN), bot_manage_messages=bool(bot & VIEW and bot & MANAGE_MESSAGES))
        output.append(row)
    return dict(guild_id=config.guild_id, bot_identity_matches=True,
                categories=[dict(category_id=identity, name=channel['name']) for identity, channel in sorted(categories.items())],
                channels=output, messages_read=False, discord_changed=False, provider_called=False)


def selection(config, report, excluded, included=INCLUDED_CATEGORIES):
    excluded = validate_ids(excluded, 'excluded_category_ids', nonempty=True, maximum=500)
    if COMMITTERS not in excluded:
        raise RolloutError('The approved Committers category must remain excluded.')
    included = validate_ids(included, 'included_category_ids', nonempty=True, maximum=500)
    if tuple(sorted(included)) != INCLUDED_CATEGORIES or set(included) & set(excluded):
        raise RolloutError('This rollout includes only the two approved categories, without overlap with exclusions.')
    categories = {item['category_id'] for item in report['categories']}
    if not set(included) <= categories:
        raise RolloutError('An included category ID was not found in this server. Recheck the two category IDs.')
    if not set(excluded) <= categories:
        raise RolloutError('An excluded category ID was not found in this server. Recheck the category ID.')
    selected, omitted, blocked = [], [], []
    command_ok = False
    for row in report['channels']:
        identity = row['channel_id']
        if identity in config.command_channel_ids:
            command_ok = (row['type'] == 0 and not row['everyone_visible'] and not row['bot_administrator']
                          and row['bot_view'] and row['bot_history'] and row['bot_send'])
            omitted.append(dict(channel_id=identity, name=row['name'], reason='private_commands_and_reports'))
        elif identity in TEST_CHANNELS:
            omitted.append(dict(channel_id=identity, name=row['name'], reason='previous_test_channel'))
        elif row['type'] != 0:
            omitted.append(dict(channel_id=identity, name=row['name'], reason='not_an_ordinary_text_channel'))
        elif row['category_id'] in excluded:
            omitted.append(dict(channel_id=identity, name=row['name'], reason='excluded_category'))
        elif row['category_id'] not in included:
            omitted.append(dict(channel_id=identity, name=row['name'], reason='outside_included_categories'))
        elif not row['everyone_visible']:
            omitted.append(dict(channel_id=identity, name=row['name'], reason='audience_unverified_not_everyone_visible'))
        elif row['bot_administrator'] or not row['bot_view'] or not row['bot_history']:
            blocked.append(dict(channel_id=identity, name=row['name'], reason='bot_read_permissions_or_administrator'))
        else:
            selected.append(dict(channel_id=identity, name=row['name'], category_id=row['category_id']))
    if not command_ok:
        raise RolloutError('bot-mod must remain private and readable/writable without Administrator.')
    if blocked:
        # Channel IDs/names are separately visible in inventory; avoid hiding partial coverage.
        raise RolloutError('Some selected text channels lack bot View Channel/Read Message History, or grant Administrator. Blocked channel IDs: ' + ', '.join(row['channel_id'] for row in blocked) + '. Run inventory to check these channels.')
    if not selected:
        raise RolloutError('No eligible public text channels remain after exclusions.')
    return dict(selected=selected, omitted=omitted, included_category_ids=sorted(included), excluded_category_ids=sorted(excluded))


def make_plan(profile, excluded, get, *, now=None):
    config = load_policy(profile)
    report = inventory(config, get)
    chosen = selection(config, report, excluded)
    plan = dict(version=2, guild_id=config.guild_id, bot_user_id=config.bot_user_id,
                baseline_policy_hash=config.policy_hash, created_at=time.time() if now is None else now,
                selected_channels=chosen['selected'], included_category_ids=chosen['included_category_ids'],
                excluded_category_ids=chosen['excluded_category_ids'])
    target = profile / PLAN_NAME
    regular_owned(target, optional=True)
    atomic_write(target, json.dumps(plan, sort_keys=True, indent=2) + '\n')
    return dict(plan_saved=str(target), selected_channels=chosen['selected'], omitted=chosen['omitted'],
                included_category_ids=chosen['included_category_ids'],
                excluded_category_ids=chosen['excluded_category_ids'], selected_count=len(chosen['selected']),
                action_policy='disabled_for_all_monitored_channels', new_channels_auto_added=False,
                active_policy_changed=False, provider_called=False, discord_changed=False)


def verify_plan(profile, get, *, now=None):
    config = load_policy(profile)
    path = profile / PLAN_NAME
    regular_owned(path)
    if path.stat().st_mode & 0o077 or path.stat().st_size > 250000:
        raise RolloutError('Expected a private bounded rollout plan.')
    plan = json.loads(path.read_text())
    if (not isinstance(plan, dict) or set(plan) != {'version', 'guild_id', 'bot_user_id', 'baseline_policy_hash',
            'created_at', 'selected_channels', 'excluded_category_ids', 'included_category_ids'} or type(plan['version']) is not int
            or plan['version'] != 2 or plan['guild_id'] != config.guild_id or plan['bot_user_id'] != config.bot_user_id
            or plan['baseline_policy_hash'] != config.policy_hash or type(plan['created_at']) not in (int, float)
            or not 0 <= (time.time() if now is None else now) - plan['created_at'] <= PLAN_AGE):
        raise RolloutError('The rollout plan is stale or the policy changed. Make a fresh plan.')
    chosen = selection(config, inventory(config, get), plan['excluded_category_ids'], plan['included_category_ids'])
    def frozen(rows):
        if not isinstance(rows, list) or len(rows) > 500:
            raise RolloutError('Invalid planned channel list.')
        if any(not isinstance(row, dict) or set(row) != {'channel_id', 'name', 'category_id'} for row in rows):
            raise RolloutError('Invalid planned channel entry.')
        return [(row['channel_id'], row['category_id']) for row in rows]
    if frozen(plan['selected_channels']) != frozen(chosen['selected']):
        raise RolloutError('Channel scope or categories changed. Make a fresh plan before applying.')
    if (plan['excluded_category_ids'] != chosen['excluded_category_ids']
            or plan['included_category_ids'] != chosen['included_category_ids']):
        raise RolloutError('Excluded category list is not canonical. Make a fresh plan.')
    target_ids = tuple(row['channel_id'] for row in chosen['selected'])
    exceptions = tuple(item for item in config.rules.approved_crossposts if set(item.channel_ids) <= set(target_ids))
    updated = replace(config, schema_version=2, allow_public_monitored_channels=True,
                      excluded_category_ids=tuple(chosen['excluded_category_ids']),
                      included_category_ids=tuple(chosen['included_category_ids']), monitored_channel_ids=target_ids,
                      actions_enabled=False, allow_public_deletion=False, policy_version='category-observation-1',
                      rules=replace(config.rules, approved_crossposts=exceptions))
    return config, updated, chosen


def apply_plan(profile, get, *, now=None, enable_deletion=False):
    import yaml
    paths = (profile.parent.parent / 'config.yaml', profile / 'config.yaml',
             profile / 'plugins/liberdus-moderator/plugin.yaml', profile / 'state/moderation.lock',
             profile / 'state/moderation.sqlite3')
    for path in paths:
        regular_owned(path)
    saved_configuration = {path: path.read_bytes() for path in paths[:3]}
    default, local, manifest = (yaml.safe_load(saved_configuration[path]) for path in paths[:3])
    if (any(data.get('platforms', {}).get('discord', {}).get('enabled') is not False for data in (default, local))
            or local.get('platforms', {}).get('liberdus_moderator', {}).get('enabled') is not False):
        raise RolloutError('Disable the moderation platform and finish the gateway restart first.')
    if manifest.get('name') != 'liberdus-moderator' or manifest.get('version') != '0.7.0':
        raise RolloutError('Install the reviewed 0.7.0 plugin while disabled before applying scope.')
    lock = os.open(paths[3], os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RolloutError('Moderation is still running; finish the disabled gateway restart.') from None
        if enable_deletion:
            from .public_deletion import verify_deletion
            config, updated, chosen = verify_deletion(profile, get)
        else:
            config, updated, chosen = verify_plan(profile, get, now=now)
        policy_path = profile / 'moderation.toml'
        original_policy = policy_path.read_bytes()
        if any(path.read_bytes() != content for path, content in saved_configuration.items()):
            raise RolloutError('Activation or installed version changed during preflight. No scope applied.')
        if Config.from_file(policy_path).policy_hash != config.policy_hash:
            raise RolloutError('Policy changed during preflight; no changes applied.')
        backup = Path(tempfile.mkdtemp(prefix='.liberdus-public-backup-', dir=profile))
        (backup / 'moderation.toml').write_bytes(original_policy)
        (backup / 'moderation.toml').chmod(0o600)
        database_backup = backup / 'moderation.sqlite3'
        fd = os.open(database_backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        connection = sqlite3.connect(paths[4], timeout=5)
        try:
            with sqlite3.connect(database_backup) as saved:
                connection.backup(saved)
            if any(path.read_bytes() != content for path, content in saved_configuration.items()):
                raise RolloutError('Activation or installed version changed during backup. No scope applied.')
            connection.execute('BEGIN IMMEDIATE')
            rows = dict(connection.execute('SELECT key,value FROM settings'))
            epoch = json.loads(rows.get('action_epoch', '0'))
            if type(epoch) is not int or not 0 <= epoch < 2**63 - 1:
                raise RolloutError('Saved action generation needs inspection before rollout.')
            for name in ('deletion_enabled', 'auto_delete_enabled', 'timeout_enabled'):
                connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (name, 'false'))
            connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', ('action_epoch', json.dumps(epoch + 1)))
            connection.commit()
            # Restrictive flags commit first. A later policy-write failure leaves
            # the platform stopped and actions off; never auto-enable on error.
            if (policy_path.read_bytes() != original_policy
                    or any(path.read_bytes() != content for path, content in saved_configuration.items())):
                raise RolloutError('Policy changed while stopped. Actions are off; inspect before retrying.')
            atomic_write(policy_path, policy_text(updated))
        finally:
            connection.close()
    finally:
        os.close(lock)
    return dict(configured=True, platform_enabled=False, selected_channels=chosen['selected'],
                selected_count=len(chosen['selected']), included_category_ids=chosen['included_category_ids'],
                excluded_category_ids=chosen['excluded_category_ids'],
                policy_hash=updated.policy_hash, actions_enabled=updated.actions_enabled,
                public_deletion_allowed=updated.allow_public_deletion, action_flags_reset=True,
                backup=str(backup), usage_counters_changed=False, paused_state_changed=False,
                credentials_changed=False, discord_changed=False, provider_called=False, gateway_restarted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('inventory')
    plan = commands.add_parser('plan')
    plan.add_argument('--exclude-category', action='append', help='Additional category ID to exclude; Committers is always excluded')
    commands.add_parser('verify')
    commands.add_parser('apply')
    args = parser.parse_args()
    profile = Path.home() / '.hermes/profiles/liberdus-mod'
    python = profile.parent.parent / 'hermes-agent/venv/bin/python'
    if not python.is_file():
        print('Scope preparation stopped: expected the existing Hermes environment.')
        return 2
    if Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), '-B', sys.argv[0], *sys.argv[1:]])
    try:
        config = load_policy(profile)
        get = DiscordReader(read_token(profile / '.env'))
        if args.operation == 'inventory':
            result = inventory(config, get)
        elif args.operation == 'plan':
            result = make_plan(profile, list(dict.fromkeys([COMMITTERS, *(args.exclude_category or [])])), get)
        elif args.operation == 'verify':
            _, updated, chosen = verify_plan(profile, get)
            result = dict(verified=True, selected_count=len(chosen['selected']), actions_enabled=updated.actions_enabled,
                          included_category_ids=chosen['included_category_ids'],
                          excluded_category_ids=chosen['excluded_category_ids'], active_policy_changed=False)
        else:
            result = apply_plan(profile, get)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (RolloutError, PreflightError) as error:
        print('Scope preparation stopped: ' + str(error))
        return 2
    except Exception:
        print('Scope preparation stopped: invalid profile, metadata or saved plan. No activation requested; inspect before retrying.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
