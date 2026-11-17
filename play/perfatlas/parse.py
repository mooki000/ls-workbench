#!/usr/bin/env python3
"""Readers for what a game run leaves behind, cut by time or by log offset into the windows a viewpoint run recorded.

Three sources, each read as it is (no guessing, a number that is not there is None):

  * the game's log (`[<thread>] vk: ...` lines, no timestamps): the periodic scene line (every 120 presents: draws,
    dispatches, passes, barriers, uploads, textures), `GPU time`, `a frame's replay time`, the draws left out, and the
    multi-line `GPU pass timing` block of RX_VK_PASS_TIMING (by kind, the slowest spans, with level 2 the programs).
    Lines are cut by their byte offset in the file, which the driver records at the start and end of a window.
  * the frame trace (RX_VK_FRAME_TRACE=1 with HOS_PROFILE_DIR=<folder>: `frames.csv` with one row a present and the
    FPS counter's split of the frame: game, render, gpu_wait, pace, waits, the main thread's time). Cut by `at_ns`, the
    host clock, which on Linux is CLOCK_MONOTONIC (`time.monotonic_ns()`), the clock the driver stamps its windows with.
  * the present trace (HOS_PRESENT_TRACE: `present.csv`, `return_ns` on the same clock): the fallback for the frame times.

Nothing here talks to a game. The formats are the ones the renderer prints (gpu/vk/vk_replay.cpp, vk_gprof.cpp); the tests
use made-up numbers in those formats.
"""
import csv
import math
import re
from pathlib import Path

NUM = r'(-?\d+(?:\.\d+)?)'
PREFIX = re.compile(r'^\[[^\]]*\]\s*')

SCENE = re.compile(
    r'vk: (\d+) presents, ' + NUM + r' fps; a frame: ' + NUM + r' draws \(' + NUM + r' skipped\), ' + NUM + r' dispatches, ' + NUM
    + r' passes, ' + NUM + r' barriers, ' + NUM + r' submits, ' + NUM + r' uploads \(' + NUM + r' MB\), ' + NUM + r' ub updates \('
    + NUM + r' overlays\), GPU-bound wait ' + NUM + r' ms.*?; (\d+) pipelines \((\d+) ms\), (\d+) programs \((\d+) failed\), (\d+) textures \((\d+) MB\)')
SCENE_KEYS = ('presents', 'fps_diagnostic', 'draws', 'skipped', 'dispatches', 'passes', 'barriers', 'submits', 'uploads', 'upload_mb',
              'ub_updates', 'overlays', 'gpu_bound_wait_ms', 'pipelines', 'pipeline_ms', 'programs', 'programs_failed', 'textures', 'texture_mb')
GPU_TIME = re.compile(r'vk: GPU time ' + NUM + r' ms a frame \(the longest submission ' + NUM + r' ms\); ' + NUM + r' textures made a frame')
REPLAY = re.compile(r"vk: a frame's replay time \(ms\):(.*)")
REPLAY_PAIR = re.compile(r'\s*([a-z][a-z ]*?) ' + NUM + r',?')
LEFT_OUT = re.compile(r'vk: a frame: ' + NUM + r' draws left out \(not ready\), ' + NUM + r' waits for shaders or pipelines \(' + NUM + r' ms\)')
GPU_WAITS = re.compile(r'vk: host time in GPU resource waits: ' + NUM + r' ms a frame')
MIRRORS = re.compile(r'vk: a frame: mirrors (\d+) \((\d+) MB\): ' + NUM + r' MB synced in ' + NUM + r' copies, ' + NUM + r' MB written back')
VRAM = re.compile(r'vk: VRAM: (\d+) MB used of (\d+) MB allowed .*?textures (\d+) MB \(budget (\d+) MB\), (\d+) textures')
ACTORS = re.compile(r'actors: (\S+): (\d+) families, (\d+) placed, (\d+) never offered, (\d+) tied, (\d+) left out \(load (\d+)\)')
ACTORS_RULE = re.compile(r'actors: (\S+): left out (\S+)')
POS = re.compile(r'cheats: returned to ' + NUM + ' ' + NUM + ' ' + NUM)

PASS_HEAD = re.compile(r'vk: GPU pass timing, (\d+) presents, per frame')
PASS_ALL = re.compile(r'vk:\s+' + NUM + r' ms a frame in all')
PASS_REC = re.compile(r"vk:\s+recordings' command buffers: " + NUM + r' ms from the first timestamp to the last in ' + NUM + r' a frame \(' + NUM + r' ms in spans')
PASS_UP = re.compile(r'vk:\s+upload command buffers: ' + NUM + r' ms in ' + NUM + r' a frame \(' + NUM + r' ms in spans')
PASS_KIND = re.compile(r'vk:\s+([mu]) (\S+)\s+' + NUM + r' ms\s+' + NUM + r'%\s+' + NUM + r' spans\s+' + NUM + r' items\s+' + NUM + r' MB\s+' + NUM + r' us a span')
PASS_SPAN = re.compile(r'vk:\s+([mu])\s+' + NUM + r' ms\s+' + NUM + r' x\s+' + NUM + r' us\s+' + NUM + r' items\s+' + NUM + r' MB\s+(.+?)\s*$')
PASS_PROG = re.compile(r'vk:\s+' + NUM + r' ms\s+' + NUM + r' runs\s+' + NUM + r' us a run\s+' + NUM + r' draws\s+' + NUM + r' us a draw\s+(.+?)\s*$')


def f(x):
    return float(x)


def read_lines(path):
    """[(byte offset of the line's start, text)] of the log's whole lines; a half line at the end (still being written) is left out"""
    data = Path(path).read_bytes()
    out, off = [], 0
    for raw in data.split(b'\n')[:-1]:   # (the last piece is empty after a final newline, or a half line still being written)
        out.append((off, raw.decode('utf-8', 'replace').rstrip('\r')))
        off += len(raw) + 1
    return out


def parse_line(text):
    """one log line as a record {'kind': ..., ...} or None for a line this module does not read"""
    t = PREFIX.sub('', text)
    m = SCENE.search(t)
    if m:
        v = m.groups()
        return {'kind': 'scene', **{k: f(x) for k, x in zip(SCENE_KEYS, v)}}
    m = GPU_TIME.search(t)
    if m:
        return {'kind': 'gpu_time', 'gpu_ms': f(m[1]), 'longest_ms': f(m[2]), 'textures_made': f(m[3])}
    m = REPLAY.search(t)
    if m:
        parts = {name.strip().replace(' ', '_'): f(v) for name, v in REPLAY_PAIR.findall(m[1])}
        return {'kind': 'replay', **parts}
    m = LEFT_OUT.search(t)
    if m:
        return {'kind': 'left_out', 'draws_left_out': f(m[1]), 'pipeline_waits': f(m[2]), 'pipeline_wait_ms': f(m[3])}
    m = GPU_WAITS.search(t)
    if m:
        return {'kind': 'gpu_waits', 'host_wait_ms': f(m[1])}
    m = MIRRORS.search(t)
    if m:
        return {'kind': 'mirrors', 'mirrors': f(m[1]), 'mirror_mb': f(m[2]), 'sync_mb': f(m[3]), 'sync_copies': f(m[4]), 'written_back_mb': f(m[5])}
    m = VRAM.search(t)
    if m:
        return {'kind': 'vram', 'used_mb': f(m[1]), 'allowed_mb': f(m[2]), 'textures_mb': f(m[3]), 'budget_mb': f(m[4]), 'textures': f(m[5])}
    m = ACTORS.search(t)
    if m:
        return {'kind': 'actors', 'area': m[1], 'families': int(m[2]), 'placed': int(m[3]), 'never_offered': int(m[4]), 'tied': int(m[5]),
                'left_out': int(m[6]), 'load': int(m[7])}
    m = ACTORS_RULE.search(t)
    if m:
        return {'kind': 'actors_left_out', 'area': m[1], 'what': m[2]}
    m = POS.search(t)
    if m:
        return {'kind': 'pos', 'x': f(m[1]), 'y': f(m[2]), 'z': f(m[3])}
    return None


def parse_log(lines):
    """records from [(offset, text)], each with its 'offset'; a GPU pass timing block (header, totals, by kind, spans, programs)
    is one record {'kind': 'pass_timing', ...} at the header's offset"""
    out = []
    block = None
    section = None
    for off, text in lines:
        t = PREFIX.sub('', text)
        m = PASS_HEAD.search(t)
        if m:
            block = {'kind': 'pass_timing', 'offset': off, 'presents': int(m[1]), 'total_ms': None, 'rec_ms': None, 'rec_buffers': None,
                     'rec_span_ms': None, 'up_ms': None, 'up_buffers': None, 'kinds': [], 'spans': [], 'programs': []}
            out.append(block)
            section = None
            continue
        if block is not None:
            if 'by kind' in t and t.startswith('vk:'):
                section = 'kinds'
                continue
            if 'spans that took the most' in t:
                section = 'spans'
                continue
            if 'programs whose runs of draws' in t:
                section = 'programs'
                continue
            m = PASS_ALL.search(t)
            if m and section is None and block['total_ms'] is None:
                block['total_ms'] = f(m[1])
                continue
            m = PASS_REC.search(t)
            if m and section is None:
                block['rec_ms'], block['rec_buffers'], block['rec_span_ms'] = f(m[1]), f(m[2]), f(m[3])
                continue
            m = PASS_UP.search(t)
            if m and section is None:
                block['up_ms'], block['up_buffers'] = f(m[1]), f(m[2])
                continue
            row = None
            if section == 'kinds' and (m := PASS_KIND.search(t)):
                block['kinds'].append({'cb': m[1], 'name': m[2], 'ms': f(m[3]), 'share': f(m[4]), 'spans': f(m[5]), 'items': f(m[6]), 'mb': f(m[7]), 'us_span': f(m[8])})
                row = True
            elif section == 'spans' and (m := PASS_SPAN.search(t)):
                block['spans'].append({'cb': m[1], 'ms': f(m[2]), 'n': f(m[3]), 'us_span': f(m[4]), 'items': f(m[5]), 'mb': f(m[6]), 'name': m[7]})
                row = True
            elif section == 'programs' and (m := PASS_PROG.search(t)):
                block['programs'].append({'ms': f(m[1]), 'runs': f(m[2]), 'us_run': f(m[3]), 'draws': f(m[4]), 'us_draw': f(m[5]), 'name': m[6]})
                row = True
            if row:
                continue
            if t.startswith('vk:') and section is not None and not row:
                block = section = None   # another vk: line: the block is over
        rec = parse_line(text)
        if rec:
            rec['offset'] = off
            out.append(rec)
    return out


def in_offsets(records, lo, hi):
    """records whose offset is in [lo, hi)"""
    return [r for r in records if lo <= r['offset'] < hi]


# ---------------------------------------------------------------- frame files

TRACE_NUMERIC = ('frame', 'ms', 'pipe_waits', 'pipe_wait_ms', 'left_out', 'translate_ms', 'uploads', 'upload_mb', 'textures', 'memsync_ms',
                 'pipelines_ms', 'uploads_ms', 'programs_ms', 'slot_wait_ms', 'gpu_behind_ms', 'draw_ms', 'desc_ms', 'cmds_ms', 'tex_lookup_ms',
                 'textures_gone', 'late_wait_ms', 'readback_wait_ms', 'tex_frame_ms', 'game_ms', 'render_ms', 'gpu_wait_ms', 'pace_ms',
                 'wait_render_ms', 'wait_gpu_ms', 'wait_main_ms', 'main_cpu_ms', 'main_blocked_ms', 'main_vcore_ms', 'at_ns')
TRACE_PARTS = ('game_ms', 'render_ms', 'gpu_wait_ms', 'pace_ms')


def read_trace(path):
    """RX_VK_FRAME_TRACE's frames.csv as a list of dicts of floats, in file order. A missing column is absent from the dict;
    a last row cut short by the end of the run is dropped"""
    rows = []
    with open(path, newline='') as fh:
        rd = csv.reader(fh)
        head = next(rd, None)
        if not head:
            return rows
        for r in rd:
            if len(r) != len(head):
                continue
            try:
                rows.append({k: float(v) for k, v in zip(head, r)})
            except ValueError:
                continue
    return rows


def read_present(path):
    """HOS_PRESENT_TRACE's present.csv (seven columns) as [{'frame', 'at_ns': return_ns, 'ms': interval_ms}]; the first row (interval 0) is dropped"""
    rows = []
    with open(path, newline='') as fh:
        rd = csv.reader(fh)
        head = next(rd, None)
        if not head or 'return_ns' not in head:
            return rows
        i_f, i_r, i_i = head.index('frame'), head.index('return_ns'), head.index('interval_ns')
        for r in rd:
            try:
                iv = int(r[i_i])
                if iv <= 0:
                    continue
                rows.append({'frame': float(r[i_f]), 'at_ns': float(r[i_r]), 'ms': iv / 1e6})
            except (ValueError, IndexError):
                continue
    return rows


def in_window(rows, t0_ns, t1_ns):
    """rows with t0 <= at_ns < t1"""
    return [r for r in rows if t0_ns <= r['at_ns'] < t1_ns]


def percentile(sorted_values, p):
    """nearest rank, p in 0..100; None for no values"""
    if not sorted_values:
        return None
    k = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[min(k, len(sorted_values)) - 1]


def describe(values):
    """n, mean, median, p99, max, min and the 1% low as frames per second (1000 / the mean of the slowest 1% of frames, at least one),
    and the counts over 16.7, 20 and 25 ms; every field None when there are no values"""
    v = sorted(x for x in values if x is not None and math.isfinite(x))
    if not v:
        return {'n': 0, 'mean': None, 'median': None, 'p99': None, 'max': None, 'min': None, 'low1_fps': None, 'over16_7': None, 'over20': None, 'over25': None}
    n = len(v)
    slow = v[-max(1, math.ceil(n * 0.01)):]
    return {'n': n, 'mean': sum(v) / n, 'median': percentile(v, 50), 'p99': percentile(v, 99), 'max': v[-1], 'min': v[0],
            'low1_fps': 1000.0 / (sum(slow) / len(slow)) if sum(slow) > 0 else None,
            'over16_7': sum(1 for x in v if x > 16.7), 'over20': sum(1 for x in v if x > 20.0), 'over25': sum(1 for x in v if x > 25.0)}


def mean_of(rows, key):
    xs = [r[key] for r in rows if key in r]
    return sum(xs) / len(xs) if xs else None
