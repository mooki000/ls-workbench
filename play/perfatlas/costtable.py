#!/usr/bin/env python3
"""The cost table: what each viewpoint costs, and what each condition takes away, from the folders a driven run leaves.

A run folder holds `windows.jsonl` (driver.py), `game_log.txt` (the game's log, whose byte offsets the windows carry), the frame trace
`trace/frames.csv` (RX_VK_FRAME_TRACE=1, HOS_PROFILE_DIR=<folder>/trace) or, failing that, `present.csv`, and `run.json` (the run's plan
entry and what it ran on). Every number is cut from those files by the window's own clock and offsets; a number that is not there is
None, never zero.

**Ordinary runs only give observations.** Whatever the machine was doing beside the game is in these numbers; the table says so in
its header and never calls a difference a speed result. A condition's "price" here is the observed difference to the baseline of the same
run (the same process, a few minutes apart); the exclusive paired worker prices a part for a claim (skill ls-perf-atlas).
"""
import json
import math
from pathlib import Path

from . import cpu as cpumod, parse

LABEL = 'ordinary run, no speed claim'
TRACE_MEANS = ('game_ms', 'render_ms', 'gpu_wait_ms', 'pace_ms', 'wait_render_ms', 'wait_gpu_ms', 'wait_main_ms', 'main_cpu_ms', 'main_blocked_ms', 'main_vcore_ms',
               'draw_ms', 'desc_ms', 'cmds_ms', 'memsync_ms', 'slot_wait_ms', 'gpu_behind_ms', 'translate_ms', 'pipe_wait_ms', 'pipelines_ms',
               'uploads_ms', 'tex_lookup_ms', 'readback_wait_ms', 'late_wait_ms', 'left_out', 'uploads', 'upload_mb', 'textures_gone')
SCENE_MEDIANS = ('draws', 'skipped', 'dispatches', 'passes', 'barriers', 'submits', 'upload_mb', 'ub_updates', 'textures', 'texture_mb')
REPLAY_PARTS = ('submit', 'present', 'draw', 'descriptors', 'texture_lookups', 'uploads', 'pipelines', 'programs', 'commands', 'dispatch', 'memory_sync',
                'queue_submit', 'slot_wait')
MIN_FRAMES = 120      # a window with fewer frames than this is flagged (a second of a 120 fps scene, two of a 60 fps)
DRIFT_LIMIT = 0.05    # the spread of repeated baseline visits that the table calls steady (the exclusive worker's own same-arm tolerance)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


class Run:
    """one driven run's files, read once"""
    def __init__(self, folder):
        self.dir = Path(folder)
        self.meta = json.loads((self.dir / 'run.json').read_text()) if (self.dir / 'run.json').exists() else {}
        self.windows = [json.loads(ln) for ln in (self.dir / 'windows.jsonl').read_text().splitlines() if ln.strip()] if (self.dir / 'windows.jsonl').exists() else []
        self.ops = [json.loads(ln) for ln in (self.dir / 'ops.jsonl').read_text().splitlines() if ln.strip()] if (self.dir / 'ops.jsonl').exists() else []
        log = self.dir / 'game_log.txt'
        self.records = parse.parse_log(parse.read_lines(log)) if log.exists() else []
        trace = self.dir / 'trace' / 'frames.csv'
        self.trace = parse.read_trace(trace) if trace.exists() else []
        present = self.dir / 'present.csv'
        self.present = parse.read_present(present) if present.exists() else []

    @property
    def id(self):
        return self.meta.get('id') or self.dir.name


def pass_summary(records, w):
    """the clean GPU pass timing blocks of a window: those after the first one inside it (the first covers presents from before the
    window began). Returns None when the run had no pass timing; {'partial': True, ...} when only the first block fell in."""
    blocks = [r for r in records if r['kind'] == 'pass_timing' and w['log0'] <= r['offset'] < w['log1']]
    if not blocks:
        return None
    clean, partial = blocks[1:], len(blocks) == 1
    use = clean or blocks
    n = len(use)
    total = mean(b['total_ms'] for b in use)
    rec = mean(b['rec_ms'] for b in use)
    kinds = {}
    for b in use:
        for k in b['kinds']:
            key = (k['cb'], k['name'])
            a = kinds.setdefault(key, {'ms': [], 'items': [], 'spans': [], 'mb': []})
            for f in ('ms', 'items', 'spans', 'mb'):
                a[f].append(k[f])
    kind_rows = sorted(({'cb': cb, 'name': name, 'ms': mean(a['ms']), 'items': mean(a['items']), 'spans': mean(a['spans']), 'mb': mean(a['mb'])}
                        for (cb, name), a in kinds.items()), key=lambda r: -r['ms'])
    progs = {}
    for b in use:
        for p in b['programs']:
            a = progs.setdefault(p['name'], {'ms': [], 'draws': []})
            a['ms'].append(p['ms'])
            a['draws'].append(p['draws'])
    prog_rows = sorted(({'name': name, 'ms': mean(a['ms']), 'draws': mean(a['draws'])} for name, a in progs.items()), key=lambda r: -r['ms'])
    return {'blocks': n, 'partial': partial, 'total_ms': total, 'rec_ms': rec, 'kinds': kind_rows, 'programs': prog_rows[:10]}


def classify_bound(parts, frame_mean):
    """which part of the frame is the longest by the game's own split (the FPS counter's Breakdown: game, render, gpu_wait, pace):
    'pace' when pacing is over 30% of the frame (a capped or pacing-limited scene: the others are not the limit), else 'game-cpu',
    'render-cpu' or 'gpu-wait'. None when the split is not there. A hint to read with the pass timing, not a verdict."""
    g, r, w, p = (parts.get(k) for k in ('game_ms', 'render_ms', 'gpu_wait_ms', 'pace_ms'))
    if None in (g, r, w, p) or not frame_mean:
        return None
    if p > 0.3 * frame_mean:
        return 'pace'
    return max((g, 'game-cpu'), (r, 'render-cpu'), (w, 'gpu-wait'))[1]


def window_row(run, w):
    rows = parse.in_window(run.trace, w['t0_ns'], w['t1_ns']) if run.trace else []
    source = 'trace'
    if not rows and run.present:
        rows, source = parse.in_window(run.present, w['t0_ns'], w['t1_ns']), 'present'
    st = parse.describe([r['ms'] for r in rows])
    parts = {k: parse.mean_of(rows, k) for k in TRACE_MEANS} if source == 'trace' else {}
    recs = [r for r in run.records if w['log0'] <= r['offset'] < w['log1']]
    scene = [r for r in recs if r['kind'] == 'scene']
    gpu = [r for r in recs if r['kind'] == 'gpu_time']
    replay = [r for r in recs if r['kind'] == 'replay']
    left = [r for r in recs if r['kind'] == 'left_out']
    mirrors = [r for r in recs if r['kind'] == 'mirrors']
    log = {k: median(r[k] for r in scene) for k in SCENE_MEDIANS}
    log.update(gpu_ms=median(r['gpu_ms'] for r in gpu), gpu_longest_ms=median(r['longest_ms'] for r in gpu), scene_lines=len(scene))
    log['textures_made'] = median(r['textures_made'] for r in gpu)
    log['draws_left_out'] = median(r['draws_left_out'] for r in left)
    log['sync_mb'] = median(r['sync_mb'] for r in mirrors)
    log['replay'] = {k: median(r.get(k) for r in replay) for k in REPLAY_PARTS}
    flags = []
    if not rows:
        flags.append('no frame file in the window')
    elif len(rows) < MIN_FRAMES:
        flags.append(f'only {len(rows)} frames')
    if w.get('arrived') is False:
        flags.append('did not arrive')
    if w.get('camera_ok') is False:
        flags.append('camera not at the aim')
    if w['kind'] == 'viewpoint' and (w.get('moved_m') or 0) > 1.0:
        flags.append(f"moved {w['moved_m']} m")
    if w.get('event_seen'):
        flags.append('an event ran')
    for name in w.get('options_failed') or []:
        flags.append(f'the setting {name} was NOT taken (the window ran at the earlier value)')
    if w['kind'] == 'walk' and w.get('result') != 'reached':
        flags.append(f"walk {w.get('result')}")
    ps = pass_summary(run.records, w)
    if ps and ps['partial']:
        flags.append('pass timing: one block only (it covers earlier frames)')
    cpu = cpu_row(w.get('cpu'), st['n'])
    return {'run': run.id, 'n': w['n'], 'kind': w['kind'], 'cond': w['cond'], 'vp': w['vp'], 'visit': w.get('visit', 1), 'tod': w.get('tod'),
            'dwell_s': round((w['t1_ns'] - w['t0_ns']) / 1e9, 1), 'source': source, 'frame': st, 'parts': parts,
            'bound': classify_bound(parts, st['mean']) if source == 'trace' else None, 'log': log, 'pass': ps,
            'cpu': cpu, 'shot': w.get('shot'), 'flags': flags, 'status0': w.get('status0'), 'status1': w.get('status1')}


def cpu_row(snaps, frames):
    """the window's CPU record: cores the game used, CPU ms a frame (all threads and by thread group), the machine's other work (mean and
    the worst 5-second stretch) and whether the window met the quiet rule; None when the run sampled no CPU"""
    c = cpumod.window_cpu(snaps) if snaps else None
    if c is None:
        return None
    ms_per_tick = 1000.0 / cpumod.HZ
    per = (lambda ticks: ticks * ms_per_tick / frames) if frames else (lambda ticks: None)
    groups = sorted(((g, per(t)) for g, t in c['groups'].items()), key=lambda kv: -(kv[1] or 0))
    return {'game_cores': c['game_cores'], 'cpu_ms_frame': per(c['game_ticks']), 'others_mean': c['others_mean'], 'others_peak': c['others_peak'],
            'quiet': c['quiet'], 'groups': groups[:6]}


def build_rows(runs):
    out = []
    for run in runs:
        out += [window_row(run, w) for w in run.windows if w.get('kind') != 'sweep']
    return out


def sweep_rows(runs):
    """the scout's views: [{run, vp, yaw, camera, pitch, draws, draws_max, fps, arrived, camera_ok}], most draws first"""
    out = []
    for run in runs:
        for w in run.windows:
            if w.get('kind') != 'sweep':
                continue
            out.append({'run': run.id, 'vp': w['vp'], 'yaw': (w.get('aim') or [None])[0], 'camera': w.get('camera'), 'pitch': w.get('pitch'),
                        'draws': w.get('draws_median'), 'draws_max': w.get('draws_max'), 'fps': w.get('fps_median'), 'arrived': w.get('arrived'),
                        'camera_ok': w.get('camera_ok'), 'pos': (w.get('status1') or {}).get('pos')})
    out.sort(key=lambda r: -(r['draws'] or -1))
    return out


def sweep_markdown(rows, top=20):
    lines = ['## Scout: draws a frame by place and view (counts: they do not move with the machine\'s load)', '',
             '| place | yaw asked | camera read | pitch | draws (median) | max | arrived | camera ok |', '|---|---|---|---|---|---|---|---|']
    for r in rows[:top]:
        lines.append(f"| {r['vp']} | {fmt(r['yaw'], 0)} | {fmt(r['camera'], 0)} | {fmt(r['pitch'], 0)} | {fmt(r['draws'], 0)} | {fmt(r['draws_max'], 0)} | "
                     f"{'yes' if r['arrived'] else 'NO'} | {'yes' if r['camera_ok'] else 'NO'} |")
    byplace = {}
    for r in rows:
        if r['draws'] is not None and r['camera_ok']:
            byplace.setdefault(r['vp'], []).append(r['draws'])
    if byplace:
        lines += ['', '| place | views | least | most | spread |', '|---|---|---|---|---|']
        for vp, d in sorted(byplace.items(), key=lambda kv: -max(kv[1])):
            lines.append(f'| {vp} | {len(d)} | {min(d):.0f} | {max(d):.0f} | {max(d) - min(d):.0f} |')
    return '\n'.join(lines) + '\n'


def reference_rows(rows):
    """{(run, vp): the first baseline row} and {vp: the first baseline row of any run}: what the deltas are against"""
    own, anyrun = {}, {}
    for r in rows:
        if r['cond'] == 'baseline' and r['visit'] == 1:
            own.setdefault((r['run'], r['vp']), r)
            anyrun.setdefault(r['vp'], r)
    return own, anyrun


def add_deltas(rows):
    """each row gets 'delta' against the same viewpoint's baseline in its own run (else in another run, marked cross_run): the frame
    mean, the game and render parts, the draws and the GPU time; None where either side lacks the number"""
    own, anyrun = reference_rows(rows)

    def d(a, b):
        return None if a is None or b is None else a - b

    for r in rows:
        ref = own.get((r['run'], r['vp']))
        cross = False
        if ref is None:
            ref, cross = anyrun.get(r['vp']), True
        if ref is None or ref is r:
            r['delta'] = None
            continue
        r['delta'] = {'cross_run': cross, 'against': f"{ref['run']}/{ref['cond']}",
                      'frame_ms': d(r['frame']['mean'], ref['frame']['mean']), 'game_ms': d(r['parts'].get('game_ms'), ref['parts'].get('game_ms')),
                      'render_ms': d(r['parts'].get('render_ms'), ref['parts'].get('render_ms')), 'draws': d(r['log']['draws'], ref['log']['draws']),
                      'gpu_ms': d(r['log']['gpu_ms'], ref['log']['gpu_ms']), 'frame_pct': (None if d(r['frame']['mean'], ref['frame']['mean']) is None
                                                                                    else 100.0 * (r['frame']['mean'] - ref['frame']['mean']) / ref['frame']['mean'])}
    return rows


def drift(rows):
    """per viewpoint and run, the spread of the frame mean over the baseline visits ((max / min) - 1); {(run, vp): spread}"""
    groups = {}
    for r in rows:
        if r['cond'] in ('baseline', 'baseline-again') and r['frame']['mean']:
            groups.setdefault((r['run'], r['vp']), []).append(r['frame']['mean'])
    return {k: max(v) / min(v) - 1 for k, v in groups.items() if len(v) >= 2}


def family_prices(rows, families):
    """{family: {'viewpoints': n, 'frame_ms': mean delta, 'draws': mean delta, 'game_ms': ...}} over the rows of conditions with that
    family (families: {condition id: family}); only same-run deltas, the others are not mixed in"""
    acc = {}
    for r in rows:
        fam = families.get(r['cond'])
        dl = r.get('delta')
        if not fam or fam == 'baseline' or not dl or dl['cross_run']:
            continue
        a = acc.setdefault(fam, {'frame_ms': [], 'game_ms': [], 'render_ms': [], 'draws': [], 'gpu_ms': [], 'conds': set()})
        for k in ('frame_ms', 'game_ms', 'render_ms', 'draws', 'gpu_ms'):
            a[k].append(dl[k])
        a['conds'].add(r['cond'])
    return {fam: {'viewpoints': len([x for x in a['frame_ms'] if x is not None]), 'conds': sorted(a['conds']), **{k: mean(a[k]) for k in ('frame_ms', 'game_ms', 'render_ms', 'draws', 'gpu_ms')}}
            for fam, a in acc.items()}


def fmt(x, nd=1):
    return '-' if x is None else (f'{x:.{nd}f}' if isinstance(x, float) else str(x))


def signed(x, nd=1):
    return '-' if x is None else f'{x:+.{nd}f}'


def markdown(rows, families=None, title='Cost table'):
    """the table as markdown: every window one row; the pass shares; the family prices; the drift of repeated baselines"""
    add_deltas(rows)
    lines = [f'## {title}', '', f'**{LABEL}.** Other work on this PC is in every number; a difference here is an observation of one process, '
             f'not a measurement. Frame ms are the renderer trace\'s interval (headless: CPU after the offscreen submit).', '',
             '| run | cond | viewpoint | v | s | frames | mean ms | p99 | low1 fps | game | render | gpu wait | pace | bound | draws | GPU ms | submit | d frame | d draws | flags |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        f, p, lg = r['frame'], r['parts'], r['log']
        dl = r.get('delta') or {}
        mark = ' (cross-run)' if dl.get('cross_run') else ''
        lines.append('| ' + ' | '.join([r['run'], r['cond'], r['vp'], str(r['visit']), fmt(r['dwell_s']), str(f['n']), fmt(f['mean'], 2), fmt(f['p99'], 1), fmt(f['low1_fps'], 0),
                                        fmt(p.get('game_ms'), 2), fmt(p.get('render_ms'), 2), fmt(p.get('gpu_wait_ms'), 2), fmt(p.get('pace_ms'), 2), r['bound'] or '-',
                                        fmt(lg['draws'], 0), fmt(lg['gpu_ms'], 1), fmt(lg['replay'].get('submit'), 1),
                                        signed(dl.get('frame_ms'), 2) + mark if dl else '-', signed(dl.get('draws'), 0) if dl else '-', '; '.join(r['flags']) or '-']) + ' |')
    cpus = [r for r in rows if r['cpu']]
    if cpus:
        lines += ['', '### CPU and the quiet record (the game\'s CPU time a frame barely moves with the machine\'s load; wall frame ms does)', '',
                  '| run | cond | viewpoint | v | frame ms | game cores | CPU ms/frame | others mean | others peak | quiet rule | main on CPU | main blocked | main waits for a core | top thread groups (ms/frame) |',
                  '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
        for r in cpus:
            c, p = r['cpu'], r['parts']
            top = ', '.join(f'{g} {v:.1f}' for g, v in c['groups'][:4])
            lines.append('| ' + ' | '.join([r['run'], r['cond'], r['vp'], str(r['visit']), fmt(r['frame']['mean'], 1), fmt(c['game_cores'], 1), fmt(c['cpu_ms_frame'], 1),
                                            fmt(c['others_mean'], 1), fmt(c['others_peak'], 1), 'met' if c['quiet'] else 'NOT met', fmt(p.get('main_cpu_ms'), 1),
                                            fmt(p.get('main_blocked_ms'), 1), fmt(p.get('main_vcore_ms'), 2), top]) + ' |')
    passes = [r for r in rows if r['pass']]
    if passes:
        lines += ['', '### GPU pass timing (RX_VK_PASS_TIMING: every span waits for the GPU, so the spans add up to more than an unprofiled frame; read the shares)', '']
        for r in passes:
            ps = r['pass']
            lines.append(f"**{r['run']} / {r['cond']} / {r['vp']} v{r['visit']}**: {ps['blocks']} block(s), {fmt(ps['total_ms'], 2)} ms a frame in all, recordings {fmt(ps['rec_ms'], 2)} ms"
                         + (' (partial)' if ps['partial'] else ''))
            lines.append('')
            lines.append('| cb | kind | ms | items | spans |')
            lines.append('|---|---|---|---|---|')
            for k in ps['kinds'][:8]:
                lines.append(f"| {k['cb']} | {k['name']} | {fmt(k['ms'], 3)} | {fmt(k['items'], 0)} | {fmt(k['spans'], 1)} |")
            lines.append('')
    if families:
        prices = family_prices(rows, families)
        if prices:
            lines += ['', '### What each part took away (mean difference to the same run\'s baseline; negative = cheaper without it)', '',
                      '| part | conditions | viewpoints | d frame ms | d game ms | d render ms | d draws | d GPU ms |', '|---|---|---|---|---|---|---|---|']
            for fam, v in sorted(prices.items(), key=lambda kv: (kv[1]['frame_ms'] is None, kv[1]['frame_ms'] or 0)):
                lines.append(f"| {fam} | {', '.join(v['conds'])} | {v['viewpoints']} | {signed(v['frame_ms'], 2)} | {signed(v['game_ms'], 2)} | {signed(v['render_ms'], 2)} | {signed(v['draws'], 0)} | {signed(v['gpu_ms'], 2)} |")
    dr = drift(rows)
    if dr:
        lines += ['', '### Revisit drift (the same viewpoint in the baseline blocks of one run)', '', '| run | viewpoint | spread of the mean | steady (<= 5%) |', '|---|---|---|---|']
        for (run, vp), s in sorted(dr.items()):
            lines.append(f"| {run} | {vp} | {100 * s:.1f}% | {'yes' if s <= DRIFT_LIMIT else 'NO'} |")
    return '\n'.join(lines) + '\n'


def to_json(rows):
    return json.dumps(add_deltas(rows), indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
