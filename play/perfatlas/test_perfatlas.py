#!/usr/bin/env python3
"""The perf atlas against made-up data and a fake game: log and trace readers, the library and its conditions, the planner, the driver
(boot, standing windows, area reloads, options, a closed-loop walk, a lost bridge), the cost table, the coverage matrix and a
rehearsal of `atlas.py hold` with a stand-in for the run wrapper. No game, no GPU. Every number below is invented.

    python3 -I -B test_perfatlas.py
"""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from perfatlas import atlas, costtable, coverage, cpu as cpumod, driver, model, parse, plan as planner, scene, timing  # noqa: E402
from perfatlas.bridge import BridgeError  # noqa: E402

PRE = '[hos host] '
SCENE_LINE = (PRE + 'vk: 240 presents, 61.5 fps; a frame: 812 draws (14 skipped), 21 dispatches, 6 passes, 120 barriers, 7 submits, 0.4 uploads (1.5 MB), '
              '5 ub updates (2 overlays), GPU-bound wait 0.3 ms (recording slots only); 1234 pipelines (987 ms), 3 programs (0 failed), 456 textures (789 MB)')
GPU_LINE = PRE + 'vk: GPU time 5.5 ms a frame (the longest submission 9.0 ms); 0.7 textures made a frame'
REPLAY_LINE = (PRE + "vk: a frame's replay time (ms): submit 4.50, present 0.20, draw 2.20, descriptors 1.10, texture lookups 0.30, uploads 0.10, "
               'pipelines 0.00, programs 0.05, commands 1.00, dispatch 0.25, memory sync 0.60, queue submit 0.10, slot wait 0.02')
LEFT_LINE = PRE + 'vk: a frame: 0.5 draws left out (not ready), 0.10 waits for shaders or pipelines (0.3 ms); translation 12 ms in all, 0 jobs queued'
PASS_BLOCK = [
    PRE + 'vk: GPU pass timing, 600 presents, per frame (each timestamp waits for the GPU: spans add up to more than an unprofiled frame; read the shares):',
    PRE + 'vk:   6.50 ms a frame in all (recordings\' command buffers + upload command buffers; the present\'s blit is not timed)',
    PRE + "vk:   recordings' command buffers: 6.00 ms from the first timestamp to the last in 7.0 a frame (5.50 ms in spans, 0.50 ms between them: glue)",
    PRE + 'vk:   upload command buffers: 0.50 ms in 1.0 a frame (0.40 ms in spans, 0.10 ms between them: texture uploads)',
    PRE + 'vk:   by kind (cb: m = recordings, u = upload ones):',
    PRE + 'vk:     m prepass     2.000 ms  30.8%      3.0 spans     300.0 items     0.10 MB    666.7 us a span',
    PRE + 'vk:     m shading     3.500 ms  53.8%      3.0 spans     600.0 items     0.20 MB   1166.7 us a span',
    PRE + 'vk:   the 25 spans that took the most (ms a frame, spans a frame, us a span, items a frame, MB a frame):',
    PRE + 'vk:   m   1.500 ms     1.0 x   1500.0 us      200.0 items   0.10 MB  shading main',
    PRE + 'vk:   the 20 programs whose runs of draws took the most (RX_VK_PASS_TIMING=2: a timestamp at each change of program):',
    PRE + 'vk:   0.800 ms    10.0 runs     80.0 us a run     120.0 draws    6.67 us a draw  prog 00ff',
]


def wrap_deg(d):
    return (d + 180.0) % 360.0 - 180.0


def lines_with_offsets(texts):
    out, off = [], 0
    for t in texts:
        out.append((off, t))
        off += len(t.encode()) + 1
    return out


class Parse(unittest.TestCase):
    def test_scene_line(self):
        r = parse.parse_line(SCENE_LINE)
        self.assertEqual(r['kind'], 'scene')
        self.assertEqual((r['presents'], r['draws'], r['skipped'], r['dispatches'], r['passes'], r['barriers'], r['submits']), (240, 812, 14, 21, 6, 120, 7))
        self.assertEqual((r['upload_mb'], r['ub_updates'], r['pipelines'], r['textures'], r['texture_mb']), (1.5, 5, 1234, 456, 789))

    def test_other_lines(self):
        self.assertEqual(parse.parse_line(GPU_LINE)['gpu_ms'], 5.5)
        r = parse.parse_line(REPLAY_LINE)
        self.assertEqual((r['submit'], r['texture_lookups'], r['memory_sync'], r['slot_wait']), (4.5, 0.3, 0.6, 0.02))
        r = parse.parse_line(LEFT_LINE)
        self.assertEqual((r['draws_left_out'], r['pipeline_wait_ms']), (0.5, 0.3))
        r = parse.parse_line(PRE + 'actors: AreaA: 3 families, 20 placed, 10 never offered, 2 tied, 4 left out (load 2)')
        self.assertEqual((r['kind'], r['area'], r['left_out'], r['tied']), ('actors', 'AreaA', 4, 2))
        self.assertIsNone(parse.parse_line(PRE + 'something else entirely'))

    def test_pass_block_and_the_line_after_it(self):
        recs = parse.parse_log(lines_with_offsets(PASS_BLOCK + [SCENE_LINE]))
        kinds = [r['kind'] for r in recs]
        self.assertEqual(kinds, ['pass_timing', 'scene'])
        b = recs[0]
        self.assertEqual((b['presents'], b['total_ms'], b['rec_ms'], b['up_ms']), (600, 6.5, 6.0, 0.5))
        self.assertEqual([(k['name'], k['ms']) for k in b['kinds']], [('prepass', 2.0), ('shading', 3.5)])
        self.assertEqual((b['spans'][0]['name'], b['spans'][0]['ms']), ('shading main', 1.5))
        self.assertEqual((b['programs'][0]['name'], b['programs'][0]['draws']), ('prog 00ff', 120.0))

    def test_offsets_and_half_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'log.txt'
            p.write_bytes(b'one\ntwo\nthree-half')
            lines = parse.read_lines(p)
            self.assertEqual(lines, [(0, 'one'), (4, 'two')])

    def test_trace_present_and_stats(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / 'frames.csv'
            t.write_text('frame,ms,game_ms,at_ns\n1,10.0,6.0,1000\n2,20.0,12.0,2000\n3,x,1,3000\n4,30.0\n')
            rows = parse.read_trace(t)
            self.assertEqual(len(rows), 2)
            self.assertEqual(parse.in_window(rows, 1500, 2500), [rows[1]])
            p = Path(d) / 'present.csv'
            p.write_text('frame,return_ns,interval_ns,present_begin_ns,renderer_begin_ns,renderer_end_ns,vcore_back_ns\n1,100,0,1,1,1,1\n2,200,16000000,1,1,1,1\n')
            self.assertEqual(parse.read_present(p), [{'frame': 2.0, 'at_ns': 200.0, 'ms': 16.0}])
        s = parse.describe([10, 20, 30, 40, 100])
        self.assertEqual((s['n'], s['mean'], s['median'], s['max'], s['over25']), (5, 40.0, 30, 100, 3))
        self.assertAlmostEqual(s['low1_fps'], 10.0)
        self.assertIsNone(parse.describe([])['mean'])


def library_obj():
    return {'schema_version': 1,
            'areas': {'a': {'map': 'AreaA', 'age': 'child', 'saves': {'day': 'save-day', 'night': 'save-night'}}},
            'viewpoints': [
                {'id': 'v1', 'area': 'a', 'pos': [10, 5, 20], 'facing': 90, 'camera': {'yaw': 45, 'pitch': 10}, 'dwell_s': 6, 'settle_s': 1, 'tags': ['crowd']},
                {'id': 'v2', 'area': 'a', 'pos': [-3, 5, 8], 'dwell_s': 5, 'settle_s': 1},
                {'id': 'v3', 'area': 'a', 'pos': [1, 2, 3], 'camera': {'yaw': 30}, 'dwell_s': 5, 'settle_s': 1}],
            'walks': [{'id': 'w1', 'area': 'a', 'points': [[10, 0], [12, 4]], 'start': [9, 5, 0], 'radius': 1.5, 'timeout_s': 20}]}


class Model(unittest.TestCase):
    def test_library_and_pos_lines(self):
        lib = model.load_library(library_obj())
        self.assertEqual(lib.viewpoints['v1'].pos_line(), '!pos 10.000 5.000 20.000')   # (the position only: the angles of the long form did not take effect)
        self.assertEqual(lib.viewpoints['v2'].pos_line(), '!pos -3.000 5.000 8.000')
        self.assertEqual(lib.viewpoints['v1'].aim(), [45.0, 10.0])
        self.assertIsNone(lib.viewpoints['v2'].aim())                                    # (no facing, no camera: the camera stays where the game puts it)
        self.assertEqual(lib.viewpoints['v3'].aim(), [30.0, None])
        self.assertEqual(lib.save_for('a', 'night'), 'save-night')

    def test_library_faults(self):
        def bad(mut, text):
            o = library_obj()
            mut(o)
            with self.assertRaises(model.LibraryError) as c:
                model.load_library(o)
            self.assertIn(text, str(c.exception))
        bad(lambda o: o.update(schema_version=2), 'schema_version')
        bad(lambda o: o['viewpoints'][0].update(pos=[1, 2]), 'pos must be')
        bad(lambda o: o['viewpoints'][0].update(pos=[1, 2, float('nan')]), 'finite')
        bad(lambda o: o['viewpoints'][0].update(camera={'pitch': 120}), 'outside')
        bad(lambda o: o['viewpoints'][0].update(tod='dusk'), 'no save')
        bad(lambda o: o['viewpoints'][1].update(id='v1'), 'used twice')
        bad(lambda o: o['viewpoints'][1].update(id='Bad Id'), 'not an id')
        bad(lambda o: o['areas']['a'].update(age='old'), 'age')
        bad(lambda o: o['areas']['a'].update(map='no spaces allowed'), 'map must be')
        bad(lambda o: o['walks'][0].update(points=[[1]]), 'not [x, z]')
        bad(lambda o: o['viewpoints'][0].update(dwell_s=1), 'outside')

    def test_conditions(self):
        cat = model.builtin_conditions()
        self.assertEqual(cat['actors-people'].rules, (('off', 'Npc_*', False),))
        self.assertEqual(cat['res-50'].options, (('resolution', 4),))
        self.assertEqual(cat['no-cull'].env, (('RX_VK_NOCULL', '1'),))
        for bad in ({'id': 'x', 'kind': 'actors', 'rules': [{'do': 'off', 'pattern': 'bad pattern'}]}, {'id': 'x', 'kind': 'env', 'env': {'lower': '1'}},
                    {'id': 'x', 'kind': 'time', 'hour': 24}, {'id': 'x', 'kind': 'option', 'options': {'Bad': 1}}, {'id': 'x', 'kind': 'weird'}):
            with self.assertRaises(model.LibraryError):
                model.parse_condition(bad)
        lib = model.load_library(library_obj())
        self.assertEqual([c.id for c in model.conditions_for(lib, ['baseline', 'res-33'])], ['baseline', 'res-33'])
        with self.assertRaises(model.LibraryError):
            model.conditions_for(lib, ['nope'])


class Plan(unittest.TestCase):
    def setUp(self):
        self.lib = model.load_library(library_obj())
        self.conds = model.conditions_for(self.lib, ['actors-people', 'actors-animals', 'res-50', 'occlusion-queries'])

    def test_blocks_order_and_reloads(self):
        runs = planner.plan_runs(self.lib, ['v1', 'v2'], self.conds, ['w1'], run_budget_s=2000)
        self.assertEqual(len(runs), 2)   # (the main process, and one for the env condition)
        main = runs[0]
        ops = main['ops']
        self.assertEqual(ops[0], {'op': 'boot', 'map': 'AreaA'})
        self.assertEqual(ops[-1], {'op': 'quit'})
        order = [(o['op'], o.get('cond')) for o in ops if o['op'] in ('pos', 'walk', 'actors')]
        conds = []
        for _, c in order:
            if not conds or conds[-1] != c:
                conds.append(c)
        self.assertEqual(conds, ['baseline', 'actors-people', 'actors-animals', 'res-50', 'baseline-again'])
        # an actors block clears the rules first; leaving actors for another kind clears them with a reload
        kinds = [o['op'] for o in ops]
        self.assertEqual(kinds.count('actors'), 2)
        self.assertEqual(kinds.count('actors_clear'), 1)   # (before 'res-50', which follows an actors block; 'baseline-again' follows the option block)
        self.assertEqual([o['visit'] for o in ops if o['op'] == 'pos' and o['cond'].startswith('baseline')], [1, 1, 2, 2])
        self.assertTrue(all(o['dwell_s'] for o in ops if o['op'] == 'pos'))
        self.assertEqual(sum(1 for o in ops if o['op'] == 'option'), sum(1 for o in ops if o['op'] == 'option_restore'))
        env_run = runs[1]
        self.assertEqual(env_run['env'], [['RX_VK_OCC_QUERIES', '1']])
        self.assertFalse(any(o['op'] in ('actors', 'actors_clear') for o in env_run['ops']))
        self.assertEqual(env_run['ops'][1]['cond'], 'occlusion-queries')

    def test_overrides_and_pass_timing_env(self):
        conds = model.conditions_for(self.lib, ['pass-timing'])
        runs = planner.plan_runs(self.lib, ['v1', 'v2'], conds, [], run_budget_s=2000, over={'dwell_s': 20, 'min_frames': 1300})
        env_run = runs[-1]
        self.assertEqual(env_run['env'], [['RX_VK_PASS_TIMING', '1']])
        pos = [o for o in env_run['ops'] if o['op'] == 'pos']
        self.assertEqual([(o['dwell_s'], o['min_frames']) for o in pos], [(20, 1300)] * 2)
        self.assertGreater(planner.op_seconds(pos[0], planner.DEFAULT_COSTS), 20)

    def test_env_conditions_alone_get_no_baseline_process_unless_named(self):
        only = planner.plan_runs(self.lib, ['v1'], model.conditions_for(self.lib, ['occlusion-queries', 'no-cull']), [], run_budget_s=2000)
        self.assertEqual([r['env'] for r in only], [[['RX_VK_OCC_QUERIES', '1']], [['RX_VK_NOCULL', '1']]])
        named = planner.plan_runs(self.lib, ['v1'], model.conditions_for(self.lib, ['baseline', 'no-cull']), [], run_budget_s=2000)
        self.assertEqual([r['env'] for r in named], [[], [['RX_VK_NOCULL', '1']]])

    def test_a_low_fps_floor_makes_a_frame_count_window_longer(self):
        conds = model.conditions_for(self.lib, ['pass-timing'])
        fast = planner.plan_runs(self.lib, ['v1'], conds, [], run_budget_s=2000, over={'dwell_s': 5, 'min_frames': 1300})[-1]['est_s']
        slow = planner.plan_runs(self.lib, ['v1'], conds, [], run_budget_s=2000, over={'dwell_s': 5, 'min_frames': 1300}, costs={'fps_floor': 25.0})[-1]['est_s']
        self.assertGreater(slow, fast + 20)   # (1300 presents are 22 s at 60 a second and 52 s at 25)

    def test_budget_splits_runs(self):
        runs = planner.plan_runs(self.lib, ['v1', 'v2'], self.conds[:2], [], run_budget_s=200)
        self.assertGreater(len(runs), 1)
        for r in runs:
            self.assertLessEqual(r['est_s'], 200 + 1)
            self.assertEqual(r['ops'][0]['op'], 'boot')
        # every condition appears in some run
        seen = {o['cond'] for r in runs for o in r['ops'] if o['op'] == 'pos'}
        self.assertTrue({'baseline', 'actors-people', 'actors-animals'} <= seen)

    def test_too_big_and_mixed(self):
        with self.assertRaises(planner.PlanError):
            planner.plan_runs(self.lib, ['v1', 'v2'], self.conds[:1], [], run_budget_s=80)
        obj = library_obj()
        obj['viewpoints'][1]['tod'] = 'night'
        lib = model.load_library(obj)
        with self.assertRaises(planner.PlanError):
            planner.plan_runs(lib, ['v1', 'v2'], [], [])

    def test_holds(self):
        runs = planner.plan_runs(self.lib, ['v1', 'v2'], self.conds[:2], [], run_budget_s=200)
        holds = planner.plan_holds(runs, hold_limit_s=480)
        self.assertEqual(sum(len(h['runs']) for h in holds), len(runs))
        for h in holds:
            self.assertLessEqual(h['est_s'], 480.0)
        with self.assertRaises(planner.PlanError):
            planner.plan_holds(runs, hold_limit_s=50)
        self.assertIn('hold h1', planner.summary(holds))


class FakeClock:
    def __init__(self):
        self.t = 10 * 10**9

    def now_ns(self):
        return self.t

    def sleep(self, s):
        self.t += int(s * 1e9)


class FakeBridge:
    """a game that lives on the fake clock: the title until A is pressed, a load after '!actors reload', a position that follows
    '!pos' lines and the stick (the camera-relative steering the driver uses), 60 presents a second"""
    def __init__(self, clock, fail_after=None):
        self.c = clock
        self.t0 = clock.t
        self.state, self.pos, self.facing, self.camera = 'menu', [0, 0, 0], 0.0, 0.0
        self.loading_until = 0
        self.options = {'resolution': 1}
        self.calls = []
        self.fail_after = fail_after
        self.sticks = 0
        self.map = None
        self.played_at = None
        self.cam_rate = 1.5       # (degrees a frame at full push; the sign is the game's, unknown to the driver)
        self.cam_sign = -1.0
        self.draws_by_yaw = lambda yaw: 1000 + int(abs(wrap_deg(yaw)))   # (a made-up scene: more draws looking toward +-180)

    def frame(self):
        return int((self.c.t - self.t0) / 1e9 * 60)

    def live(self, line):
        w = line.split()
        if w[0] == '!pos':
            nums = [float(x) for x in w[1:]]
            self.pos = nums[:3]
            if len(nums) >= 4:
                self.facing = nums[3]
                self.camera = nums[4] if len(nums) == 6 else nums[3]
        elif w[:2] == ['!actors', 'reload']:
            self.loading_until = self.c.t + int(3e9)
            self.pos = [0, 0, 9]

    def _guard(self, name):
        self.calls.append(name)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise BridgeError('the game closed the connection')

    def status(self):
        self._guard('status')
        if self.state == 'play' and self.played_at is None:
            self.played_at = self.c.t
        loading = self.c.t < self.loading_until
        playing = self.state == 'play'
        return {'ok': True, 'frame': self.frame(), 'state': 'loading' if loading else self.state, 'fps': 60.0, 'map': 'AreaA' if playing else None,
                'draws': self.draws_by_yaw(self.camera) if playing else 0.0,
                'game': {'pos': self.pos, 'facing': self.facing, 'camera': self.camera, 'event': False, 'loading': loading, 'age': 'child',
                         'frame': self.frame(), 'map': 'AreaA'} if playing else None}

    def press(self, buttons, frames=8, wait=True):
        self._guard('press')
        self.state = 'play'
        return {'ok': True}

    def stick(self, x, y, frames=8, which='L', wait=False):
        self._guard('stick')
        if which == 'R':
            self.camera = wrap_deg(self.camera + self.cam_sign * x * self.cam_rate * frames)
            return {'ok': True}
        self.sticks += 1
        want = math.atan2(x, y)
        heading = math.radians(self.camera) - want
        d = 4.0 * frames / 60.0 * math.hypot(x, y)
        self.pos = [self.pos[0] + d * math.sin(heading), self.pos[1], self.pos[2] + d * math.cos(heading)]
        return {'ok': True}

    def release(self):
        self._guard('release')
        return {'ok': True}

    def shot(self, path):
        self._guard('shot')
        Path(path).write_bytes(b'png')
        return {'ok': True}

    def option_get(self, name):
        self._guard('option_get')
        return {'ok': True, 'option': {'key': name, 'value': self.options.get(name, 0)}}

    def option_set(self, name, value):
        self._guard('option_set')
        was = self.options.get(name, 0)
        self.options[name] = value
        return {'ok': True, 'was': was, 'option': {'key': name, 'value': value}}

    def quit(self):
        self._guard('quit')
        return {'ok': True}


def make_driver(tmp, bridge, clock, **kw):
    log = Path(tmp) / 'game.log'
    log.write_text('')
    live = Path(tmp) / 'live.txt'
    live.write_text('')

    def on_live(line):
        bridge.live(line)
        if line.startswith('!actors reload'):
            with open(log, 'a') as f:
                f.write('[hos BancModule] actors: AreaA: 3 families, 20 placed, 10 never offered, 2 tied, 4 left out (load 2)\n')
    return driver.Driver(bridge, live, log, Path(tmp) / 'out', clock=clock, on_live=on_live, **kw), live, log


class Driver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = model.load_library(library_obj())
        self.clock = FakeClock()
        self.bridge = FakeBridge(self.clock)

    def test_whole_plan(self):
        drv, live, log = make_driver(self.tmp.name, self.bridge, self.clock)
        conds = model.conditions_for(self.lib, ['actors-people', 'res-50'])
        runs = planner.plan_runs(self.lib, ['v1', 'v2'], conds, ['w1'], run_budget_s=5000)
        res = drv.run(runs[0]['ops'])
        errors = [r for r in res if r['result'].get('error')]
        self.assertEqual(errors, [])
        wins = [json.loads(ln) for ln in (Path(self.tmp.name) / 'out' / 'windows.jsonl').read_text().splitlines()]
        self.assertEqual([w['kind'] for w in wins].count('viewpoint'), 8)   # (4 blocks x 2 viewpoints: baseline, people, res-50, baseline again)
        self.assertEqual([w['kind'] for w in wins].count('walk'), 4)
        w1 = wins[0]
        self.assertTrue(w1['arrived'] and w1['camera_ok'])
        self.assertLessEqual(abs(wrap_deg(w1['status0']['camera'] - 45.0)), 10.0)   # (the camera was turned to the viewpoint's camera yaw, 45)
        self.assertGreater(w1['aimed_pushes'], 0)
        self.assertEqual(w1['status0']['pos'], [10, 5, 20])
        self.assertGreaterEqual((w1['t1_ns'] - w1['t0_ns']) / 1e9, 6.0)
        self.assertTrue(Path(w1['shot']).exists())
        self.assertEqual(w1['moved_m'], 0.0)
        walk = [w for w in wins if w['kind'] == 'walk'][0]
        self.assertEqual((walk['result'], walk['points_reached']), ('reached', 2))
        self.assertTrue(all(w['cpu'] is None for w in wins))   # (no sampler was given)
        # the live file carries what the plan asked for, in order
        lines = live.read_text().splitlines()
        self.assertEqual(lines[0], '!pos 10.000 5.000 20.000')
        self.assertIn('!actors clear AreaA', lines)
        self.assertIn('!actors off AreaA Npc_*', lines)
        self.assertEqual(lines.count('!actors reload'), 2)   # (the people block's, and the clear before res-50)
        # an option was set live and put back to the first-seen value
        self.assertEqual(self.bridge.options['resolution'], 1)
        # the reload recorded the log line that says what was left out
        reload = [r for r in res if r['op']['op'] == 'actors'][0]['result']
        self.assertTrue(reload['ok'])
        self.assertIn('4 left out', reload['actors_line'])
        self.assertEqual(res[-1]['op'], {'op': 'quit'})

    def test_aim_learns_the_stick_whichever_way_it_turns(self):
        for sign in (1.0, -1.0):
            with tempfile.TemporaryDirectory() as tmp:
                clock = FakeClock()
                bridge = FakeBridge(clock)
                bridge.cam_sign = sign
                drv, _, _ = make_driver(tmp, bridge, clock)
                drv.run([{'op': 'boot', 'map': 'AreaA'}])
                for want in (90.0, -120.0, 170.0, 0.0):
                    ok, cam, pitch, pushes = drv.aim(want)
                    self.assertTrue(ok, (sign, want, cam))
                    self.assertLessEqual(abs(wrap_deg(cam - want)), 10.0)
                self.assertEqual(drv.cam_sign, sign)
                drv.close()

    def test_aim_gives_up_when_the_camera_does_not_turn(self):
        self.bridge.cam_rate = 0.0
        drv, _, _ = make_driver(self.tmp.name, self.bridge, self.clock)
        drv.run([{'op': 'boot', 'map': 'AreaA'}])
        ok, cam, pitch, pushes = drv.aim(120.0)
        self.assertFalse(ok)
        self.assertLessEqual(pushes, 8)

    def test_sweep_reads_draws_in_each_view(self):
        drv, _, _ = make_driver(self.tmp.name, self.bridge, self.clock)
        runs = planner.plan_sweep(self.lib, ['v1', 'v2'], [0, 90, 180], run_budget_s=900)
        self.assertEqual(len(runs), 1)
        res = drv.run(runs[0]['ops'])
        sweeps = [r for r in res if r['op']['op'] == 'sweep']
        self.assertEqual(len(sweeps), 2)
        views = sweeps[0]['result']['views']
        self.assertEqual([round(v['yaw']) for v in views], [0, 90, 180])
        self.assertEqual([v['draws'] for v in views], [1000 + int(abs(wrap_deg(v['camera']))) for v in views])   # (made-up scene: 1000 + |camera read|)
        rows = costtable.sweep_rows([costtable.Run(Path(self.tmp.name) / 'out')])
        self.assertEqual(rows[0]['draws'], max(v['draws'] for v in views))
        self.assertIn('Scout', costtable.sweep_markdown(rows))

    def test_boot_presses_a_through_the_title(self):
        drv, _, _ = make_driver(self.tmp.name, self.bridge, self.clock)
        res = drv.run([{'op': 'boot', 'map': 'AreaA'}, {'op': 'quit'}])
        self.assertTrue(res[0]['result']['ok'])
        self.assertIn('press', self.bridge.calls)

    def test_a_lost_bridge_ends_the_run_and_records_the_rest(self):
        bridge = FakeBridge(self.clock, fail_after=20)
        drv, _, _ = make_driver(self.tmp.name, bridge, self.clock)
        ops = [{'op': 'boot', 'map': 'AreaA'}] + planner.pos_ops(self.lib, 'baseline', ['v1', 'v2'], 1) + [{'op': 'quit'}]
        res = drv.run(ops)
        self.assertTrue(any('bridge:' in r['result'].get('error', '') for r in res))
        self.assertIn('skipped', res[-1]['result'])   # (the quit: nothing to quit through a bridge that is gone)
        self.assertTrue(drv.aborted.startswith('bridge:'))

    def test_the_wall_budget_skips_measuring_ops_but_still_quits(self):
        drv, _, _ = make_driver(self.tmp.name, self.bridge, self.clock, deadline_ns=self.clock.t + int(4e9))
        ops = [{'op': 'boot', 'map': 'AreaA'}] + planner.pos_ops(self.lib, 'baseline', ['v1', 'v2'], 1) + [{'op': 'quit'}]
        res = drv.run(ops)
        self.assertEqual(res[-1]['result'], {'ok': True})
        self.assertTrue(any('wall budget' in r['result'].get('skipped', '') for r in res))

    def test_a_walk_that_cannot_move_is_stuck(self):
        self.bridge.stick = lambda *a, **k: {'ok': True}   # nothing moves Link
        drv, _, _ = make_driver(self.tmp.name, self.bridge, self.clock)
        drv.op_boot({'op': 'boot', 'map': 'AreaA'})
        r = drv.op_walk({'op': 'walk', 'cond': 'baseline', 'walk': 'w1', 'visit': 1, 'points': [[50, 50]], 'start': None, 'radius': 1.5, 'timeout_s': 30, 'shot': False})
        self.assertEqual(r['result'], 'stuck')
        drv.close()


class Cpu(unittest.TestCase):
    def fake_proc(self, root, game_ticks_a, game_ticks_b, busy_a, busy_b):
        """a /proc tree with a game of 3 threads and the machine's totals; returns (snapshot reader)"""
        root = Path(root)
        (root / '42' / 'task').mkdir(parents=True)
        (root / '42' / 'environ').write_bytes(b'A=1\0HOS_CONTROL=/tmp/pa-x/c.sock\0B=2\0')
        (root / '43').mkdir()
        (root / '43' / 'environ').write_bytes(b'HOS_CONTROL=/tmp/other.sock\0')
        (root / 'abc').mkdir()
        return root

    def write(self, root, ticks, busy, total=100000):
        root = Path(root)
        names = {'1': 'BaseProcExecutor3', '2': 'Module System (Worker)2', '3': 'Presentation'}
        for tid, name in names.items():
            d = root / '42' / 'task' / tid
            d.mkdir(exist_ok=True)
            # fields after the name: state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime ...
            u, sy = ticks[tid]
            (d / 'stat').write_text(f'{tid} ({name}) S 1 1 1 0 -1 0 0 0 0 0 {u} {sy} 0 0\n')
        (root / 'stat').write_text(f'cpu  {busy} 0 0 {total - busy} 0 0 0 0 0 0\n')

    def test_find_pid_by_control_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fake_proc(tmp, 0, 0, 0, 0)
            self.assertEqual(cpumod.find_game_pid('/tmp/pa-x/c.sock', proc=str(root)), 42)
            self.assertIsNone(cpumod.find_game_pid('/tmp/none.sock', proc=str(root)))

    def test_thread_names_with_spaces_and_groups(self):
        self.assertEqual(cpumod.parse_task_stat('7 (Module System (Worker)2) S 1 1 1 0 -1 0 0 0 0 0 30 12 0 0'), ('Module System (Worker)2', 42))
        self.assertEqual(cpumod.group_name('BaseProcExecutor3'), 'BaseProcExecutor')
        self.assertEqual(cpumod.group_name('Presentation'), 'Presentation')
        self.assertIsNone(cpumod.parse_task_stat('garbage'))

    def test_window_cores_others_and_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fake_proc(tmp, 0, 0, 0, 0)
            t = [0]
            sampler = cpumod.Sampler(42, proc=str(root), clock=lambda: t[0])
            hz = cpumod.HZ
            # 10 s: the game's three threads use 200 + 100 + 100 ticks a second (4 cores); the machine is busy 6 cores: 2 others
            snaps = []
            for sec in range(0, 11, 1):
                t[0] = sec * 10**9
                self.write(root, {'1': (200 * sec * hz // 100, 0), '2': (100 * sec * hz // 100, 0), '3': (50 * sec * hz // 100, 50 * sec * hz // 100)}, 6 * hz * sec)
                s = sampler.snapshot()
                if 0 < sec < 10:
                    s['game_ticks'] = sum(x[1] for x in s['threads'].values())
                    s['threads'] = {}
                snaps.append(s)
            c = cpumod.window_cpu(snaps)
            self.assertAlmostEqual(c['game_cores'], 4.0)
            self.assertAlmostEqual(c['machine_cores'], 6.0)
            self.assertAlmostEqual(c['others_mean'], 2.0)
            self.assertAlmostEqual(c['others_peak'], 2.0)
            self.assertTrue(c['quiet'])
            self.assertEqual(c['groups'], {'BaseProcExecutor': 2000, 'Module System (Worker)': 1000, 'Presentation': 1000})
            # the same game on a machine busy with 12 cores: 8 others, not quiet
            for sec, s in zip(range(11), snaps):
                s['sys_busy'] = 12 * hz * sec
            c = cpumod.window_cpu(snaps)
            self.assertAlmostEqual(c['others_mean'], 8.0)
            self.assertFalse(c['quiet'])
            self.assertIsNone(cpumod.window_cpu(snaps[:1]))

    def test_a_peak_stretch_breaks_the_quiet_rule_though_the_mean_is_low(self):
        hz = cpumod.HZ
        snaps = []
        busy = 0
        for sec in range(0, 21):
            busy += (10 if 5 <= sec <= 10 else 1) * hz    # 1 other core, 10 for six seconds in the middle
            snaps.append({'t_ns': sec * 10**9, 'sys_busy': busy, 'sys_total': 1, 'threads': {}, 'game_ticks': 0})
        c = cpumod.window_cpu(snaps)
        self.assertLess(c['others_mean'], cpumod.QUIET_MEAN * 2)
        self.assertGreater(c['others_peak'], cpumod.QUIET_PEAK)
        self.assertFalse(c['quiet'])


class CostTable(unittest.TestCase):
    def make_run(self, root, name='r1'):
        d = Path(root) / name
        (d / 'trace').mkdir(parents=True)
        log_lines = []
        wins = []
        rows = ['frame,ms,game_ms,render_ms,gpu_wait_ms,pace_ms,draw_ms,at_ns']
        t = 1_000_000_000
        frame = 0
        log_off = 0

        def log(text):
            nonlocal log_off
            log_lines.append(text)
            log_off += len(text.encode()) + 1

        plan = [('baseline', 1, 10.0), ('actors-people', 1, 8.0), ('baseline-again', 2, 10.4)]
        for n, (cond, visit, ms) in enumerate(plan, 1):
            log0 = log_off
            t0 = t
            for _ in range(200):
                frame += 1
                t += int(ms * 1e6)
                rows.append(f'{frame},{ms},{ms * 0.5},{ms * 0.3},{ms * 0.1},{ms * 0.1},{ms * 0.2},{t}')
            log(SCENE_LINE.replace('812 draws', f'{800 if cond != "actors-people" else 700} draws'))
            log(GPU_LINE)
            log(REPLAY_LINE)
            if cond == 'baseline':
                for ln in PASS_BLOCK:
                    log(ln)
                for ln in PASS_BLOCK:
                    log(ln.replace('6.50 ms', '6.60 ms'))
            wins.append({'run': name, 'n': n, 'kind': 'viewpoint', 'cond': cond, 'vp': 'v1', 'visit': visit, 'tod': 'day', 't0_ns': t0, 't1_ns': t + 1,
                         'log0': log0, 'log1': log_off, 'frame0': 0, 'frame1': 0, 'status0': {}, 'status1': {}, 'arrived': True, 'camera_ok': True,
                         'shot': None, 'moved_m': 0.0, 'event_seen': False})
            t += 500_000_000
        (d / 'game_log.txt').write_text('\n'.join(log_lines) + '\n')
        (d / 'trace' / 'frames.csv').write_text('\n'.join(rows) + '\n')
        (d / 'windows.jsonl').write_text('\n'.join(json.dumps(w) for w in wins) + '\n')
        (d / 'run.json').write_text(json.dumps({'id': name}))
        return d

    def test_rows_deltas_and_markdown(self):
        with tempfile.TemporaryDirectory() as root:
            d = self.make_run(root)
            run = costtable.Run(d)
            rows = costtable.build_rows([run])
            self.assertEqual(len(rows), 3)
            base, people, again = rows
            self.assertAlmostEqual(base['frame']['mean'], 10.0)
            self.assertEqual(base['frame']['n'], 200)
            self.assertEqual(base['log']['draws'], 800)
            self.assertEqual(people['log']['draws'], 700)
            self.assertEqual(base['bound'], 'game-cpu')
            self.assertAlmostEqual(base['parts']['game_ms'], 5.0)
            # the pass block: two blocks in the baseline window, the second one is the clean one
            self.assertEqual(base['pass']['blocks'], 1)
            self.assertFalse(base['pass']['partial'])
            self.assertAlmostEqual(base['pass']['total_ms'], 6.6)
            self.assertEqual(base['pass']['kinds'][0]['name'], 'shading')
            self.assertIsNone(people['pass'])
            costtable.add_deltas(rows)
            self.assertIsNone(base['delta'])
            self.assertAlmostEqual(people['delta']['frame_ms'], -2.0)
            self.assertEqual(people['delta']['draws'], -100)
            self.assertFalse(people['delta']['cross_run'])
            prices = costtable.family_prices(rows, {'actors-people': 'people', 'baseline': 'baseline'})
            self.assertAlmostEqual(prices['people']['frame_ms'], -2.0)
            self.assertAlmostEqual(costtable.drift(rows)[('r1', 'v1')], 10.4 / 10.0 - 1)
            md = costtable.markdown(rows, {'actors-people': 'people', 'baseline-again': 'baseline'})
            self.assertIn('ordinary run, no speed claim', md)
            self.assertIn('What each part took away', md)
            self.assertIn('Revisit drift', md)
            self.assertIn('| r1 | v1 | 4.0% | yes |', md)
            data = json.loads(costtable.to_json(rows))
            self.assertEqual(len(data), 3)

    def test_cpu_row_per_frame_and_groups(self):
        hz = cpumod.HZ
        snaps = [{'t_ns': 0, 'sys_busy': 0, 'sys_total': 1, 'threads': {'1': ['BaseProcExecutor0', 0], '2': ['BaseProcExecutor1', 0], '3': ['Presentation', 0]}},
                 {'t_ns': 5 * 10**9, 'sys_busy': 4 * hz * 5, 'sys_total': 1, 'game_ticks': 3 * hz * 5, 'threads': {}},
                 {'t_ns': 10 * 10**9, 'sys_busy': 4 * hz * 10, 'sys_total': 1,
                  'threads': {'1': ['BaseProcExecutor0', hz * 10], '2': ['BaseProcExecutor1', hz * 10], '3': ['Presentation', hz * 10]}}]
        row = costtable.cpu_row(snaps, 300)   # 300 frames in 10 s
        self.assertAlmostEqual(row['game_cores'], 3.0)
        self.assertAlmostEqual(row['cpu_ms_frame'], 3 * 10 * 1000 / 300)
        self.assertEqual(row['groups'][0][0], 'BaseProcExecutor')
        self.assertAlmostEqual(row['groups'][0][1], 2 * 10 * 1000 / 300)
        self.assertAlmostEqual(row['others_mean'], 1.0)
        self.assertTrue(row['quiet'])
        self.assertIsNone(costtable.cpu_row(None, 300))
        self.assertIsNone(costtable.cpu_row(snaps[:1], 300))

    def test_missing_files_give_none_not_zero(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root) / 'r'
            d.mkdir()
            (d / 'windows.jsonl').write_text(json.dumps({'run': 'r', 'n': 1, 'kind': 'viewpoint', 'cond': 'baseline', 'vp': 'v', 'visit': 1, 't0_ns': 1, 't1_ns': 2,
                                                         'log0': 0, 'log1': 0, 'arrived': False}) + '\n')
            rows = costtable.build_rows([costtable.Run(d)])
            self.assertIsNone(rows[0]['frame']['mean'])
            self.assertIsNone(rows[0]['log']['draws'])
            self.assertIn('no frame file in the window', rows[0]['flags'])
            self.assertIn('did not arrive', rows[0]['flags'])


class Coverage(unittest.TestCase):
    FIELD = {'rows': [{'group': 'MapA (child)', 'sessions': 20, 'hours': 2.0, 'fps': 30.0, 'low1': 10.0, 'hitches_per_hour': 900},
                      {'group': 'MapB (adult)', 'sessions': 4, 'hours': 1.0, 'fps': 60.0, 'low1': 40.0, 'hitches_per_hour': 100},
                      {'group': 'Title and intro', 'sessions': 90, 'hours': 5.0, 'fps': 30.0, 'low1': 5.0, 'hitches_per_hour': 100}]}
    AREAS = {'a': {'map': 'MapA', 'label': 'Area A'}, 'b': {'map': 'MapB', 'label': 'Area B', 'heavy': True}, 'c': {'map': 'MapC', 'label': 'Area C', 'heavy': True},
             'd': {'map': 'MapD', 'label': 'Area D'}}

    def test_rank_and_matrix(self):
        ranked = coverage.rank_areas(self.AREAS, self.FIELD)
        self.assertEqual([r['key'] for r in ranked], ['a', 'b', 'c', 'd'])   # (lost hours first, then a known heavy area without a row)
        self.assertAlmostEqual(ranked[0]['lost_hours'], 1.0)
        self.assertEqual(ranked[1]['lost_hours'], 0.0)
        self.assertIsNone(ranked[2]['sessions'])
        low = coverage.rank_areas(self.AREAS, self.FIELD, min_sessions=10)
        self.assertIsNone(low[1]['hours'] if low[1]['key'] == 'b' else 1)   # (4 sessions: an anecdote, its figures are withheld)
        cells = coverage.load_cells({'cells': {'a|stand': {'state': 'observed'}, 'a|walk': {'state': 'planned'}}})
        md = coverage.matrix_markdown(ranked, cells, ['stand', 'walk'])
        self.assertIn('| Area A | 20 | 2.00 | 30.0 | 10.0 | 900 | observed | planned |', md)
        self.assertIn('Area C * | - | - | - | - | - | - | - |', md)
        nxt = coverage.next_cells(ranked, cells, ['stand', 'walk'], limit=3)
        self.assertEqual([(k, c) for k, c, _ in nxt], [('b', 'stand'), ('c', 'stand'), ('d', 'stand')])
        with self.assertRaises(ValueError):
            coverage.load_cells({'cells': {'a|stand': {'state': 'done'}}})


class SceneAndTiming(unittest.TestCase):
    SPEC = {'map': 'AreaA', 'aim_yaw': 120.0, 'aim_tol': 10.0, 'pos': [10.0, 20.0], 'pos_tol_m': 1.0, 'control_by_s': 50, 'ready_by_s': 53, 'sample_every_s': 10.0}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.bridge = FakeBridge(self.clock)
        self.bridge.pos = [10.0, 5.0, 20.0]
        self.bridge.state = 'menu'

    def drive(self, spec=None, until_s=100):
        spec = dict(spec or self.SPEC)
        first = self.clock.t / 1e9 - 20.0   # (the game's first present was 20 s ago on the fake clock)
        clock = lambda: self.clock.t / 1e9
        return scene.drive(self.bridge, self.tmp.name, spec, first, until_s, clock=clock, sleep=self.clock.sleep)

    def test_a_good_scene_validates(self):
        self.bridge.press('A')   # (the boot script's presses: the fake game is in play after one)
        rec = self.drive()
        self.assertIsNotNone(rec['controllable_at_s'])
        self.assertTrue(rec['aim']['ok'])
        self.assertLessEqual(rec['ready_at_s'], 53)
        self.assertGreaterEqual(len(rec['samples']), 3)
        self.assertEqual(scene.validate(rec), [])
        self.assertEqual(scene.validate(json.loads((Path(self.tmp.name) / 'actions.json').read_text())), [])
        self.assertEqual(scene.main(['validate', self.tmp.name]), 0)

    def test_validator_names_each_way_a_scene_goes_wrong(self):
        self.bridge.press('A')
        rec = self.drive()
        for mutate, text in ((lambda r: r['samples'][1].update(camera=-60.0), 'the camera read'),
                             (lambda r: r['samples'][1].update(pos=[40.0, 5.0, 20.0]), 'Link read'),
                             (lambda r: r['samples'][0].update(event=True), 'event=True'),
                             (lambda r: r['samples'][0].update(map='Other'), 'map Other'),
                             (lambda r: r.update(ready_at_s=60.0), 'ready at 60.0'),
                             (lambda r: r['aim'].update(ok=False), 'did not reach'),
                             (lambda r: r.update(samples=[]), 'no status sample')):
            bad = json.loads(json.dumps(rec))
            mutate(bad)
            problems = scene.validate(bad)
            self.assertTrue(any(text in p for p in problems), (text, problems))
        self.assertEqual(scene.validate({'spec': self.SPEC, 'controllable_at_s': None, 'errors': ['x']}), ['x', 'never controllable'])

    def test_a_game_that_never_gets_controllable_is_recorded(self):
        # the title is never passed (no press): the fake stays in the menu
        rec = self.drive({**self.SPEC, 'control_by_s': 8})
        self.assertIsNone(rec['controllable_at_s'])
        self.assertTrue(rec['errors'])
        self.assertTrue(scene.validate(rec))

    def test_a_bridge_loss_is_an_error_not_a_crash(self):
        self.bridge.press('A')
        self.bridge.fail_after = 5
        rec = self.drive()
        self.assertTrue(any('bridge:' in e for e in rec['errors']))
        self.assertTrue(scene.validate(rec))

    def test_spec_needs_its_fields(self):
        with self.assertRaises(ValueError):
            scene.spec_of({'atlas_scene': {'map': 'A'}})
        self.assertEqual(scene.spec_of({'atlas_scene': {k: 1 for k in scene.REQUIRED}})['aim_tol'], 10.0)

    def test_first_present_reads_the_trace(self):
        p = Path(self.tmp.name) / 'present.csv'
        self.assertIsNone(scene.first_present_s(p))
        p.write_text('frame,return_ns\n1,5000000000,0\n')
        self.assertEqual(scene.first_present_s(p), 5.0)

    # ---- the worker's inputs
    def scenario(self, **kw):
        base = dict(label='atlas-test', save='/saves/x', scene=self.SPEC, good_max=30.0, bad_min=36.0, from_s=60, to_s=105)
        base.update(kw)
        return timing.make_scenario(**base)

    def test_scenario_demands_bands_and_a_window_that_fits(self):
        sc = self.scenario()
        self.assertEqual(sc['schema_version'], 1)
        self.assertEqual(sc['measure'], {'name': 'mean_ms', 'direction': 'higher-worse', 'good_max': 30.0, 'bad_min': 36.0})
        self.assertEqual((sc['run']['anchor'], sc['run']['from'], sc['run']['to']), ('first', 60, 105))
        self.assertEqual(sc['run']['env']['HOS_DYNRES'], '0')
        self.assertEqual(sc['run']['atlas_scene']['aim_yaw'], 120.0)
        for kw, text in (({'good_max': None}, 'bands'), ({'bad_min': 20.0}, 'below'), ({'to_s': 120}, 'window'), ({'from_s': 54}, 'aim must be done')):
            with self.assertRaises(timing.TimingError) as c:
                self.scenario(**kw)
            self.assertIn(text, str(c.exception))

    def test_plan_fits_the_cap_and_orders_the_arms(self):
        cmd = timing.run_command('python3', '/x/scene.py', '/h/regress', '/c')
        self.assertEqual(cmd[:4], ['python3', '-I', '-u', '/x/scene.py'])
        self.assertIn('{scenario}', cmd)
        outs = [str(Path(self.tmp.name) / f'o{i}') for i in range(4)]
        plan = timing.null_pair_plan('lab', '/copy', 'abc', outs, '/s.json', cmd)
        self.assertEqual([e['role'] for e in plan['entries']], ['reference', 'candidate', 'reference', 'candidate'])
        self.assertEqual({(e['copy'], e['commit']) for e in plan['entries']}, {('/copy', 'abc')})   # (a null pair: one build)
        self.assertEqual(4 * (plan['seconds'] + plan['grace_seconds']) + 10, 470)
        self.assertEqual(plan['avoid'], [timing.AVOID_WIDGET])
        with self.assertRaises(timing.TimingError):
            timing.null_pair_plan('lab', '/copy', 'abc', outs, '/s.json', cmd, seconds=120)   # (4 x 125 + 10 = 510 s: over the cap)
        with self.assertRaises(timing.TimingError):
            timing.make_plan([('reference', 'l', 'c', 'k', outs[0]), ('reference', 'l', 'c', 'k', outs[1])] * 2, '/s.json', cmd)
        Path(outs[1]).mkdir()
        with self.assertRaises(timing.TimingError):
            timing.null_pair_plan('lab', '/copy', 'abc', outs, '/s.json', cmd)   # (an output folder exists)

    def test_null_pair_verdicts(self):
        def runs(vals, valid=True, disturbed=False):
            return [{'role': r, 'result': {'valid': valid, 'disturbed': disturbed, 'value': v, 'others_cores_window': 0.5, 'others_cores_peak': 1.0}}
                    for r, v in zip(['reference', 'candidate'] * 2, vals)]
        v = timing.null_pair_verdict(runs([30.0, 30.3, 30.2, 29.9]))
        self.assertEqual(v['verdict'], 'usable')
        self.assertAlmostEqual(v['gap'], abs((30.3 + 29.9) / 2 - (30.0 + 30.2) / 2) / 30.1)
        v = timing.null_pair_verdict(runs([30.0, 30.3, 32.0, 29.9]))   # (the reference's repeats are 6.7% apart)
        self.assertEqual(v['verdict'], 'noisy')
        self.assertTrue(any('reference spread' in r for r in v['reasons']))
        v = timing.null_pair_verdict(runs([30.0, 31.5, 30.1, 31.6]))   # (spreads small, the arms 5% apart: the same build)
        self.assertEqual(v['verdict'], 'noisy')
        self.assertTrue(any('arm gap' in r for r in v['reasons']))
        self.assertEqual(timing.null_pair_verdict(runs([30.0, 30.3, 30.2, 29.9], disturbed=True))['verdict'], 'invalid')
        self.assertEqual(timing.null_pair_verdict(runs([30.0, 30.3, 30.2, 29.9], valid=False))['verdict'], 'invalid')
        self.assertEqual(timing.null_pair_verdict(runs([30.0, 30.3])[:2])['verdict'], 'invalid')
        self.assertEqual(len(timing.null_pair_verdict(runs([30.0, 30.3, 30.2, 29.9]))['rows']), 4)   # (every row is kept)
        self.assertIsNone(timing.spread([1.0]))


class Rehearsal(unittest.TestCase):
    """atlas.py plan / hold / table end to end with the stand-in wrapper (real sockets, real files, real time: a few seconds)"""
    def test_plan_hold_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            lib = tmp / 'lib.json'
            obj = library_obj()
            obj['viewpoints'] = obj['viewpoints'][:2]
            for v in obj['viewpoints']:
                v.update(dwell_s=3, settle_s=0)
            obj['walks'] = []
            lib.write_text(json.dumps(obj))
            planf = tmp / 'plan.json'
            self.assertEqual(atlas.main(['check', str(lib)]), 0)
            self.assertEqual(atlas.main(['plan', str(lib), '--area', 'a', '--tod', 'day', '--viewpoints', 'v1,v2', '--conditions', 'baseline,actors-people',
                                         '--run-budget', '300', '--hold-limit', '480', '--out', str(planf)]), 0)
            plan = json.loads(planf.read_text())
            self.assertEqual(plan['families']['actors-people'], 'people')
            copy = tmp / 'copy'
            (copy / 'runs').mkdir(parents=True)
            saves = tmp / 'saves'
            (saves / 'save-day').mkdir(parents=True)
            out = tmp / 'out'
            rc = atlas.main(['hold', str(planf), '--hold', 'h1', '--copy', str(copy), '--out', str(out), '--hs-run', str(HERE / 'rehearsal' / 'fake_hs_run.sh'),
                             '--saves', str(saves), '--wall', '120'])
            self.assertEqual(rc, 0)
            hold = json.loads((out / 'hold.json').read_text())
            run = hold['runs'][0]
            self.assertIsNone(run['aborted'], run)
            rd = out / run['id']
            for name in ('windows.jsonl', 'ops.jsonl', 'game_log.txt', 'run.json', 'launch.txt', 'trace/frames.csv', 'summary.txt'):
                self.assertTrue((rd / name).exists(), name)
            launch = (rd / 'launch.txt').read_text()
            self.assertIn('RX_VK_FRAME_TRACE=1', launch)
            self.assertIn('HOS_DYNRES=0', launch)
            runs = [costtable.Run(rd)]
            rows = costtable.build_rows(runs)
            self.assertEqual([(r['cond'], r['vp']) for r in rows], [('baseline', 'v1'), ('baseline', 'v2'), ('actors-people', 'v1'), ('actors-people', 'v2'),
                                                                   ('baseline-again', 'v1'), ('baseline-again', 'v2')])
            for r in rows:
                self.assertGreater(r['frame']['n'], 100, r)
                self.assertAlmostEqual(r['parts']['game_ms'] / r['frame']['mean'], 0.6, delta=0.01)
                self.assertEqual(r['log']['draws'], 900)
            self.assertTrue(all(r['flags'] == [] for r in rows), [r['flags'] for r in rows])
            for r in rows:   # (the CPU record: the fake game is a real process found by its HOS_CONTROL; it burns a little CPU)
                self.assertIsNotNone(r['cpu'], r['vp'])
                self.assertGreaterEqual(r['cpu']['game_cores'], 0.0)
                self.assertIsNotNone(r['cpu']['others_mean'])
            self.assertIsNotNone(run['game_pid'])
            self.assertEqual(atlas.main(['table', str(rd), '--md', str(tmp / 'table.md')]), 0)
            md = (tmp / 'table.md').read_text()
            self.assertIn('Cost table', md)
            self.assertIn('the quiet record', md)
            # a scout: each place looked at in two yaws, the draws read from the bridge's status
            plan2 = tmp / 'plan2.json'
            self.assertEqual(atlas.main(['plan', str(lib), '--sweep', '--area', 'a', '--tod', 'day', '--viewpoints', 'v1,v2', '--yaws', '0,90', '--dwell', '3',
                                         '--run-budget', '300', '--prefix', 's', '--out', str(plan2)]), 0)
            out2 = tmp / 'out2'
            rc = atlas.main(['hold', str(plan2), '--hold', 'h1', '--copy', str(copy), '--out', str(out2), '--hs-run', str(HERE / 'rehearsal' / 'fake_hs_run.sh'),
                             '--saves', str(saves), '--wall', '120'])
            self.assertEqual(rc, 0)
            rd2 = out2 / 's1'
            sw = costtable.sweep_rows([costtable.Run(rd2)])
            self.assertEqual(len(sw), 4)
            self.assertTrue(all(r['draws'] == 900.0 and r['arrived'] and r['camera_ok'] for r in sw), sw)
            self.assertEqual(atlas.main(['table', str(rd2), '--md', str(tmp / 'sweep.md'), '--json', str(tmp / 'sweep.json')]), 0)
            self.assertIn('Scout', (tmp / 'sweep.md').read_text())
            # the plan's own socket folder is gone after the run
            self.assertEqual(list(Path('/tmp').glob('pa-*/c.sock')), [])


if __name__ == '__main__':
    unittest.main(verbosity=1)
