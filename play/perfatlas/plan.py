#!/usr/bin/env python3
"""From a library and a list of conditions to runs and holds, without a game.

A *run* is one game process (one `hs-run.sh`): it boots from a save, then executes an ordered list of ops. A *hold* is what one
admission behind the quiet-window and measuring locks may run: one or more runs one after the other, at most `hold_limit_s`
(480 s unless the main session granted more for these holds).

Ops (what `driver.py` executes; each is a plain dict, the plan is JSON):

    {"op": "boot", "map": "<map name>"}              wait until the save is in play in that map (presses A through the title)
    {"op": "time", "hour": 22}                       !cheat 100+h (set the time of day); an area reload makes it show
    {"op": "actors", "cond": id, "rules": [["off", "Npc_*", false], ...], "reload": true, "area": "<map>"}
    {"op": "actors_clear", "area": "<map>", "reload": true}
    {"op": "option", "name": "...", "value": ...}    live, through the menu's setter; the first-seen value is remembered
    {"op": "option_restore", "name": "..."}
    {"op": "pos", "cond": id, "vp": id, "visit": n, "settle_s": 3, "dwell_s": 15, "min_frames": 0, "line": "!pos x y z", "aim": [yaw, pitch] | null, "shot": true}
    {"op": "stand", "cond": id, "vp": id, "visit": 1, "expect": [x, z], "aim": [yaw, pitch] | null, "settle_s": 2, "dwell_s": 10, "shot": true}   no !pos: where the save put Link
    {"op": "sweep", "vp": id, "line": "!pos x y z", "yaws": [0, 60, ...], "dwell_s": 3}   look each way and read the draws (a scout)
    {"op": "walk", "cond": id, "walk": id, "points": [[x, z], ...], "start": [x, y, z] | null, "radius": 1.5, "timeout_s": 90, "shot": true}
    {"op": "quit"}

Conditions of kind 'env' change the game process: they get runs of their own (env on the launch line), one run for each distinct
env. The first and last block of an actors run is the baseline, so a drift between the two (a revisit of the same place after the
area was reloaded several times) shows beside the price of each family.
"""
import math

from . import model

# Seconds, calibrated on the first hold (17 Nov, Castle Town, a loaded PC): boot 34 to 39, an area reload 9 to 10, a standing window 26 for a
# 15 s dwell and a 3 s settle (so about 8 s of arrival, polling and picture beyond them). The camera turn (aim_s) is a first guess.
DEFAULT_COSTS = {'boot_s': 45.0, 'reload_s': 14.0, 'arrive_s': 5.0, 'shot_s': 2.5, 'option_s': 2.0, 'time_s': 1.0, 'quit_s': 12.0,
                  'gap_between_runs_s': 25.0, 'walk_extra_s': 6.0, 'margin': 1.1, 'fps_floor': 60.0, 'aim_s': 4.0}


class PlanError(ValueError):
    pass


def op_seconds(op, costs):
    k = op['op']
    if k == 'boot':
        return costs['boot_s']
    if k in ('actors', 'actors_clear'):
        return costs['reload_s'] if op.get('reload') else costs['option_s']
    if k in ('option', 'option_restore'):
        return costs['option_s']
    if k == 'time':
        return costs['time_s']
    if k in ('pos', 'stand'):
        return (costs['arrive_s'] + (costs['aim_s'] if op.get('aim') else 0) + op['settle_s'] + max(op['dwell_s'], op.get('min_frames', 0) / costs['fps_floor'])
                + (costs['shot_s'] if op.get('shot') else 0))
    if k == 'sweep':
        return costs['arrive_s'] + op.get('settle_s', 1.0) + len(op['yaws']) * (costs['aim_s'] + op.get('look_s', 1.0) + op.get('dwell_s', 3.0) + 1.0)
    if k == 'walk':
        return min(op['timeout_s'], 60.0 + op['timeout_s'] * 0.5) + costs['walk_extra_s'] + (costs['shot_s'] if op.get('shot') else 0)
    if k == 'quit':
        return costs['quit_s']
    raise PlanError(f'unknown op {k!r}')


def seconds_of(ops, costs):
    return sum(op_seconds(o, costs) for o in ops) * costs['margin']


def pos_ops(lib, cond_id, vp_ids, visit, shot=True, over=None):
    """`over` replaces the library's dwell_s, settle_s or min_frames for every viewpoint of the plan (a pass-timing run needs 1,300
    presents in a window so that two 600-present blocks fall inside it)"""
    over = over or {}
    ops = []
    for vid in vp_ids:
        vp = lib.viewpoints[vid]
        ops.append({'op': 'pos', 'cond': cond_id, 'vp': vid, 'visit': visit, 'settle_s': over.get('settle_s', vp.settle_s),
                    'dwell_s': over.get('dwell_s', vp.dwell_s), 'min_frames': over.get('min_frames', vp.min_frames), 'line': vp.pos_line(),
                    'aim': vp.aim(), 'shot': shot, 'tod': vp.tod})
    return ops


def walk_ops(lib, cond_id, walk_ids, visit, shot=True):
    ops = []
    for wid in walk_ids:
        w = lib.walks[wid]
        ops.append({'op': 'walk', 'cond': cond_id, 'walk': wid, 'visit': visit, 'points': [list(p) for p in w.points],
                    'start': list(w.start) if w.start else None, 'radius': w.radius, 'timeout_s': w.timeout_s, 'shot': shot, 'tod': w.tod})
    return ops


def condition_block(lib, cond, vp_ids, walk_ids, area_map, visit, previous, over=None):
    """ops of one condition's block. `previous` is the condition before it in the same run (None at the start), which decides what
    must be undone first: actor rules are cleared (with a reload) when the block before had some, and options are restored after."""
    ops = []
    if previous is not None and previous.kind == 'actors' and cond.kind != 'actors':
        ops.append({'op': 'actors_clear', 'area': area_map, 'reload': True})
    if cond.kind == 'actors':
        ops.append({'op': 'actors', 'cond': cond.id, 'area': area_map, 'rules': [list(r) for r in cond.rules], 'reload': True,
                    'clear_first': True})
    elif cond.kind == 'option':
        ops += [{'op': 'option', 'name': k, 'value': v, 'cond': cond.id} for k, v in cond.options]
    elif cond.kind == 'time':
        ops.append({'op': 'time', 'hour': cond.hour})
        if cond.reload is not False:
            ops.append({'op': 'actors_clear', 'area': area_map, 'reload': True})   # (a reload with no rules: the area is read again at the new time)
    ops += pos_ops(lib, cond.id, vp_ids, visit, over=over) + walk_ops(lib, cond.id, walk_ids, visit)
    if cond.kind == 'option':
        ops += [{'op': 'option_restore', 'name': k} for k, v in cond.options]
    return ops


def plan_runs(lib, vp_ids, conditions, walk_ids=(), area=None, tod=None, run_budget_s=420.0, costs=None, env=(), over=None, prefix='r'):
    """runs for one area and one time of day.

    vp_ids / walk_ids: viewpoints and walks of that area. conditions: model.Condition list in the order to visit (the baseline
    is added first and last when any actors or time condition is present, and a 'baseline' condition in the list is not repeated).
    env: extra launch switches for every run ((name, value) pairs). Conditions of kind 'env' are split off into runs of their own.
    over: {'dwell_s', 'settle_s', 'min_frames'} replacing the library's values for every viewpoint. prefix: the runs' ids are <prefix>1, <prefix>2 ...
    Returns a list of run dicts {id, area, tod, save, env, ops, est_s}."""
    costs = {**DEFAULT_COSTS, **(costs or {})}
    items = [lib.viewpoints.get(i) or lib.walks.get(i) for i in list(vp_ids) + list(walk_ids)]
    if any(i is None for i in items):
        raise PlanError('a viewpoint or walk id is not in the library')
    areas = {i.area for i in items}
    tods = {i.tod for i in items}
    if area is None:
        if len(areas) != 1:
            raise PlanError(f'the items span areas {sorted(areas)}: plan one area at a time (area=...)')
        area = next(iter(areas))
    if area not in lib.areas:
        raise PlanError(f'area {area!r} is not in the library')
    if any(i.area != area for i in items):
        raise PlanError(f'an item is not in area {area}')
    if tod is None:
        if len(tods) != 1:
            raise PlanError(f'the items span times of day {sorted(tods)}: plan one at a time (tod=...)')
        tod = next(iter(tods))
    if any(i.tod != tod for i in items):
        raise PlanError(f'an item is not for time of day {tod}')
    a = lib.areas[area]
    save = lib.save_for(area, tod)
    vp_ids, walk_ids = list(vp_ids), list(walk_ids)
    plain = [c for c in conditions if c.kind != 'env']
    envs = [c for c in conditions if c.kind == 'env']
    runs = []

    def new_run(rid, extra_env):
        return {'id': rid, 'area': area, 'map': a.map, 'tod': tod, 'save': save, 'env': [list(p) for p in tuple(env) + tuple(extra_env)],
                'ops': [{'op': 'boot', 'map': a.map}], 'blocks': []}

    def close(run):
        run['ops'].append({'op': 'quit'})
        run['est_s'] = round(seconds_of(run['ops'], costs), 1)
        run.pop('blocks')
        return run

    # ---- the main process: baseline first, the conditions, the baseline again when something was reloaded (none when every condition is a launch switch:
    # those get a process each, and a baseline process only when 'baseline' is named)
    if plain:
        base = model.Condition('baseline', 'baseline', 'as the game plays', 'baseline')
        seq = [c for c in plain if c.id != 'baseline']
        order = [base] + seq
        if any(c.kind in ('actors', 'time') for c in seq):
            order.append(model.Condition('baseline-again', 'baseline', 'as the game plays, after the reloads', 'baseline'))
        cur, previous, n, baselines = new_run(f'{prefix}1', ()), None, 1, 0
        for cond in order:
            if cond.kind == 'baseline':
                baselines += 1   # (the visit number of a baseline block: 1 at the start, 2 after the reloads)
            visit = baselines if cond.kind == 'baseline' else 1
            block = condition_block(lib, cond, vp_ids, walk_ids, a.map, visit, previous, over)
            block_s = seconds_of(block, costs)
            run_s = seconds_of(cur['ops'] + [{'op': 'quit'}], costs)
            if run_s + block_s > run_budget_s and len(cur['ops']) > 1:
                runs.append(close(cur))   # (a process that ends needs no undoing: the rules die with it)
                n += 1
                cur, previous = new_run(f'{prefix}{n}', ()), None
                block = condition_block(lib, cond, vp_ids, walk_ids, a.map, visit, previous, over)
            if seconds_of(block + [{'op': 'boot', 'map': a.map}, {'op': 'quit'}], costs) > run_budget_s:
                raise PlanError(f'condition {cond.id} alone needs {seconds_of(block, costs):.0f} s: more than the run budget {run_budget_s:.0f} s '
                                f'(fewer viewpoints, shorter dwells or a longer budget)')
            cur['ops'] += block
            previous = cond
        runs.append(close(cur))
    # ---- one process for each distinct env
    groups = {}
    for c in envs:
        groups.setdefault(c.env, []).append(c)
    for e, conds in groups.items():
        cur = new_run(f'{prefix}{len(runs) + 1}', e)
        # the env conditions that share a process share its switches: they are one condition, labelled by their ids
        label = '+'.join(c.id for c in conds)
        cur['ops'] += pos_ops(lib, label, vp_ids, 1, over=over) + walk_ops(lib, label, walk_ids, 1)
        if seconds_of(cur['ops'] + [{'op': 'quit'}], costs) > run_budget_s:
            raise PlanError(f'the env run {label} needs {seconds_of(cur["ops"], costs):.0f} s: more than the run budget {run_budget_s:.0f} s')
        runs.append(close(cur))
    return runs


def plan_holds(runs, hold_limit_s=480.0, costs=None):
    """group runs into holds (admissions) of at most hold_limit_s, in order; a run alone longer than the limit is an error"""
    costs = {**DEFAULT_COSTS, **(costs or {})}
    holds, cur, used = [], [], 0.0
    for r in runs:
        need = r['est_s'] + (costs['gap_between_runs_s'] if cur else 0.0)
        if r['est_s'] > hold_limit_s:
            raise PlanError(f'run {r["id"]} needs {r["est_s"]:.0f} s, more than a hold of {hold_limit_s:.0f} s')
        if cur and used + need > hold_limit_s:
            holds.append({'id': f'h{len(holds) + 1}', 'runs': cur, 'est_s': round(used, 1)})
            cur, used, need = [], 0.0, r['est_s']
        cur.append(r)
        used += need
    if cur:
        holds.append({'id': f'h{len(holds) + 1}', 'runs': cur, 'est_s': round(used, 1)})
    return holds


def summary(holds):
    """a short text: holds, runs, minutes and the op counts"""
    lines = []
    for h in holds:
        lines.append(f"hold {h['id']}: {len(h['runs'])} run(s), about {h['est_s']:.0f} s")
        for r in h['runs']:
            ops = r['ops']
            n_pos = sum(1 for o in ops if o['op'] in ('pos', 'sweep'))
            n_walk = sum(1 for o in ops if o['op'] == 'walk')
            n_rel = sum(1 for o in ops if o['op'] in ('actors', 'actors_clear') and o.get('reload'))
            env = ' '.join(f'{k}={v}' for k, v in r['env']) or '(no extra switches)'
            if r.get('pin'):
                env += f" pinned to CPUs {r['pin']}"
            if any(o.get('go') for o in ops):
                env += ' with a go window'
            lines.append(f"  run {r['id']}: {n_pos} viewpoint visits, {n_walk} walks, {n_rel} area reloads, about {r['est_s']:.0f} s; {env}")
    return '\n'.join(lines)


def plan_sweep(lib, vp_ids, yaws, run_budget_s=420.0, dwell_s=3.0, costs=None, prefix='s'):
    """scout runs: each viewpoint of the list (its position only) looked at in each yaw, as many places to a run as fit the budget.
    Returns runs like plan_runs'; a time of day needs its own plan (the save differs)"""
    costs = {**DEFAULT_COSTS, **(costs or {})}
    items = [lib.viewpoints.get(i) for i in vp_ids]
    if any(i is None for i in items):
        raise PlanError('a viewpoint id is not in the library')
    areas, tods = {i.area for i in items}, {i.tod for i in items}
    if len(areas) != 1 or len(tods) != 1:
        raise PlanError(f'the items span areas {sorted(areas)} and times of day {sorted(tods)}: plan one of each')
    area, tod = next(iter(areas)), next(iter(tods))
    a = lib.areas[area]
    save = lib.save_for(area, tod)
    runs, cur = [], None

    def start(n):
        return {'id': f'{prefix}{n}', 'area': area, 'map': a.map, 'tod': tod, 'save': save, 'env': [], 'ops': [{'op': 'boot', 'map': a.map}]}

    def close(r):
        r['ops'].append({'op': 'quit'})
        r['est_s'] = round(seconds_of(r['ops'], costs), 1)
        return r

    cur = start(1)
    for vp in items:
        op = {'op': 'sweep', 'vp': vp.id, 'line': vp.pos_line(), 'yaws': list(yaws), 'dwell_s': dwell_s, 'settle_s': 1.0, 'look_s': 1.0, 'tod': tod}
        if seconds_of(cur['ops'] + [op, {'op': 'quit'}], costs) > run_budget_s and len(cur['ops']) > 1:
            runs.append(close(cur))
            cur = start(len(runs) + 1)
        if seconds_of(cur['ops'] + [op, {'op': 'quit'}], costs) > run_budget_s:
            raise PlanError(f'one place with {len(yaws)} yaws needs {seconds_of([op], costs):.0f} s: more than the run budget {run_budget_s:.0f} s')
        cur['ops'].append(op)
    runs.append(close(cur))
    return runs


def plan_save_proof(lib, vp_ids, dwell_s=10.0, costs=None, prefix='v', env=(), go=None, pin=None):
    """one run that boots each viewpoint's own save and checks where Link stands (no `!pos`): the proof that a save made for a viewpoint reaches
    play at that place. Each viewpoint needs its own area (its own save) in the library; one run for each, in the order given. Estimates only.
    `env` ((name, value) pairs) goes on every run's launch line: a long stand with HOS_PROFILE=1 is a profile run; `{run}` in a value is the run's output
    folder (a recorder's path). `go` ({'after_s', 'for_s'}): the stand makes the recorder's go file (HOS_CHAIN's `<path>.go`) for that part of its window.
    `pin`: a CPU list ('0-11') the game is launched under (taskset): a small CPU's contention, an arm of its own."""
    costs = {**DEFAULT_COSTS, **(costs or {})}
    runs = []
    for i, vid in enumerate(vp_ids, 1):
        vp = lib.viewpoints.get(vid)
        if vp is None:
            raise PlanError(f'{vid}: no such viewpoint')
        a = lib.areas[vp.area]
        save = lib.save_for(vp.area, vp.tod)
        ops = [{'op': 'boot', 'map': a.map},
               {'op': 'stand', 'cond': 'save-proof', 'vp': vid, 'visit': 1, 'expect': [vp.pos[0], vp.pos[2]], 'aim': vp.aim(), 'settle_s': 2.0,
                'dwell_s': dwell_s, 'shot': True, 'tod': vp.tod}, {'op': 'quit'}]
        if go:
            if go['after_s'] + go['for_s'] > dwell_s:
                raise PlanError(f'the go window ({go["after_s"]} + {go["for_s"]} s) does not fit the dwell of {dwell_s} s')
            ops[1]['go'] = dict(go)
        runs.append({'id': f'{prefix}{i}', 'area': vp.area, 'map': a.map, 'tod': vp.tod, 'save': save, 'env': [list(e) for e in env], 'ops': ops,
                     'est_s': round(seconds_of(ops, costs), 1), 'pin': pin})
    return runs
