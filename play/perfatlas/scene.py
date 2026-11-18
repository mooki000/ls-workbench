#!/usr/bin/env python3
"""The atlas's scene for the paired timing worker: one viewpoint, the game started from a save that puts Link there, the camera turned to
the viewpoint's heading with the right stick, then nothing while the worker's window is measured.

It is the pattern of a scene driver around the shared adapter (the adapter measures; this only sets the scene and records what it did):

    scene.py run  --copy C --output O --scenario S.json --seconds N --commit SHA     (the `run_command` of a `_hold` plan; needs --regress-dir)
    scene.py validate OUTPUT                                                          (the scenario's `validator_command`)

The scenario's `run` object carries `atlas_scene` = {"map", "aim_yaw", "aim_tol", "pos": [x, z], "pos_tol_m", "control_by_s", "ready_by_s"}:
the map Link must stand in, the camera heading, where he must read, and the seconds after the first present by which the game must be
controllable and the aim finished (both before the window's `from`). `validate` fails the run (the worker keeps it as invalid) unless
the game was controllable in time, Link read where the save puts him, the camera held its heading through the window, no event ran and
nothing loaded. A viewpoint is a save made for the place (`--start` of the save maker); no `!pos` is needed, so the same scene runs on a
plain main build and on a build with the change.

The thread that drives the bridge is injected (bridge, clock, sleep), so the tests run it against a fake game.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from perfatlas.bridge import Bridge, BridgeError  # noqa: E402
from perfatlas.camera import Aimer, wrap  # noqa: E402

REQUIRED = ('map', 'aim_yaw', 'pos', 'control_by_s', 'ready_by_s')


def spec_of(settings):
    spec = dict(settings.get('atlas_scene') or {})
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise ValueError(f'scenario run.atlas_scene lacks {missing}')
    spec.setdefault('aim_tol', 10.0)
    spec.setdefault('pos_tol_m', 1.0)
    spec.setdefault('sample_every_s', 10.0)
    return spec


def first_present_s(present_csv):
    """the host-clock seconds of the first row of present.csv (the game's first present), or None"""
    try:
        with open(present_csv) as f:
            for line in f:
                p = line.split(',')
                if len(p) >= 3 and p[0].isdigit() and p[1].isdigit():
                    return int(p[1]) / 1e9
    except OSError:
        pass
    return None


def snap(st):
    g = (st.get('game') if isinstance(st, dict) else None) or {}
    return {'state': st.get('state'), 'map': st.get('map'), 'pos': g.get('pos'), 'camera': g.get('camera'), 'pitch': g.get('camera_pitch'),
            'event': g.get('event'), 'loading': g.get('loading'), 'frame': st.get('frame')}


def controllable(st, want_map):
    g = (st.get('game') if isinstance(st, dict) else None) or {}
    return bool(st.get('ok')) and st.get('state') == 'play' and st.get('map') == want_map and g.get('event') is False and g.get('loading') is False


def drive(bridge, out, spec, first, until_s, clock=time.monotonic, sleep=time.sleep, stop=None):
    """the scene: wait for control, aim, then sample the status every `sample_every_s` until `until_s` seconds after the first present.
    Writes OUT/actions.json (the validator reads it) and returns the record. `first` is the first present on `clock`'s timeline."""
    stop = stop or threading.Event()
    rec = {'spec': spec, 'controllable_at_s': None, 'aim': None, 'ready_at_s': None, 'samples': [], 'errors': []}

    def rel():
        return clock() - first

    def save():
        Path(out, 'actions.json').write_text(json.dumps(rec, indent=1) + '\n')

    try:
        while rel() < spec['control_by_s'] and not stop.is_set():
            st = bridge.status()
            flat = {'ok': st.get('ok'), 'state': st.get('state'), 'map': st.get('map') or (st.get('game') or {}).get('map'), 'game': st.get('game') or {},
                    'frame': st.get('frame')}
            if controllable(flat, spec['map']):
                rec['controllable_at_s'] = round(rel(), 2)
                rec['before_aim'] = snap(flat)
                break
            sleep(1.0)
        if rec['controllable_at_s'] is None:
            rec['errors'].append(f"not controllable in {spec['map']} by {spec['control_by_s']} s after the first present")
            save()
            return rec
        aimer = Aimer(bridge, sleep=sleep)
        ok, cam, pitch, pushes = aimer.aim(spec['aim_yaw'], spec['aim_tol'])
        bridge.release()
        rec['aim'] = {'ok': ok, 'camera': cam, 'pitch': pitch, 'pushes': pushes, 'sign': aimer.sign, 'rate': aimer.rate}
        rec['ready_at_s'] = round(rel(), 2)
        save()
        while rel() < until_s and not stop.is_set():
            sleep(spec['sample_every_s'])
            st = bridge.status()
            flat = {'ok': st.get('ok'), 'state': st.get('state'), 'map': st.get('map') or (st.get('game') or {}).get('map'), 'game': st.get('game') or {},
                    'frame': st.get('frame')}
            rec['samples'].append({'at_s': round(rel(), 2), **snap(flat)})
            save()
    except BridgeError as e:
        rec['errors'].append(f'bridge: {e}')
    finally:
        save()
    return rec


def validate(actions):
    """list of problems with a run's actions record (empty = the scene was as asked)"""
    problems = []
    spec = actions.get('spec') or {}
    for e in actions.get('errors') or []:
        problems.append(e)
    if actions.get('controllable_at_s') is None:
        problems.append('never controllable')
        return problems
    if actions['controllable_at_s'] > spec.get('control_by_s', 1e9):
        problems.append(f"controllable at {actions['controllable_at_s']} s, after {spec['control_by_s']} s")
    if actions.get('ready_at_s') is None or actions['ready_at_s'] > spec.get('ready_by_s', 1e9):
        problems.append(f"ready at {actions.get('ready_at_s')} s, after {spec.get('ready_by_s')} s (the window starts at its `from`)")
    aim = actions.get('aim') or {}
    if not aim.get('ok'):
        problems.append(f"the camera did not reach {spec.get('aim_yaw')} (read {aim.get('camera')})")
    pts = [actions.get('before_aim') or {}] + list(actions.get('samples') or [])
    for p in pts:
        where = p.get('pos')
        if where:
            d = math.hypot(where[0] - spec['pos'][0], where[2] - spec['pos'][1])
            if d > spec.get('pos_tol_m', 1.0):
                problems.append(f"Link read {d:.2f} m from the place at {p.get('at_s', 'before the aim')}")
    for p in actions.get('samples') or []:
        if p.get('camera') is None or abs(wrap(p['camera'] - spec['aim_yaw'])) > spec.get('aim_tol', 10.0):
            problems.append(f"the camera read {p.get('camera')} at {p.get('at_s')} s (aim {spec['aim_yaw']})")
        if p.get('event') is not False or p.get('loading') is not False:
            problems.append(f"event={p.get('event')} loading={p.get('loading')} at {p.get('at_s')} s")
        if p.get('map') != spec['map']:
            problems.append(f"map {p.get('map')} at {p.get('at_s')} s")
    if not (actions.get('samples') or []):
        problems.append('no status sample during the window')
    return problems


def cmd_validate(a):
    problems = validate(json.loads((Path(a.output) / 'actions.json').read_text()))
    for p in problems:
        print('INVALID:', p)
    print('scene ok' if not problems else f'{len(problems)} problem(s)')
    return 1 if problems else 0


def cmd_run(a):
    sys.path.insert(0, str(Path(a.regress_dir)))
    import run_case   # noqa: E402  (the shared adapter: it measures; this wrapper only sets the scene)
    scenario = json.loads(Path(a.scenario).read_text())
    settings = scenario['run']
    spec = spec_of(settings)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    ctl = Path(a.control_dir)
    ctl.mkdir(mode=0o700, exist_ok=True)
    assert ctl.stat().st_uid == os.geteuid() and not (ctl.stat().st_mode & 0o077), 'the control folder must be private'
    sock = ctl / (out.parent.name + '-' + out.name.split('-')[0] + '.sock')
    assert not sock.exists(), 'the control socket name is already in use'
    settings.setdefault('env', {})['HOS_CONTROL'] = str(sock)
    stop, box = threading.Event(), {}

    def thread():
        first = None
        while not stop.wait(0.1):
            sec = first_present_s(out / 'present.csv')
            if sec is not None:
                first = sec
                break
        if first is None:
            return
        try:
            bridge = Bridge(str(sock), timeout=8, connect_timeout=60)
        except BridgeError as e:
            box['record'] = {'spec': spec, 'controllable_at_s': None, 'errors': [str(e)], 'samples': []}
            (out / 'actions.json').write_text(json.dumps(box['record'], indent=1) + '\n')
            return
        try:
            box['record'] = drive(bridge, out, spec, first, settings.get('to', 105) - 3, stop=stop)
        finally:
            bridge.close()

    t = threading.Thread(target=thread, daemon=True)
    t.start()
    try:
        run_case.run_case(a.copy, a.output, scenario, a.seconds, a.commit)
    finally:
        stop.set()
        t.join(timeout=5)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('run')
    for k in ('copy', 'output', 'scenario', 'commit'):
        p.add_argument('--' + k, required=True)
    p.add_argument('--seconds', type=float, required=True)
    p.add_argument('--regress-dir', required=True, help="the folder with the shared adapter's run_case.py")
    p.add_argument('--control-dir', required=True, help='a private (0700) folder for the control socket, outside every run folder')
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser('validate')
    p.add_argument('output')
    p.set_defaults(fn=cmd_validate)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
