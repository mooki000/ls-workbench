#!/usr/bin/env python3
"""Builders for the paired timing worker's inputs (a scenario and a `_hold` plan) for one atlas viewpoint, and the verdict of a null pair.

Pure functions: nothing here starts a game. The worker is the shared regression adapter (`regress.py _hold`, `run_case.py`); this
file only writes its inputs the way every atlas timing hold needs them, and refuses what a timing hold must not do:

  * the decision bands are required arguments (set before the results exist, never defaulted);
  * the plan must fit the worker's cap: `n * (seconds + grace) + 10 <= max_hold_seconds` for its n runs;
  * runs are reference, candidate, reference, candidate (A B A B); a **null pair** uses one copy and one commit for all four.

A null pair is the first timing hold at a scene: the same build on both arms. Its two same-arm spreads and its arm gap say whether the scene
can decide anything. Verdict bands (set here, before any result): same-arm spread at most 5% (the worker's own tolerance) and an arm gap
at most 3%; a gap of that size is the smallest difference an optimization at this scene could be told from noise.
"""
import json
from pathlib import Path

CAP_S = 480
RESERVE_S = 10
FIXED_ENV = {'HOS_OUTPUT_RES': '2048x1152', 'HOS_DYNRES': '0', 'HOS_FPS_MAX': '240', 'RX_VK_DLSS': '0', 'RX_VK_FRAMEGEN': '0',
             'RX_VK_AUTO_TUNE': '0', 'RX_VK_AUTO_PRESET': '0', 'RX_VK_SHOT_EVERY': '1000000'}
SPREAD_MAX = 0.05
GAP_MAX = 0.03
AVOID_WIDGET = {'every': 900, 'at': 535, 'for': 12}   # the desktop's usage widget (skill ls-perf-compare)


class TimingError(ValueError):
    pass


def make_scenario(label, save, scene, good_max, bad_min, from_s=55, to_s=105, boot_input='5:A,8.7:A,11.7:A,15:A', measure='mean_ms',
                  env=None, seconds=110, grace_seconds=5, repetitions=2, extra_expect=()):
    """the scenario JSON for one viewpoint. `scene` is the atlas_scene object (map, aim_yaw, pos [x, z], control_by_s, ready_by_s, ...).
    good_max and bad_min are the decision bands in the measure's own unit (worseness: higher is worse); both are required."""
    if good_max is None or bad_min is None:
        raise TimingError('the decision bands (good_max, bad_min) are set before the results: pass both')
    if not good_max < bad_min:
        raise TimingError(f'good_max {good_max} must be below bad_min {bad_min}')
    if not 0 < from_s < to_s <= seconds - 3:
        raise TimingError(f'the window {from_s}..{to_s} must lie inside the run ({seconds} s) with 3 s to spare')
    if scene.get('ready_by_s', 1e9) > from_s - 2:
        raise TimingError(f"the aim must be done by {from_s - 2} s (the window starts at {from_s}): ready_by_s is {scene.get('ready_by_s')}")
    e = dict(FIXED_ENV)
    e.update(env or {})
    return {'schema_version': 1, 'name': label, 'kind': 'timing', 'seconds': seconds, 'grace_seconds': grace_seconds, 'repetitions': repetitions,
            'max_retries': 0, 'quiet_cores': 3.0, 'quiet_peak_cores': 6.0, 'repeat_tolerance': SPREAD_MAX,
            'measure': {'name': measure, 'direction': 'higher-worse', 'good_max': good_max, 'bad_min': bad_min},
            'run': {'label': label, 'user': save, 'input': boot_input, 'anchor': 'first', 'from': from_s, 'to': to_s, 'readback_csv': False,
                    'sample_seconds': 2.5, 'atlas_scene': scene, 'env': e, 'expect_log': ['2048x1152', *extra_expect],
                    'forbid_log': ['device lost']}}


def make_plan(entries, scenario_path, run_command, seconds=110, grace_seconds=5, settle_seconds=10, quiet_cores=3, max_hold_seconds=CAP_S, avoid=(AVOID_WIDGET,)):
    """a `_hold` plan. entries: [(role, label, copy, commit, output)] in the order they run (reference, candidate, reference, candidate)."""
    n = len(entries)
    if [e[0] for e in entries] != ['reference', 'candidate'] * (n // 2) or n < 4 or n % 2:
        raise TimingError('the runs go reference, candidate, reference, candidate (at least two of each)')
    if n * (seconds + grace_seconds) + RESERVE_S > max_hold_seconds or max_hold_seconds > CAP_S:
        raise TimingError(f'{n} runs of {seconds}+{grace_seconds} s and the reserve need {n * (seconds + grace_seconds) + RESERVE_S} s: '
                          f'more than the hold cap {min(max_hold_seconds, CAP_S)} s')
    outs = [e[4] for e in entries]
    if len(set(outs)) != n or any(Path(o).exists() for o in outs):
        raise TimingError('every output folder must be new and distinct')
    return {'kind': 'timing', 'seconds': seconds, 'grace_seconds': grace_seconds, 'max_hold_seconds': max_hold_seconds, 'settle_seconds': settle_seconds,
            'quiet_cores': quiet_cores, 'avoid': list(avoid), 'scenario': str(scenario_path), 'run_command': list(run_command),
            'entries': [{'role': r, 'label': l, 'copy': c, 'commit': k, 'scenario': str(scenario_path), 'output': o} for r, l, c, k, o in entries]}


def run_command(python, scene_py, regress_dir, control_dir):
    """the plan's run_command: the scene driver around the adapter ({copy} {commit} {output} {seconds} {scenario} are the worker's)"""
    return [python, '-I', '-u', str(scene_py), 'run', '--copy', '{copy}', '--commit', '{commit}', '--output', '{output}', '--seconds', '{seconds}',
            '--scenario', '{scenario}', '--regress-dir', str(regress_dir), '--control-dir', str(control_dir)]


def null_pair_plan(label, copy, commit, outputs, scenario_path, run_command_, **kw):
    """four entries on one copy and one commit (reference, candidate, reference, candidate: all the same build)"""
    if len(outputs) != 4:
        raise TimingError('a null pair has four outputs')
    entries = [(r, label, copy, commit, o) for r, o in zip(['reference', 'candidate'] * 2, outputs)]
    return make_plan(entries, scenario_path, run_command_, **kw)


def spread(values):
    """(max / min) - 1 of the values; None for fewer than two or a non-positive one"""
    v = [x for x in values if isinstance(x, (int, float))]
    if len(v) < 2 or min(v) <= 0:
        return None
    return max(v) / min(v) - 1.0


def null_pair_verdict(runs, spread_max=SPREAD_MAX, gap_max=GAP_MAX):
    """runs: the worker's PLAN.runs.json rows [{role, label, result: {valid, disturbed, value, ...}}] in run order.
    Returns {'verdict': 'usable' | 'noisy' | 'invalid', 'ref_spread', 'cand_spread', 'gap', 'rows': [...], 'reasons': [...]}; every row is kept.
    usable: all four valid and not disturbed, both same-arm spreads <= spread_max, |mean(cand) - mean(ref)| / mean(ref) <= gap_max.
    noisy: valid but a band is broken (the scene cannot decide a change smaller than the larger of the spread and the gap). invalid: a run is not."""
    rows, reasons = [], []
    ref, cand = [], []
    for r in runs:
        res = r.get('result') or {}
        row = {'role': r.get('role'), 'valid': res.get('valid'), 'disturbed': res.get('disturbed'), 'value': res.get('value'),
               'others': res.get('others_cores_window'), 'peak': res.get('others_cores_peak'), 'problem': res.get('problem')}
        rows.append(row)
        if not res.get('valid') or res.get('disturbed') or not isinstance(res.get('value'), (int, float)):
            reasons.append(f"{r.get('role')} run: valid={res.get('valid')} disturbed={res.get('disturbed')} problem={res.get('problem')}")
        else:
            (ref if r.get('role') == 'reference' else cand).append(res['value'])
    if len(runs) < 4 or reasons:
        return {'verdict': 'invalid', 'ref_spread': spread(ref), 'cand_spread': spread(cand), 'gap': None, 'rows': rows, 'reasons': reasons or ['fewer than four runs']}
    rs, cs = spread(ref), spread(cand)
    gap = abs(sum(cand) / len(cand) - sum(ref) / len(ref)) / (sum(ref) / len(ref))
    if rs is None or cs is None:
        return {'verdict': 'invalid', 'ref_spread': rs, 'cand_spread': cs, 'gap': gap, 'rows': rows, 'reasons': ['a spread cannot be computed']}
    if rs > spread_max:
        reasons.append(f'reference spread {100 * rs:.1f}% > {100 * spread_max:.0f}%')
    if cs > spread_max:
        reasons.append(f'candidate spread {100 * cs:.1f}% > {100 * spread_max:.0f}%')
    if gap > gap_max:
        reasons.append(f'arm gap {100 * gap:.1f}% > {100 * gap_max:.0f}% (the same build on both arms)')
    return {'verdict': 'usable' if not reasons else 'noisy', 'ref_spread': rs, 'cand_spread': cs, 'gap': gap, 'rows': rows, 'reasons': reasons}


def write_json(path, obj):
    p = Path(path)
    if p.exists():
        raise TimingError(f'{p} exists: every plan and scenario is written once')
    p.write_text(json.dumps(obj, indent=1) + '\n')
