#!/usr/bin/env python3
"""Executes a run's ops (plan.py) against a live game through the control bridge and the live-input file, and writes what the
analysis needs: `windows.jsonl` (one record for each measured stretch: viewpoint or walk, condition, the host-clock start and end
in nanoseconds, the log's byte offsets, the game's status before and after, a picture) and `ops.jsonl` (every op with its result).

Windows are stamped with `time.monotonic_ns()` (the clock of the frame trace's `at_ns` and of present.csv on Linux), and with the
log's size in bytes (lines have no timestamps): `parse.py` cuts the game's files with them. The driver measures nothing itself.

It never repeats an input it is unsure of: a bridge fault ends the run (the ops after it are recorded as `skipped`), a refused or
late step is recorded and the next op goes on. Everything that touches the world is injected (bridge, clock, sleep, the live-file
writer, the log path) so the tests run it against a fake game in no time.
"""
import json
import math
import re
import time
from pathlib import Path

from .bridge import BridgeError
from .camera import Aimer


class Clock:
    def now_ns(self):
        return time.monotonic_ns()

    def sleep(self, s):
        time.sleep(s)


def wrap(d):
    return (d + 180.0) % 360.0 - 180.0


class RunAborted(RuntimeError):
    pass


class Driver:
    def __init__(self, bridge, live_path, log_path, out_dir, run_id='r1', clock=None, deadline_ns=None, poll_s=0.5, on_live=None,
                 boot_timeout_s=240.0, log=None, cpu=None):
        self.bridge = bridge
        self.live_path = Path(live_path)
        self.log_path = Path(log_path)
        self.out = Path(out_dir)
        self.run_id = run_id
        self.clock = clock or Clock()
        self.deadline_ns = deadline_ns
        self.poll_s = poll_s
        self.on_live = on_live
        self.boot_timeout_s = boot_timeout_s
        self.say = log or (lambda text: None)
        self.options_before = {}
        self.n = 0
        self.aborted = None
        self.cpu = cpu                      # (cpu.Sampler: the game's and the machine's CPU, for the quiet record and CPU ms a frame)
        self.aimer = Aimer(bridge, sleep=self.sleep, status=self.status)   # (the right stick's handedness and rate, learned by the first push)
        (self.out / 'shots').mkdir(parents=True, exist_ok=True)
        self.windows_f = open(self.out / 'windows.jsonl', 'a', buffering=1)
        self.ops_f = open(self.out / 'ops.jsonl', 'a', buffering=1)

    # ---------------------------------------------------------------- small helpers
    def now(self):
        return self.clock.now_ns()

    def sleep(self, s):
        self.clock.sleep(s)

    def log_size(self):
        try:
            return self.log_path.stat().st_size
        except OSError:
            return 0

    def send_live(self, line):
        """append one complete line to the live-input file (the game polls it every 30 ms; each line's times count from when it is read)"""
        with open(self.live_path, 'a') as f:
            f.write(line.rstrip('\n') + '\n')
        if self.on_live:
            self.on_live(line)

    def status(self):
        """the bridge's status reply, flattened: {'ok', 'frame', 'state', 'map', 'game': {...}} (game is {} when the snapshot is null)"""
        r = None
        for attempt in range(3):   # (a read is safe to ask again; a loaded PC answers late. A closed connection is not retried.)
            try:
                r = self.bridge.status()
                break
            except BridgeError as e:
                if 'no reply in time' not in str(e) or attempt == 2:
                    raise
        if not r.get('ok'):
            return {'ok': False, 'frame': r.get('frame'), 'state': None, 'map': None, 'game': {}}
        g = r.get('game') or {}
        return {'ok': True, 'frame': r.get('frame'), 'state': r.get('state'), 'map': r.get('map') or g.get('map'), 'game': g, 'fps': r.get('fps'),
                'draws': r.get('draws')}

    def controllable(self, st, expect_map=None):
        g = st.get('game') or {}
        return (st.get('ok') and st.get('state') == 'play' and bool(st.get('map')) and g.get('event') is False and g.get('loading') is False
                and (expect_map is None or st.get('map') == expect_map))

    def wait_controllable(self, expect_map, timeout_s, press_a=False, stable=3):
        """poll until the game is in play in expect_map, no event, no load, `stable` polls running; press A through a title when asked"""
        end = self.now() + int(timeout_s * 1e9)
        good, last_press, st = 0, 0, None
        while self.now() < end:
            st = self.status()
            if self.controllable(st, expect_map):
                good += 1
                if good >= stable:
                    return True, st
            else:
                good = 0
                if press_a and not st.get('map') and self.now() - last_press > 2.5e9:
                    self.bridge.press('A', 12, wait=False)
                    last_press = self.now()
            self.sleep(self.poll_s)
        return False, st

    def snapshot(self, st):
        g = st.get('game') or {}
        return {'frame': st.get('frame'), 'state': st.get('state'), 'map': st.get('map'), 'pos': g.get('pos'), 'facing': g.get('facing'),
                'camera': g.get('camera'), 'camera_pitch': g.get('camera_pitch'), 'event': g.get('event'), 'loading': g.get('loading'),
                'age': g.get('age'), 'fps': st.get('fps'), 'draws': st.get('draws')}

    # ---------------------------------------------------------------- ops
    def run(self, ops):
        results = []
        for op in ops:
            if self.aborted and (op['op'] != 'quit' or self.aborted.startswith('bridge:')):
                results.append(self.record_op(op, self.now(), self.now(), {'skipped': self.aborted}))
                continue
            if op['op'] != 'quit' and self.deadline_ns is not None and self.now() > self.deadline_ns:
                self.aborted = 'the wall budget is used up'
                results.append(self.record_op(op, self.now(), self.now(), {'skipped': self.aborted}))
                continue
            t0 = self.now()
            try:
                res = getattr(self, 'op_' + op['op'])(op)
            except BridgeError as e:
                self.aborted = f'bridge: {e}'
                res = {'error': self.aborted}
            except Exception as e:   # (a bug in one op must not take the run's other windows with it)
                res = {'error': f'{type(e).__name__}: {e}'}
            results.append(self.record_op(op, t0, self.now(), res))
        self.close()
        return results

    def close(self):
        self.windows_f.close()
        self.ops_f.close()

    def record_op(self, op, t0, t1, res):
        rec = {'run': self.run_id, 'op': op, 't0_ns': t0, 't1_ns': t1, 'result': res}
        self.ops_f.write(json.dumps(rec, separators=(',', ':')) + '\n')
        return rec

    def op_boot(self, op):
        ok, st = self.wait_controllable(op.get('map'), self.boot_timeout_s, press_a=True)
        return {'ok': ok, 'status': self.snapshot(st) if st else None}

    def op_time(self, op):
        self.send_live(f"!cheat {100 + int(op['hour'])}")
        self.sleep(1.0)
        return {'ok': True, 'hour': op['hour']}

    def op_option(self, op):
        name = op['name']
        if name not in self.options_before:
            got = self.bridge.option_get(name)
            if got.get('ok'):
                self.options_before[name] = got.get('option', {}).get('value')
        r = self.bridge.option_set(name, op['value'])
        self.sleep(self.poll_s)
        return {'ok': bool(r.get('ok')), 'was': r.get('was'), 'now': (r.get('option') or {}).get('value'), 'error': None if r.get('ok') else r.get('error')}

    def op_option_restore(self, op):
        name = op['name']
        if name not in self.options_before or self.options_before[name] is None:
            return {'ok': False, 'error': 'no value was recorded before'}
        r = self.bridge.option_set(name, self.options_before[name])
        self.sleep(self.poll_s)
        return {'ok': bool(r.get('ok')), 'now': (r.get('option') or {}).get('value')}

    def reload_area(self, area_map, timeout_s=60.0):
        """`!actors reload` until a load starts (it is refused while an event plays or within seconds of the last load), then until
        the game is controllable in the map again; then the log's per-load actor line, which says what was left out"""
        log0 = self.log_size()
        start = self.now()
        attempts = 0
        loaded = False
        while attempts < 4 and self.now() - start < timeout_s * 1e9:
            attempts += 1
            self.send_live('!actors reload')
            seen = self.now()
            while self.now() - seen < 9e9:
                st = self.status()
                g = st.get('game') or {}
                if st.get('state') == 'loading' or g.get('loading') is True:
                    loaded = True
                    break
                self.sleep(0.25)
            if loaded:
                break
        if not loaded:
            return {'ok': False, 'attempts': attempts, 'error': 'no load started (refused? an event, a load or a map that is not a teleport destination)'}
        ok, st = self.wait_controllable(area_map, timeout_s, stable=3)
        line = self.wait_log(r'actors: ' + area_map + r': \d+ families', log0, 10.0)
        return {'ok': ok, 'attempts': attempts, 'load_s': round((self.now() - start) / 1e9, 1), 'actors_line': line, 'status': self.snapshot(st) if st else None}

    def wait_log(self, pattern, offset, timeout_s):
        rx = re.compile(pattern)
        end = self.now() + int(timeout_s * 1e9)
        while True:
            try:
                data = self.log_path.read_bytes()[offset:]
            except OSError:
                data = b''
            for ln in data.decode('utf-8', 'replace').splitlines():
                if rx.search(ln):
                    return ln.strip()
            if self.now() >= end:
                return None
            self.sleep(0.5)

    def op_actors_clear(self, op):
        self.send_live('!actors clear ' + op['area'])
        self.sleep(0.3)
        return self.reload_area(op['area']) if op.get('reload') else {'ok': True}

    def op_actors(self, op):
        area = op['area']
        if op.get('clear_first'):
            self.send_live('!actors clear ' + area)
            self.sleep(0.3)
        for do, pattern, force in op['rules']:
            self.send_live(f"!actors {do} {area} {pattern}" + (' force' if force else ''))
            self.sleep(0.1)
        self.send_live('!actors list ' + area)
        self.sleep(0.3)
        res = self.reload_area(area) if op.get('reload') else {'ok': True}
        res['rules'] = op['rules']
        return res

    def arrive(self, target, timeout_s=8.0, tol_m=0.6):
        """wait until Link reads within tol_m of (x, z); returns (ok, last status)"""
        end = self.now() + int(timeout_s * 1e9)
        st = None
        pos_ok = False
        while self.now() < end:
            st = self.status()
            p = (st.get('game') or {}).get('pos')
            if p:
                pos_ok = math.hypot(p[0] - target[0], p[2] - target[2]) <= tol_m
                if pos_ok:
                    break
            self.sleep(0.25)
        return pos_ok, st

    @property
    def cam_sign(self):
        return self.aimer.sign

    def aim(self, yaw, tol=10.0, tries=8):
        """turn the camera to `yaw` degrees with the right stick (camera.Aimer); returns (camera_ok, camera yaw, pitch, pushes)"""
        return self.aimer.aim(yaw, tol, tries)

    def shot(self, name):
        path = (self.out / 'shots' / name).resolve()
        r = self.bridge.shot(path)
        return str(path) if r.get('ok') else None

    def dwell(self, dwell_s, min_frames):
        """stand for dwell_s wall seconds and at least min_frames presents (at most 4 x dwell_s); sample the status each poll"""
        t0 = self.now()
        cpu = self.cpu_start()
        st0 = self.status()
        f0 = st0.get('frame') or 0
        samples = [self.snapshot(st0)]
        hard = t0 + int(max(dwell_s * 4, dwell_s + 10) * 1e9)
        while True:
            self.sleep(self.poll_s)
            st = self.status()
            samples.append(self.snapshot(st))
            self.cpu_mid(cpu)
            elapsed = (self.now() - t0) / 1e9
            frames = (st.get('frame') or f0) - f0
            if (elapsed >= dwell_s and frames >= min_frames) or self.now() >= hard:
                self.cpu_end(cpu)
                return t0, self.now(), samples, st0, st, cpu

    # ---- CPU: snapshots with the threads at the window's two ends, the machine's and the game's totals between
    def cpu_start(self):
        if self.cpu is None:
            return None
        try:
            return [self.cpu.snapshot()]
        except OSError:
            return None

    def cpu_mid(self, snaps):
        if snaps is None:
            return
        try:
            s = self.cpu.snapshot()
        except OSError:
            return
        s['game_ticks'] = sum(t[1] for t in s['threads'].values())
        s['threads'] = {}   # (kept at the two ends only)
        snaps.append(s)

    def cpu_end(self, snaps):
        if snaps is None:
            return
        try:
            snaps.append(self.cpu.snapshot())
        except OSError:
            pass

    def write_window(self, **rec):
        self.n += 1
        rec = {'run': self.run_id, 'n': self.n, **rec}
        self.windows_f.write(json.dumps(rec, separators=(',', ':')) + '\n')
        return rec

    def go_to(self, line, attempts=2):
        """send the `!pos` line and wait for Link to read there; one more ask when it did not take (the update that takes a `!pos` can
        pass a pending cheat by)"""
        nums = [float(x) for x in line.split()[1:]]
        target = (nums[0], nums[1], nums[2])
        self.send_live(line)
        ok, st = self.arrive(target)
        for _ in range(attempts - 1):
            if ok:
                break
            self.send_live(line)
            ok, st = self.arrive(target)
        return ok, st

    def op_pos(self, op):
        pos_ok, st = self.go_to(op['line'])
        aim = op.get('aim')
        cam_ok, cam, pitch, pushes = (True, None, None, 0)
        if aim is not None:
            cam_ok, cam, pitch, pushes = self.aim(aim[0])
        self.sleep(op.get('settle_s', 3.0))
        log0 = self.log_size()
        name = f"{self.run_id}-{self.n + 1:03d}-{op['cond']}-{op['vp']}-v{op['visit']}.png" if op.get('shot') else None
        t0, t1, samples, st0, st1, cpu = self.dwell(op['dwell_s'], op.get('min_frames', 0))
        log1 = self.log_size()
        shot = self.shot(name) if name else None   # (after the window: the picture's own frame stays out of the numbers)
        rec = self.write_window(kind='viewpoint', cond=op['cond'], vp=op['vp'], visit=op['visit'], tod=op.get('tod'), t0_ns=t0, t1_ns=t1,
                                log0=log0, log1=log1, frame0=st0.get('frame'), frame1=st1.get('frame'),
                                status0=self.snapshot(st0), status1=self.snapshot(st1), arrived=pos_ok, camera_ok=cam_ok, aim=aim, aimed_pushes=pushes,
                                shot=shot, moved_m=self.moved(samples), event_seen=any(s.get('event') for s in samples), samples=len(samples), cpu=cpu)
        return {'ok': pos_ok, 'window': rec['n'], 'camera_ok': cam_ok, 'camera': cam}

    def op_sweep(self, op):
        """stand at one place and look in each of `yaws` in turn, reading the bridge's draws a frame while standing (no picture, no scene
        lines: a scout for where the heavy views are; draws are counts, they do not move with the machine's load)"""
        pos_ok, st = self.go_to(op['line'])
        self.sleep(op.get('settle_s', 1.0))
        out = []
        for yaw in op['yaws']:
            cam_ok, cam, pitch, pushes = self.aim(yaw)
            self.sleep(op.get('look_s', 1.0))
            t0, t1, samples, st0, st1, cpu = self.dwell(op.get('dwell_s', 3.0), 0)
            draws = sorted(x['draws'] for x in samples if x.get('draws') is not None)
            fps = sorted(x['fps'] for x in samples if x.get('fps') is not None)
            rec = self.write_window(kind='sweep', cond='sweep', vp=op['vp'], visit=1, tod=op.get('tod'), t0_ns=t0, t1_ns=t1, log0=0, log1=0,
                                    frame0=st0.get('frame'), frame1=st1.get('frame'), status0=self.snapshot(st0), status1=self.snapshot(st1),
                                    arrived=pos_ok, camera_ok=cam_ok, aim=[yaw, None], camera=cam, pitch=pitch, aimed_pushes=pushes,
                                    draws_median=draws[len(draws) // 2] if draws else None, draws_max=draws[-1] if draws else None,
                                    draws_min=draws[0] if draws else None, fps_median=fps[len(fps) // 2] if fps else None, samples=len(samples), cpu=cpu)
            out.append({'yaw': yaw, 'camera': cam, 'draws': rec['draws_median']})
        return {'ok': pos_ok, 'views': out}

    @staticmethod
    def moved(samples):
        pts = [s['pos'] for s in samples if s.get('pos')]
        if len(pts) < 2:
            return None
        return round(max(math.hypot(p[0] - pts[0][0], p[2] - pts[0][2]) for p in pts), 2)

    def op_walk(self, op):
        """walk the points in order, steering from the game's own samples (the stick's angle is the camera's yaw minus the bearing,
        right positive; its handedness is checked as Link moves, as hsap does): the window is the whole walk"""
        if op.get('start'):
            self.go_to('!pos ' + ' '.join(f'{v:.3f}' for v in op['start']))
            self.sleep(2.0)
        log0 = self.log_size()
        name = f"{self.run_id}-{self.n + 1:03d}-{op['cond']}-{op['walk']}-v{op['visit']}.png" if op.get('shot') else None
        cpu = self.cpu_start()
        st0 = self.status()
        t0 = self.now()
        end = t0 + int(op['timeout_s'] * 1e9)
        sign, hist, mirror, last_check = 1.0, [], 0, self.now()
        reached, track, result = 0, [], 'timeout'
        pi = 0
        pts = op['points']
        sent_sample = None
        while self.now() < end and pi < len(pts):
            st = self.status()
            g = st.get('game') or {}
            pos, cam = g.get('pos'), g.get('camera')
            if not pos or cam is None or g.get('event') is not False or g.get('loading') is not False:
                self.sleep(0.1)
                continue
            x, z = pts[pi]
            d = math.hypot(x - pos[0], z - pos[2])
            track.append([round((self.now() - t0) / 1e9, 2), round(pos[0], 2), round(pos[2], 2)])
            if d <= op['radius']:
                reached += 1
                pi += 1
                continue
            now = self.now()
            hist.append((now, pos[0], pos[2]))
            hist = [h for h in hist if now - h[0] <= 2.5e9]
            if now - t0 > 3e9 and len(hist) > 4 and now - hist[0][0] >= 2.4e9 and math.hypot(pos[0] - hist[0][1], pos[2] - hist[0][2]) < 0.3:
                result = 'stuck'
                break
            bearing = math.degrees(math.atan2(x - pos[0], z - pos[2]))
            # the handedness check: moving more than 100 degrees off the wanted heading three checks running
            old = [h for h in hist if now - h[0] >= 0.6e9]
            if old and now - last_check > 0.5e9:
                last_check = now
                mx, mz = pos[0] - old[-1][1], pos[2] - old[-1][2]
                if math.hypot(mx, mz) > 0.5:
                    err = abs(wrap(math.degrees(math.atan2(mx, mz)) - bearing))
                    mirror = mirror + 1 if err > 100 else 0
                    if mirror >= 3:
                        sign, mirror, hist = -sign, 0, []
            want = wrap(cam - bearing) * sign
            if len(track) % 8 == 0:
                self.cpu_mid(cpu)
            if g.get('frame') != sent_sample:   # (one push for each fresh sample)
                mag = 1.0 if d > 1.2 else 0.5
                self.bridge.stick(mag * math.sin(math.radians(want)), mag * math.cos(math.radians(want)), frames=8)
                sent_sample = g.get('frame')
            self.sleep(0.1)
        if pi >= len(pts):
            result = 'reached'
        self.bridge.release()
        t1 = self.now()
        self.cpu_end(cpu)
        st1 = self.status()
        log1 = self.log_size()
        shot = self.shot(name) if name else None   # (the end of the walk, after the window)
        rec = self.write_window(kind='walk', cond=op['cond'], vp=op['walk'], visit=op['visit'], tod=op.get('tod'), t0_ns=t0, t1_ns=t1,
                                log0=log0, log1=log1, frame0=st0.get('frame'), frame1=st1.get('frame'),
                                status0=self.snapshot(st0), status1=self.snapshot(st1), arrived=result == 'reached', camera_ok=True, shot=shot,
                                result=result, points_reached=reached, points=len(pts), track=track[::5], sign=sign, cpu=cpu)
        return {'ok': result == 'reached', 'result': result, 'window': rec['n']}

    def op_quit(self, op):
        self.bridge.release()
        r = self.bridge.quit()
        return {'ok': bool(r.get('ok'))}
