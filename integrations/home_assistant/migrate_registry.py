"""Rehearsable LED wall entity migration through HA's supported registry API.

Plans contain private registry metadata. Keep them with the deployment backup,
not in Git. No playback, config-entry, YAML or .storage writes occur here.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import uuid

DOMAIN = 'led_grid_wall'
ENTITIES = (
    ('light', 'led_grid_wall', 'light'),
    ('number', 'led_grid_wall_pace', 'pace'),
    ('sensor', 'led_grid_wall_observation', 'observation'),
)
USER_FIELDS = ('name', 'icon', 'area_id', 'device_class', 'disabled_by', 'hidden_by', 'aliases', 'labels', 'categories')
IDENTITY_FIELDS = ('id', 'entity_id', 'platform', 'unique_id', 'config_entry_id', 'device_id')


def projection(entry):
    result = json.loads(json.dumps({key: entry.get(key) for key in (*IDENTITY_FIELDS, *USER_FIELDS, 'options')}))
    for key in ('aliases', 'labels'):
        result[key] = sorted(result[key] or [], key=lambda value: str(value))
    for key in ('categories', 'options'):
        result[key] = result[key] or {}
    return result


def metadata_update(before, after):
    message = {'type': 'config/entity_registry/update', 'entity_id': before['entity_id']}
    if before['entity_id'] != after['entity_id']:
        message['new_entity_id'] = after['entity_id']
    for key in USER_FIELDS:
        if before[key] != after[key]:
            value = deepcopy(after[key])
            if key == 'categories':
                value = {name: after[key].get(name) for name in before[key].keys() | after[key].keys()}
            message[key] = value
    return message


def make_plan(entries, installation_id):
    if str(uuid.UUID(installation_id)) != installation_id:
        raise ValueError('Installation identity must be a canonical UUID')
    entries = [projection(entry) for entry in entries]
    identifiers = [entry['id'] for entry in entries]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Duplicate registry record identity')
    pairs = []
    for domain, legacy_key, native_key in ENTITIES:
        public = f'{domain}.{legacy_key}'
        legacy = [e for e in entries if e['platform'] == 'template' and e['unique_id'] == legacy_key and e['entity_id'].split('.')[0] == domain]
        native = [e for e in entries if e['platform'] == DOMAIN and e['unique_id'] == f'{installation_id}_{native_key}' and e['entity_id'].split('.')[0] == domain]
        if len(legacy) != 1 or len(native) != 1:
            raise ValueError(f'Expected one legacy and one native record for {public}')
        old, new = legacy[0], native[0]
        if old['entity_id'] != public or new['entity_id'] == public:
            raise ValueError(f'Public entity ownership changed: {public}')
        archived = f'{domain}.led_grid_wall_legacy_{native_key}'
        if any(e['entity_id'] == archived for e in entries):
            raise ValueError(f'Migration destination is occupied: {archived}')
        if not new['config_entry_id'] or not new['device_id']:
            raise ValueError('Native wall is not registered under a device/config entry')
        for entry in (old, new):
            if entry['disabled_by'] not in (None, 'user') or entry['hidden_by'] not in (None, 'user'):
                raise ValueError('Registry-owned visibility cannot be migrated through the user API')
        pairs.append({'legacy': old, 'native': new, 'archived_entity_id': archived})
    if len({p['native']['config_entry_id'] for p in pairs}) != 1 or len({p['native']['device_id'] for p in pairs}) != 1:
        raise ValueError('Native entities must belong to the same wall device and config entry')
    return {'schema': 'ledgrid.ha-registry-migration', 'schema_version': 1, 'installation_id': installation_id, 'pairs': pairs}


def operations(plan):
    if plan.get('schema') != 'ledgrid.ha-registry-migration' or plan.get('schema_version') != 1:
        raise ValueError('Unsupported migration plan')
    original = [pair[key] for pair in plan['pairs'] for key in ('legacy', 'native')]
    # Rebuild the only allowed six-record scope instead of trusting saved commands.
    if make_plan(original, plan['installation_id']) != plan:
        raise ValueError('Migration plan has changed or is malformed')
    steps = []
    for pair in plan['pairs']:
        old, new = deepcopy(pair['legacy']), deepcopy(pair['native'])
        archived = {**old, 'entity_id': pair['archived_entity_id'], 'disabled_by': 'user'}
        steps.append((old, archived, metadata_update(old, archived)))
        replacement = {**new, **{key: deepcopy(old[key]) for key in USER_FIELDS}, 'entity_id': old['entity_id']}
        steps.append((deepcopy(new), deepcopy(replacement), metadata_update(new, replacement)))
        for name in sorted(old['options'].keys() | new['options'].keys()):
            value = deepcopy(old['options'].get(name))
            if replacement['options'].get(name) == value:
                continue
            before = deepcopy(replacement)
            if value is None:
                replacement['options'].pop(name, None)
            else:
                replacement['options'][name] = value
            message = {'type': 'config/entity_registry/update', 'entity_id': replacement['entity_id'], 'options_domain': name, 'options': value}
            steps.append((before, deepcopy(replacement), message))
    return steps


async def registry_snapshot(call):
    summaries = await call({'type': 'config/entity_registry/list'})
    wanted = [row['entity_id'] for row in summaries if row.get('platform') in ('template', DOMAIN) and (
        row.get('unique_id') in {key for _, key, _ in ENTITIES} or row.get('platform') == DOMAIN)]
    # All summary IDs remain present for collision checks; full records are needed
    # only for the old controls and the native integration.
    extended = await call({'type': 'config/entity_registry/get_entries', 'entity_ids': wanted}) if wanted else {}
    return [extended.get(row['entity_id']) or row for row in summaries]


async def execute(plan, call, *, reverse=False, report=None):
    steps = operations(plan)
    if reverse:
        reversed_steps = []
        for before, after, message in reversed(steps):
            inverse = metadata_update(after, before)
            if 'options_domain' in message:
                name = message['options_domain']
                inverse.update(options_domain=name, options=deepcopy(before['options'].get(name)))
            reversed_steps.append((after, before, inverse))
        steps = reversed_steps
    completed = 0
    for index, (before, after, message) in enumerate(steps):
        rows = await registry_snapshot(call)
        matches = [row for row in rows if row.get('id') == before['id']]
        if len(matches) != 1:
            raise RuntimeError('A planned registry record disappeared; inspect before continuing')
        current = projection(matches[0])
        if current == after:
            continue  # Resume an explicitly repeated plan after partial success.
        if current != before:
            # A later step for this same entity may already have completed. Check
            # only states produced by this exact plan; never overwrite manual edits.
            later = [end for start, end, _ in steps[index + 1:] if start['id'] == before['id']]
            if current in later:
                completed += 1
                continue
            raise RuntimeError(f'Registry changed since planning: {before["entity_id"]}')
        target = message.get('new_entity_id')
        if target and any(row['entity_id'] == target and row.get('id') != before['id'] for row in rows):
            raise RuntimeError(f'Entity ID is occupied: {target}')
        await call(message)
        observed = await call({'type': 'config/entity_registry/get', 'entity_id': after['entity_id']})
        if projection(observed) != after:
            raise RuntimeError(f'Registry did not confirm expected metadata: {after["entity_id"]}')
        completed += 1
        if report:
            report({'completed': completed, 'entity_id': after['entity_id'], 'reverse': reverse})
    return {'success': True, 'direction': 'restore' if reverse else 'migrate'}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'apply', 'restore'))
    parser.add_argument('--url', required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--installation-id')
    args = parser.parse_args()
    # The caller obtains this through smarthome's dedicated SSH/API workflow.
    # Never place an access token in argv, output or a committed file.
    token = sys.stdin.readline().strip()
    if not token:
        raise ValueError('Pass the Home Assistant access token on standard input')
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.ws_connect(args.url.rstrip('/') + '/api/websocket') as socket:
            await socket.receive_json(timeout=10)
            await socket.send_json({'type': 'auth', 'access_token': token})
            if (await socket.receive_json(timeout=10)).get('type') != 'auth_ok':
                raise RuntimeError('Home Assistant authentication failed')
            request_id = 0
            async def call(message):
                nonlocal request_id
                request_id += 1
                await socket.send_json({'id': request_id, **message})
                result = await socket.receive_json(timeout=10)
                if result.get('id') != request_id or not result.get('success'):
                    raise RuntimeError(f'Home Assistant rejected registry operation: {result.get("error")}')
                return result['result']
            if args.action == 'plan':
                plan = make_plan(await registry_snapshot(call), args.installation_id)
                # Never overwrite the original recovery input on a repeated command.
                descriptor = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'w', encoding='utf8') as handle:
                    json.dump(plan, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                print('Saved private migration plan; no registry changes made.')
            else:
                plan = json.loads(args.plan.read_text())
                print(json.dumps(await execute(plan, call, reverse=args.action == 'restore', report=lambda row: print(json.dumps(row), flush=True))))


if __name__ == '__main__':
    asyncio.run(main())
