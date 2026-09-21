"""Owner-run deletion opt-in for the seven approved, already-monitored channels.

Verification reads Discord metadata only. Application requires stopped moderation,
keeps the scope unchanged, and leaves all runtime action switches OFF.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

from .preflight import DiscordReader, PreflightError, read_token
from .public_rollout import (COMMITTERS, INCLUDED_CATEGORIES, RolloutError, apply_plan,
                             inventory, load_policy, selection)

APPROVED_CHANNELS = frozenset({
    '1293238000313958451', '1318757883260964884', '1453063291961212959',
    '1479253446258458644', '1486610685398876170', '746426388050870282', '746499160823431199',
})


def verify_deletion(profile, get):
    config = load_policy(profile)
    if (not config.allow_public_monitored_channels
            or set(config.monitored_channel_ids) != APPROVED_CHANNELS
            or tuple(sorted(config.included_category_ids)) != INCLUDED_CATEGORIES
            or COMMITTERS not in config.excluded_category_ids):
        raise RolloutError('Expected the active seven-channel Community rollout with its category boundaries. No scope expansion allowed.')
    report = inventory(config, get)
    # New channels do not enter scope or require permissions for this update.
    report['channels'] = [row for row in report['channels']
                          if row['channel_id'] in APPROVED_CHANNELS or row['channel_id'] in config.command_channel_ids]
    chosen = selection(config, report, config.excluded_category_ids, config.included_category_ids)
    if {row['channel_id'] for row in chosen['selected']} != APPROVED_CHANNELS:
        raise RolloutError('An approved channel is missing, private or outside the approved categories. No deletion policy applied.')
    missing = [row['channel_id'] for row in report['channels']
               if row['channel_id'] in APPROVED_CHANNELS and not row['bot_manage_messages']]
    if missing:
        raise RolloutError('Allow Manage Messages for Liberdus Moderator in these monitored channels: ' + ', '.join(missing))
    updated = replace(config, actions_enabled=True, allow_public_deletion=True,
                      policy_version='category-deletion-1')
    return config, updated, chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('verify', 'apply'))
    args = parser.parse_args()
    profile = Path.home() / '.hermes/profiles/liberdus-mod'
    python = profile.parent.parent / 'hermes-agent/venv/bin/python'
    if not python.is_file():
        print('Deletion preparation stopped: expected the existing Hermes environment.')
        return 2
    if Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), '-B', sys.argv[0], *sys.argv[1:]])
    try:
        load_policy(profile)
        get = DiscordReader(read_token(profile / '.env'))
        if args.operation == 'verify':
            _, _, chosen = verify_deletion(profile, get)
            result = dict(verified=True, public_deletion_allowed=True, selected_channels=chosen['selected'],
                          selected_count=len(chosen['selected']), active_policy_changed=False,
                          discord_changed=False, provider_called=False)
        else:
            result = apply_plan(profile, get, enable_deletion=True)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (RolloutError, PreflightError) as error:
        print('Deletion preparation stopped: ' + str(error))
        return 2
    except Exception:
        print('Deletion preparation stopped: invalid profile or metadata. No activation requested; inspect before retrying.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
