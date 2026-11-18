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
LAG_S = 12   # (first present after the game's start: 6 to 12 s in the atlas's holds; the window's `to` must leave this much of the wall)
FIXED_ENV = {'HOS_OUTPUT_RES': '2048x1152', 'HOS_DYNRES': '0', 'HOS_FPS_MAX': '240', 'RX_VK_DLSS': '0', 'RX_VK_FRAMEGEN': '0',
             'RX_VK_AUTO_TUNE': '0', 'RX_VK_AUTO_PRESET': '0', 'RX_VK_SHOT_EVERY': '1000000'}
SPREAD_MAX = 0.05
GAP_MAX = 0.03
AVOID_WIDGET = {'every': 900, 'at': 535, 'for': 12}   # the desktop's usage widget (skill ls-perf-compare)


class TimingError(ValueError):
    pass


def make_scenario(label, save, scene, good_max, bad_min, from_s=45, to_s=95, boot_input=None, measure='mean_ms',
                  env=None, seconds=110, grace_seconds=5, repetitions=2, extra_expect=()):
    """the scenario JSON for one viewpoint. `scene` is the atlas_scene object (map, aim_yaw, pos [x, z], control_by_s, ready_by_s, ...).
    good_max and bad_min are the decision bands in the measure's own unit (worseness: higher is worse); both are required."""
    if good_max is None or bad_min is None:
        raise TimingError('the decision bands (good_max, bad_min) are set before the results: pass both')
    if not good_max < bad_min:
        raise TimingError(f'good_max {good_max} must be below bad_min {bad_min}')
    if not 0 < from_s < to_s <= seconds - LAG_S:
        raise TimingError(f'the window {from_s}..{to_s} must end {LAG_S} s before the run does ({seconds} s): the frame clock starts when the game '
                          f'first presents (6 to 12 s after its start in the first holds) and the wall limit counts from the start')
    if scene.get('ready_by_s', 1e9) > from_s - 2:
        raise TimingError(f"the aim must be done by {from_s - 2} s (the window starts at {from_s}): ready_by_s is {scene.get('ready_by_s')}")
    run_extra = {'input': boot_input} if boot_input else {}   # (default: no input script: the scene driver presses A through the title itself)
    e = dict(FIXED_ENV)
    e.update(env or {})
    return {'schema_version': 1, 'name': label, 'kind': 'timing', 'seconds': seconds, 'grace_seconds': grace_seconds, 'repetitions': repetitions,
            'max_retries': 0, 'quiet_cores': 3.0, 'quiet_peak_cores': 6.0, 'repeat_tolerance': SPREAD_MAX,
            'measure': {'name': measure, 'direction': 'higher-worse', 'good_max': good_max, 'bad_min': bad_min},
            'run': {'label': label, 'user': save, 'anchor': 'first', 'from': from_s, 'to': to_s, 'readback_csv': False,
                    'sample_seconds': 2.5, 'atlas_scene': scene, 'env': e, 'expect_log': ['2048x1152', *extra_expect],
                    'forbid_log': ['device lost'], **run_extra}}


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


def pair_rows(runs):
    """one row a run from the worker's PLAN.runs.json rows: mean ms, p99, 1% low fps, the game's CPU ms a frame (cores x frame ms), the others' cores, the kernel lines"""
    rows = []
    for r in runs:
        res = r.get('result') or {}
        m = res.get('measures') or {}
        mean = res.get('value') if isinstance(res.get('value'), (int, float)) else m.get('mean_ms')
        cores = (res.get('threads') or {}).get('total')
        rows.append({'role': r.get('role'), 'valid': res.get('valid'), 'disturbed': res.get('disturbed'), 'mean_ms': mean, 'p99_ms': m.get('p99_ms'),
                     'low1_fps': m.get('low1_fps'), 'frames': m.get('frames'), 'cpu_ms_frame': (cores * mean) if isinstance(cores, (int, float)) and isinstance(mean, (int, float)) else None,
                     'others': res.get('others_cores_window', res.get('others_cores')), 'peak': res.get('others_cores_peak'),
                     'kernel': (res.get('kernel_errors') or 0) + (res.get('nvrm_lines') or 0), 'problem': res.get('problem')})
    return rows


def pair_verdict(runs, spread_max=SPREAD_MAX, gap_min=GAP_MAX, quiet_mean=3.0, quiet_peak=6.0):
    """the verdict of a reference/candidate/reference/candidate pair (two builds), by rules set before the results:
    valid only when all four runs are valid, not disturbed, and the others used under `quiet_mean` cores on average and under `quiet_peak` at the highest 5 s;
    then the same-arm spreads must be at most `spread_max` ('noisy' otherwise: nothing is averaged away); a gap (candidate against reference, the mean of the means)
    of `gap_min` or more that is also larger than both spreads is a result ('faster' / 'slower'); anything smaller is 'no difference beyond the spread'.
    Returns {'verdict', 'gap', 'ref_spread', 'cand_spread', 'ref_mean', 'cand_mean', 'rows', 'reasons'}; every row is kept."""
    rows = pair_rows(runs)
    reasons = []
    for r in rows:
        if not r['valid'] or r['disturbed'] or not isinstance(r['mean_ms'], (int, float)):
            reasons.append(f"{r['role']} run: valid={r['valid']} disturbed={r['disturbed']} problem={r['problem']}")
        elif r['others'] is None or r['peak'] is None:
            reasons.append(f"{r['role']} run: the others' cores were not recorded")
        elif r['others'] >= quiet_mean or r['peak'] >= quiet_peak:
            reasons.append(f"{r['role']} run: not quiet (others {r['others']:.1f} cores mean, {r['peak']:.1f} peak; the rule is under {quiet_mean:.0f} and {quiet_peak:.0f})")
    ref = [r['mean_ms'] for r in rows if r['role'] == 'reference' and isinstance(r['mean_ms'], (int, float))]
    cand = [r['mean_ms'] for r in rows if r['role'] == 'candidate' and isinstance(r['mean_ms'], (int, float))]
    out = {'rows': rows, 'reasons': reasons, 'gap': None, 'ref_spread': spread(ref), 'cand_spread': spread(cand), 'ref_mean': None, 'cand_mean': None}
    if len(rows) < 4 or len(ref) < 2 or len(cand) < 2:
        out.update(verdict='invalid', reasons=reasons or ['fewer than four runs'])
        return out
    out['ref_mean'], out['cand_mean'] = sum(ref) / len(ref), sum(cand) / len(cand)
    out['gap'] = out['cand_mean'] / out['ref_mean'] - 1.0
    if reasons:
        out['verdict'] = 'invalid'
        return out
    rs, cs = out['ref_spread'], out['cand_spread']
    if rs is None or cs is None or rs > spread_max or cs > spread_max:
        out['verdict'] = 'noisy'
        out['reasons'] = [f'same-arm spread {100 * x:.1f}% > {100 * spread_max:.0f}% ({n})' for n, x in (('reference', rs), ('candidate', cs)) if x is not None and x > spread_max] or ['a spread cannot be computed']
        return out
    gap = out['gap']
    if abs(gap) >= gap_min and abs(gap) > max(rs, cs):
        out['verdict'] = 'faster' if gap < 0 else 'slower'
    else:
        out['verdict'] = 'no difference beyond the spread'
    return out


def write_json(path, obj):
    p = Path(path)
    if p.exists():
        raise TimingError(f'{p} exists: every plan and scenario is written once')
    p.write_text(json.dumps(obj, indent=1) + '\n')
