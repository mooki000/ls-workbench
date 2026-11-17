#!/usr/bin/env python3
"""perf atlas: viewpoints and walks in a game area, conditions that price a part of the frame, and the cost table. Game-free except
`hold`, which drives an owned game inside a hold the caller already queued.

  atlas.py check LIB.json                                   validate a library, print what it holds
  atlas.py plan LIB.json --area A --tod day --viewpoints a,b [--walks w] --conditions baseline,actors-people,... \\
                         [--run-budget 420] [--hold-limit 480] [--env NAME=VALUE ...] --out plan.json
  atlas.py merge PLAN.json ... --hold-limit 1100 --out plan.json   (several legs, each planned with its own --prefix, packed again)
  atlas.py hold PLAN.json --hold h1 --copy <game copy> --out OUT [--hs-run <path>] [--saves <folder>] [--wall <s>]
  atlas.py table RUNDIR... [--library LIB.json] [--md out.md] [--json out.json]
  atlas.py coverage --field field.json --areas areas.json [--cells cells.json] [--next N]

`hold` runs the plan's hold: for each run a private control folder, the game through the workbench's run wrapper (`hs-run.sh`, which asks
the GPU gate itself: the caller's waiter holds the quiet and measuring locks, nothing else is taken here), the driver against its
control socket, and a folder OUT/<run>/ with windows.jsonl, ops.jsonl, game_log.txt, trace/frames.csv, present.csv, summary.txt and
run.json. It never signals a process it did not start. It is ordinary-run machinery: pictures, counts and shares, no speed claim.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from perfatlas import costtable, coverage, model, plan as planner  # noqa: E402
from perfatlas.bridge import Bridge, BridgeError  # noqa: E402
from perfatlas.driver import Driver  # noqa: E402

# Fixed cost for every atlas run: no dynamic resolution, no Auto preset, one output size, a cap well above what a scene reaches, no
# upscaler and no frame generation, and no periodic pictures (the driver takes its own, outside the windows).
FIXED_COST_ENV = [('HOS_DYNRES', '0'), ('RX_VK_AUTO_TUNE', '0'), ('RX_VK_AUTO_PRESET', '0'), ('HOS_OUTPUT_RES', '2048x1152'),
                  ('HOS_FPS_MAX', '240'), ('RX_VK_DLSS', '0'), ('RX_VK_FRAMEGEN', '0'), ('RX_VK_SHOT_EVERY', '1000000')]


def _harness():
    """the folder with hs-run.sh and saves/: PERFATLAS_HARNESS, else the folder this package sits two levels under (the live harness keeps it in
    playthrough/perfatlas/), else the workbench repository's harness/ folder"""
    if os.environ.get('PERFATLAS_HARNESS'):
        return Path(os.environ['PERFATLAS_HARNESS'])
    root = HERE.parent.parent
    return root if (root / 'hs-run.sh').is_file() else root / 'harness'


HARNESS = _harness()


def say(text):
    print(time.strftime('%H:%M:%S ') + text, flush=True)


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def cmd_check(a):
    lib = model.load_library(read_json(a.library))
    print(f'areas {sorted(lib.areas)}; {len(lib.viewpoints)} viewpoints, {len(lib.walks)} walks, {len(lib.conditions)} conditions of its own')
    cat = model.builtin_conditions()
    print(f'catalogue: {len(cat)} conditions: ' + ', '.join(sorted(cat)))
    for v in lib.viewpoints.values():
        print(f'  {v.id:<28} {v.area:<12} {v.tod:<6} dwell {v.dwell_s:g}s  {v.pos_line()}')
    return 0


def parse_env(items):
    out = []
    for it in items or []:
        name, sep, value = it.partition('=')
        if not sep or not model.ENV_NAME_RE.match(name) or not model.ENV_VALUE_RE.match(value):
            raise SystemExit(f'--env {it!r} is not NAME=VALUE (upper case name, a value without white space)')
        out.append((name, value))
    return out


def cmd_plan(a):
    lib = model.load_library(read_json(a.library))
    ids = [s for s in a.viewpoints.split(',') if s] if a.viewpoints else []
    wids = [s for s in a.walks.split(',') if s] if a.walks else []
    conds = model.conditions_for(lib, [s for s in a.conditions.split(',') if s])
    over = {k: v for k, v in (('dwell_s', a.dwell), ('settle_s', a.settle), ('min_frames', a.min_frames)) if v is not None}
    runs = planner.plan_runs(lib, ids, conds, wids, area=a.area, tod=a.tod, run_budget_s=a.run_budget, env=parse_env(a.env), over=over, prefix=a.prefix)
    holds = planner.plan_holds(runs, a.hold_limit)
    fams = {c.id: (c.family or c.kind) for c in conds}
    Path(a.out).write_text(json.dumps({'schema_version': 1, 'kind': 'perfatlas-plan', 'area': a.area, 'tod': a.tod, 'holds': holds, 'families': fams,
                                       'fixed_env': FIXED_COST_ENV}, indent=1) + '\n')
    print(planner.summary(holds))
    return 0


def cmd_merge(a):
    plans = [read_json(p) for p in a.plans]
    runs = [r for pl in plans for h in pl['holds'] for r in h['runs']]
    ids = [r['id'] for r in runs]
    if len(ids) != len(set(ids)):
        raise SystemExit(f'run ids repeat: {ids} (plan each leg with its own --prefix)')
    fams = {}
    for pl in plans:
        fams.update(pl.get('families', {}))
    holds = planner.plan_holds(runs, a.hold_limit)
    Path(a.out).write_text(json.dumps({'schema_version': 1, 'kind': 'perfatlas-plan', 'holds': holds, 'families': fams, 'fixed_env': FIXED_COST_ENV}, indent=1) + '\n')
    print(planner.summary(holds))
    return 0


def find_log(run_dir, wait_s=900):   # (the wrapper may wait for the GPU gate first: the game's wall counts from its start, the log's first line)
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        logs = sorted(Path(run_dir).glob('*_log.txt'))
        if logs:
            return logs[0]
        time.sleep(0.5)
    return None


def run_one(a, run, outdir, cap_s=None):
    """one run of a hold: the game through hs-run.sh, the driver beside it, the files collected"""
    out = Path(outdir) / run['id']
    out.mkdir(parents=True, exist_ok=False)
    (out / 'trace').mkdir()
    tag = f"PerfAtlas-{run['id']}-{time.strftime('%m%d-%H%M%S')}"
    ctl = Path(tempfile.mkdtemp(prefix='pa-', dir='/tmp'))
    os.chmod(ctl, 0o700)
    sock, live = ctl / 'c.sock', ctl / 'live.txt'
    live.write_text('')
    env = [('HOS_CONTROL', str(sock)), ('HOS_INPUT_LIVE', str(live)), ('RX_VK_FRAME_TRACE', '1'), ('HOS_PROFILE_DIR', str(out / 'trace'))]
    env += list(FIXED_COST_ENV) + [tuple(p) for p in run['env']]
    seen, uniq = set(), []
    for k, v in reversed(env):   # (a later entry wins: the run's own switches over the fixed ones)
        if k not in seen:
            seen.add(k)
            uniq.append((k, v))
    env = list(reversed(uniq))
    save = run['save']
    if not os.path.isabs(save):
        save = str(Path(a.saves) / save)
    wall = int(a.wall or min(450, max(120, run['est_s'] + 90)))   # (an ordinary game: 450 s at most, boot included)
    if cap_s is not None:
        wall = min(wall, max(60, int(cap_s)))   # (the hold's own end: the game is over before its waiter's timeout)
    cmd = [a.hs_run, a.copy, tag, str(wall), '--user', save] + [f'{k}={v}' for k, v in env]
    say(f"run {run['id']}: {tag} wall {wall}s save {Path(save).name}; env " + ' '.join(f'{k}={v}' for k, v in run['env']))
    (out / 'launch.txt').write_text(' '.join(cmd) + '\n')
    stdout = open(out / 'hs-run.out', 'w')
    proc = subprocess.Popen(cmd, stdout=stdout, stderr=subprocess.STDOUT, start_new_session=True)
    run_dir = Path(a.copy) / 'runs' / tag
    result = {'aborted': None}
    try:
        log = find_log(run_dir)
        started = time.monotonic_ns()
        if log is None:
            result['aborted'] = 'the game wrote no log'
        else:
            bridge = Bridge(sock, timeout=15, connect_timeout=300)
            drv = Driver(bridge, live, log, out, run_id=run['id'], deadline_ns=started + (wall - 40) * 10**9, log=say)
            drv.run(run['ops'])
            result['aborted'] = drv.aborted
            bridge.close()
    except BridgeError as e:
        result['aborted'] = f'bridge: {e}'
    finally:
        try:
            proc.wait(timeout=75)
        except subprocess.TimeoutExpired:
            say(f"run {run['id']}: the wrapper did not end in 75 s; ending its group by pid {proc.pid}")
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        stdout.close()
        for name in ('present.csv', 'frames.csv', 'summary.txt', 'exit.txt', 'env-used.txt', 'kernel.txt', 'kernel-status.json', 'args.txt'):
            if (run_dir / name).exists():
                shutil.copy2(run_dir / name, out / name)
        logs = sorted(run_dir.glob('*_log.txt'))
        if logs:
            shutil.copy2(logs[0], out / 'game_log.txt')
        shutil.rmtree(ctl, ignore_errors=True)
    meta = {'id': run['id'], 'tag': tag, 'copy': a.copy, 'save': run['save'], 'env': env, 'area': run['area'], 'tod': run['tod'], 'wall_s': wall,
            'plan_est_s': run['est_s'], 'label': 'ordinary run, no speed claim', **result}
    (out / 'run.json').write_text(json.dumps(meta, indent=1) + '\n')
    return meta


def cmd_hold(a):
    plan = read_json(a.plan)
    holds = {h['id']: h for h in plan['holds']}
    if a.hold not in holds:
        raise SystemExit(f'no hold {a.hold!r} in the plan (has {sorted(holds)})')
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    metas = []
    for run in holds[a.hold]['runs']:
        left = (a.hold_seconds - (time.monotonic() - started)) if a.hold_seconds else None
        if left is not None and left < run['est_s'] + 40:
            say(f"run {run['id']} skipped: {left:.0f} s of the hold are left, it needs about {run['est_s']:.0f} and 40 s of reserve")
            metas.append({'id': run['id'], 'skipped': 'the hold is nearly used up'})
            continue
        try:
            metas.append(run_one(a, run, out, None if left is None else left - 30))
        except Exception as e:   # (a run's fault must not take the hold's other runs with it)
            say(f"run {run['id']} failed: {type(e).__name__}: {e}")
            metas.append({'id': run['id'], 'aborted': f'{type(e).__name__}: {e}'})
    (out / 'hold.json').write_text(json.dumps({'hold': a.hold, 'runs': metas, 'seconds': round(time.monotonic() - started, 1)}, indent=1) + '\n')
    return 0


def cmd_table(a):
    runs = [costtable.Run(d) for d in a.rundirs]
    rows = costtable.build_rows(runs)
    fams = {}
    if a.library:
        lib = model.load_library(read_json(a.library))
        for c in list(model.builtin_conditions().values()) + list(lib.conditions.values()):
            fams[c.id] = c.family or c.kind
    for d in a.rundirs:   # (a plan's families, when the folder's parent has one)
        pj = Path(d).parent / 'plan.json'
        if pj.exists():
            fams.update(read_json(pj).get('families', {}))
    fams.setdefault('baseline-again', 'baseline')
    md = costtable.markdown(rows, fams)
    if a.md:
        Path(a.md).write_text(md)
    if a.json:
        Path(a.json).write_text(costtable.to_json(rows))
    print(md)
    return 0


def cmd_coverage(a):
    field = read_json(a.field)
    areas = read_json(a.areas)
    cells = coverage.load_cells(read_json(a.cells)) if a.cells else {}
    ranked = coverage.rank_areas(areas, field, min_sessions=a.min_sessions)
    print(coverage.matrix_markdown(ranked, cells))
    if a.next:
        print('\nnext cells:')
        for key, case, why in coverage.next_cells(ranked, cells, limit=a.next):
            print(f'  {key} / {case}: {why}')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('check')
    p.add_argument('library')
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser('plan')
    p.add_argument('library')
    p.add_argument('--area')
    p.add_argument('--tod')
    p.add_argument('--viewpoints', default='')
    p.add_argument('--walks', default='')
    p.add_argument('--conditions', default='baseline')
    p.add_argument('--run-budget', type=float, default=420.0)
    p.add_argument('--hold-limit', type=float, default=480.0)
    p.add_argument('--env', action='append')
    p.add_argument('--dwell', type=float, help="dwell seconds for every viewpoint (over the library's)")
    p.add_argument('--settle', type=float)
    p.add_argument('--min-frames', type=int, help='at least this many presents in each window (1300 for pass timing)')
    p.add_argument('--prefix', default='r', help="the runs' ids start with this (so that plans can be merged)")
    p.add_argument('--out', required=True)
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser('merge')
    p.add_argument('plans', nargs='+')
    p.add_argument('--hold-limit', type=float, default=480.0)
    p.add_argument('--out', required=True)
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser('hold')
    p.add_argument('plan')
    p.add_argument('--hold', required=True)
    p.add_argument('--copy', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--hs-run', default=str(HARNESS / 'hs-run.sh'))
    p.add_argument('--saves', default=str(HARNESS / 'saves'))
    p.add_argument('--wall', type=int, default=0, help="each run's wall seconds (default: its estimate + 90)")
    p.add_argument('--hold-seconds', type=float, default=0, help="the hold's length: a run that no longer fits is skipped")
    p.set_defaults(fn=cmd_hold)
    p = sub.add_parser('table')
    p.add_argument('rundirs', nargs='+')
    p.add_argument('--library')
    p.add_argument('--md')
    p.add_argument('--json')
    p.set_defaults(fn=cmd_table)
    p = sub.add_parser('coverage')
    p.add_argument('--field', required=True)
    p.add_argument('--areas', required=True)
    p.add_argument('--cells')
    p.add_argument('--min-sessions', type=float, default=0)
    p.add_argument('--next', type=int, default=0)
    p.set_defaults(fn=cmd_coverage)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
