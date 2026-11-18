#!/usr/bin/env python3
"""A stand-in for `hs-run.sh` and the game, for rehearsing `atlas.py hold` without a game or a GPU. It takes the wrapper's arguments
(<copy> <tag> <wall> --user <save> NAME=VALUE ...), makes <copy>/runs/<tag>/ with a log, serves the control bridge's JSON lines on the
socket named by HOS_CONTROL, follows the live-input file (`!pos`, `!actors reload`, `!cheat`), writes the frame trace to
HOS_PROFILE_DIR/frames.csv and periodic scene lines to the log, and ends on `quit` or at its wall. Made-up numbers; the formats are the
renderer's. Real time, short: a rehearsal takes seconds."""
import json
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path

args = sys.argv[1:]
copy, tag, wall = args[0], args[1], float(args[2])
env = dict(a.split('=', 1) for a in args[3:] if '=' in a and not a.startswith('--'))
run_dir = Path(copy) / 'runs' / tag
run_dir.mkdir(parents=True, exist_ok=True)
log = open(run_dir / 'fake_log.txt', 'a', buffering=1)
start = time.monotonic()
lock = threading.Lock()
S = {'map': 'AreaA', 'state': 'menu', 'pos': [0, 0, 0], 'facing': 0.0, 'camera': 0.0, 'pitch': 0.0, 'loading': False, 'options': {'resolution': 1},
     'pending': None, 'quit': False}
trace = None
if env.get('HOS_PROFILE_DIR'):
    trace = open(Path(env['HOS_PROFILE_DIR']) / 'frames.csv', 'w', buffering=1)
    trace.write('frame,ms,game_ms,render_ms,gpu_wait_ms,pace_ms,at_ns\n')


def say(text):
    log.write('[hos host] ' + text + '\n')


def frame():
    return int((time.monotonic() - start) * 60)


def ground():
    """the game: starts at the title (state menu), is in play after 2 s unless A was pressed earlier"""
    t = time.monotonic() - start
    if S['state'] == 'menu' and S.get('a') and t > 1.0:
        S['state'] = 'play'


def frames_thread():
    n, last = 0, time.monotonic()
    while not S['quit']:
        time.sleep(1 / 60)
        now = time.monotonic()
        n += 1
        if trace:
            ms = (now - last) * 1000
            trace.write(f'{n},{ms:.2f},{ms * 0.6:.2f},{ms * 0.3:.2f},{ms * 0.05:.2f},{ms * 0.05:.2f},{time.monotonic_ns()}\n')
        last = now
        if n % 120 == 0:
            say(f'vk: {n} presents, 60.0 fps; a frame: 900 draws (10 skipped), 20 dispatches, 5 passes, 100 barriers, 6 submits, 0.5 uploads (1.5 MB), 3 ub updates (1 overlays), GPU-bound wait 0.0 ms (recording slots only); 100 pipelines (50 ms), 0 programs (0 failed), 400 textures (900 MB)')
            say("vk: a frame's replay time (ms): submit 4.00, present 0.20, draw 2.00, descriptors 1.00, texture lookups 0.10, uploads 0.10, pipelines 0.00, programs 0.00, commands 1.00, dispatch 0.30, memory sync 0.50, queue submit 0.10, slot wait 0.05")
            say('vk: GPU time 5.0 ms a frame (the longest submission 6.0 ms); 0.5 textures made a frame')
        if env.get('RX_VK_PASS_TIMING') and n % 300 == 0:
            say('vk: GPU pass timing, 600 presents, per frame (each timestamp waits for the GPU):')
            say('vk:   6.00 ms a frame in all (recordings and uploads)')
            say("vk:   recordings' command buffers: 5.50 ms from the first timestamp to the last in 6.0 a frame (5.00 ms in spans, 0.50 ms between them: glue)")
            say('vk:   upload command buffers: 0.50 ms in 1.0 a frame (0.40 ms in spans, 0.10 ms between them: uploads)')
            say('vk:   by kind (cb: m = recordings, u = upload ones):')
            say('vk:     m prepass    2.000 ms  33.3%      3.0 spans     300.0 items     0.10 MB    666.7 us a span')
            say('vk:     m shading    3.000 ms  50.0%      3.0 spans     600.0 items     0.20 MB   1000.0 us a span')
            say('vk:   the 25 spans that took the most (ms a frame, spans a frame, us a span, items a frame, MB a frame):')
            say('vk:   m   1.500 ms     1.0 x   1500.0 us      200.0 items   0.10 MB  shading main')


def live_thread():
    path = Path(env['HOS_INPUT_LIVE'])
    off = 0
    while not S['quit']:
        time.sleep(0.03)
        try:
            data = path.read_bytes()[off:]
        except OSError:
            continue
        cut = data.rfind(b'\n')
        if cut < 0:
            continue
        off += cut + 1
        for line in data[:cut].decode().splitlines():
            w = line.split()
            if not w:
                continue
            if w[0] == '!pos':
                nums = [float(x) for x in w[1:]]
                time.sleep(0.2)
                with lock:
                    S['pos'] = nums[:3]
                    if len(nums) >= 4:
                        S['facing'] = nums[3]
                        S['camera'] = nums[4] if len(nums) == 6 else nums[3]
                    say('cheats: returned to %.2f %.2f %.2f' % tuple(nums[:3]))
            elif w[:2] == ['!actors', 'reload']:
                def reload():
                    with lock:
                        S['loading'] = True
                    time.sleep(1.2)
                    with lock:
                        S['loading'] = False
                        S['pos'] = [0, 0, 9]
                    time.sleep(0.5)
                    say('actors: AreaA: 3 families, 20 placed, 10 never offered, 2 tied, 4 left out (load 2)')
                threading.Thread(target=reload, daemon=True).start()
            else:
                say('live: ' + line)


def serve(conn):
    f = conn.makefile('rwb')
    for raw in f:
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        cmd, rid = req.get('cmd'), req.get('id')
        ground()
        rep = {'id': rid, 'ok': True, 'frame': frame()}
        with lock:
            if cmd == 'status':
                rep.update(state='loading' if S['loading'] else S['state'], map=S['map'] if S['state'] == 'play' else None, fps=60.0, draws=900.0,
                           game={'pos': S['pos'], 'facing': S['facing'], 'camera': S['camera'], 'camera_pitch': S['pitch'], 'event': False,
                                 'loading': S['loading'], 'age': 'child', 'frame': frame(), 'map': S['map']} if S['state'] == 'play' else None)
            elif cmd == 'press':
                S['a'] = True
            elif cmd == 'stick' and req.get('stick') == 'R':
                S['camera'] = (S['camera'] - req.get('x', 0) * 1.5 * req.get('frames', 8) + 180.0) % 360.0 - 180.0
            elif cmd == 'stick':
                want = math.atan2(req.get('x', 0), req.get('y', 0))
                heading = math.radians(S['camera']) - want
                d = 4.0 * req.get('frames', 8) / 60.0 * math.hypot(req.get('x', 0), req.get('y', 0))
                S['pos'] = [S['pos'][0] + d * math.sin(heading), S['pos'][1], S['pos'][2] + d * math.cos(heading)]
            elif cmd == 'shot':
                Path(req['path']).write_bytes(b'\x89PNG fake')
            elif cmd == 'option_get':
                rep['option'] = {'key': req['name'], 'value': S['options'].get(req['name'], 0)}
            elif cmd == 'option_set':
                rep['was'] = S['options'].get(req['name'], 0)
                S['options'][req['name']] = req['value']
                rep['option'] = {'key': req['name'], 'value': req['value']}
            elif cmd == 'quit':
                S['quit'] = True
        f.write((json.dumps(rep) + '\n').encode())
        f.flush()
        if cmd == 'quit':
            return


say('vk: present: 2048x1152')
sockpath = env['HOS_CONTROL']
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sockpath)
srv.listen(4)
srv.settimeout(0.5)
threading.Thread(target=frames_thread, daemon=True).start()
threading.Thread(target=live_thread, daemon=True).start()
while not S['quit'] and time.monotonic() - start < wall:
    try:
        c, _ = srv.accept()
    except socket.timeout:
        continue
    threading.Thread(target=serve, args=(c,), daemon=True).start()
time.sleep(0.3)
(run_dir / 'summary.txt').write_text(f'hs-run {tag} exit=0 wall={time.monotonic() - start:.1f}s fake\n')
(run_dir / 'exit.txt').write_text('0\n')
(run_dir / 'present.csv').write_text('frame,return_ns,interval_ns,present_begin_ns,renderer_begin_ns,renderer_end_ns,vcore_back_ns\n')
