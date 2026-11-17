#!/usr/bin/env python3
"""The coverage matrix: which areas and which use cases the atlas has looked at, ranked by where players lose frames.

Inputs are plain JSON a caller keeps locally: the field data (`ls_field.py perf --by map --json`: rows of
{group: "<map> (<age>)", sessions, hours, fps, low1, hitches_per_hour}) and a coverage file naming the areas, the use cases and the
state of each cell. Nothing here knows a map: the names come from the files.

A cell is one of `planned`, `observed` (an ordinary run: counts, pictures, shares), `priced` (an exclusive paired run priced a part
or an optimization), `finding` (a proven result exists) or `n/a`. A cell nobody wrote is `-`: not planned, not a negative result.
"""
import json

STATES = ('-', 'n/a', 'planned', 'observed', 'priced', 'finding')
USE_CASES = {
    'first-visit': 'a fresh game, the first time the area loads (pipelines and textures cold)',
    'revisit': 'the same area again in one process (caches warm, state from the last visit)',
    'stand': 'standing still at a viewpoint, looking at the heavy view',
    'walk': 'a fixed route on foot (streaming, loads, hitches)',
    'crowd': 'the most people and animals in view or near',
    'night': 'night (other actors, lights, other effects)',
    'cutscene': 'a cutscene or conversation camera (nothing to control)',
    'combat': 'a fight (effects, enemies, the lock-on camera)',
    'weather': 'rain, fog or another weather state',
    'menus': 'a menu or the map open over the game',
    'coop': 'co-op bodies of other players drawn',
}


def field_index(field):
    """{map name: row} from the field data object; the age in the group's brackets is dropped, "Title and intro" and rows with no
    sessions are left out; the same map twice (two ages) keeps the row with the more hours"""
    out = {}
    for r in field.get('rows', []):
        g = r.get('group', '')
        if not g or g.startswith('Title'):
            continue
        name = g.split(' (')[0].strip()
        if name not in out or r.get('hours', 0) > out[name].get('hours', 0):
            out[name] = r
    return out


def lost_hours(row):
    """player-hours lost against 60 fps: hours x (1 - fps / 60), zero for a row at 60 or above or without numbers. A ranking aid, not a
    measure: fps caps and the cards in use bias the averages, and sessions are not players"""
    if not row or row.get('fps') is None or row.get('hours') is None:
        return 0.0
    return max(0.0, row['hours'] * (1.0 - min(row['fps'], 60.0) / 60.0))


def rank_areas(areas, field, min_sessions=0):
    """areas: {key: {"map": ..., "label": ..., "heavy": bool (known heavy), "note": ...}} -> a list of dicts sorted by lost hours
    (known heavy areas with no field row after the ones with rows). Each has sessions, hours, fps, low1, hitches_per_hour (or None)."""
    idx = field_index(field)
    out = []
    for key, a in areas.items():
        row = idx.get(a.get('map', ''))
        sessions = row.get('sessions') if row else None
        usable = row is not None and (sessions or 0) >= min_sessions
        out.append({'key': key, 'label': a.get('label', key), 'map': a.get('map'), 'heavy': bool(a.get('heavy')), 'sessions': sessions if usable else None,
                    'hours': row.get('hours') if usable else None, 'fps': row.get('fps') if usable else None, 'low1': row.get('low1') if usable else None,
                    'hitches_per_hour': row.get('hitches_per_hour') if usable else None, 'lost_hours': lost_hours(row) if usable else None,
                    'note': a.get('note', '')})
    out.sort(key=lambda r: (r['lost_hours'] is None, -(r['lost_hours'] or 0.0), not r['heavy'], r['key']))
    return out


def load_cells(obj):
    """{(area key, use case): {'state': ..., 'note': ...}} from {"cells": {"<area>|<use case>": {"state": ..., "note": ...}}}; an unknown state raises"""
    cells = {}
    for k, v in (obj.get('cells') or {}).items():
        area, _, case = k.partition('|')
        state = v.get('state', '-')
        if state not in STATES:
            raise ValueError(f'cell {k}: state {state!r} is not one of {STATES}')
        cells[(area, case)] = {'state': state, 'note': v.get('note', '')}
    return cells


def matrix_markdown(ranked, cells, use_cases=None):
    use_cases = use_cases or list(USE_CASES)
    lines = ['| area | sessions | hours | fps | low1 | hitches/h | ' + ' | '.join(use_cases) + ' |',
             '|---|---|---|---|---|---|' + '---|' * len(use_cases)]
    for r in ranked:
        def f(x, nd=1):
            return '-' if x is None else f'{x:.{nd}f}'
        row = [r['label'] + (' *' if r['heavy'] else ''), f(r['sessions'], 0), f(r['hours'], 2), f(r['fps']), f(r['low1']), f(r['hitches_per_hour'], 0)]
        row += [cells.get((r['key'], c), {}).get('state', '-') for c in use_cases]
        lines.append('| ' + ' | '.join(row) + ' |')
    lines += ['', '`*` known heavy before the field data (a place named in a report or an issue). Field figures: release builds, sessions and hours '
              'beside every figure; below ten sessions a row is an anecdote.']
    return '\n'.join(lines) + '\n'


def next_cells(ranked, cells, use_cases=None, limit=10):
    """the cells to fill next: in rank order, for each area the first use cases of the list whose state is '-' (planned cells come after);
    [(area key, use case, why)]"""
    use_cases = use_cases or list(USE_CASES)
    out = []
    for r in ranked:
        for c in use_cases:
            st = cells.get((r['key'], c), {}).get('state', '-')
            if st == '-':
                why = f"{r['label']}: " + (f"{r['lost_hours']:.2f} player-hours lost" if r['lost_hours'] else 'known heavy' if r['heavy'] else 'no field row')
                out.append((r['key'], c, why))
                if len(out) >= limit:
                    return out
    return out


def load_json(path):
    with open(path) as fh:
        return json.load(fh)
