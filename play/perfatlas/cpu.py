#!/usr/bin/env python3
"""CPU accounting for a window: how many cores the game used and by which threads, and how many cores everything else on the PC used
(the quiet rule's "other work"). Reads /proc only (`/proc/stat`, `/proc/<pid>/task/<tid>/stat`), standard library.

CPU time per frame is the measure that survives a busy machine: wall frame time grows when other work takes cores, the CPU time the
game's threads burn for a frame barely does. It is still an observation, not a speed result.

Ticks are USER_HZ (100 a second on Linux). A snapshot is {"t_ns", "sys_busy", "sys_total", "threads": {tid: [name, ticks]}} with
`sys_busy` and `sys_total` the machine's jiffies (all cores). The quiet rule of the exclusive worker: other work below 3 cores on
average and at most 6 in any 5-second sample.
"""
import os
import re
import time
from pathlib import Path

HZ = os.sysconf('SC_CLK_TCK') if hasattr(os, 'sysconf') else 100
QUIET_MEAN, QUIET_PEAK, SAMPLE_S = 3.0, 6.0, 5.0


def find_game_pid(sock_path, proc='/proc'):
    """the pid of the process whose environment holds HOS_CONTROL=<sock_path> (the game the wrapper started), or None.
    Only this user's processes are readable; the first match with the most threads wins (a wrapper shell has the same environment)."""
    want = f'HOS_CONTROL={sock_path}'.encode()
    best = None
    for p in Path(proc).iterdir():
        if not p.name.isdigit():
            continue
        try:
            env = (p / 'environ').read_bytes().split(b'\0')
        except OSError:
            continue
        if want in env:
            try:
                n = len(list((p / 'task').iterdir()))
            except OSError:
                n = 0
            if best is None or n > best[0]:
                best = (n, int(p.name))
    return best[1] if best else None


def read_system(proc='/proc'):
    """(busy, total) jiffies of all cores from the first line of /proc/stat; busy excludes idle and iowait"""
    with open(Path(proc) / 'stat') as f:
        parts = f.readline().split()
    v = [int(x) for x in parts[1:]]
    total = sum(v[:8])   # (user nice system idle iowait irq softirq steal; guest time is inside user)
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return total - idle, total


def parse_task_stat(text):
    """(comm, utime + stime ticks) of one /proc/<pid>/task/<tid>/stat line; the name is inside the parentheses and may hold spaces"""
    i, j = text.find('('), text.rfind(')')
    if i < 0 or j < i:
        return None
    rest = text[j + 2:].split()
    if len(rest) < 13:
        return None
    return text[i + 1:j], int(rest[11]) + int(rest[12])


def read_threads(pid, proc='/proc'):
    out = {}
    try:
        tids = list((Path(proc) / str(pid) / 'task').iterdir())
    except OSError:
        return out
    for t in tids:
        try:
            got = parse_task_stat((t / 'stat').read_text())
        except OSError:
            continue
        if got:
            out[t.name] = [got[0], got[1]]
    return out


class Sampler:
    def __init__(self, pid, proc='/proc', clock=time.monotonic_ns):
        self.pid, self.proc, self.clock = pid, proc, clock

    def snapshot(self):
        busy, total = read_system(self.proc)
        return {'t_ns': self.clock(), 'sys_busy': busy, 'sys_total': total, 'threads': read_threads(self.pid, self.proc)}


def group_name(comm):
    """a thread's group: the name without its trailing number ('BaseProcExecutor3' -> 'BaseProcExecutor'); a pool's workers share one"""
    return re.sub(r'[\d]+$', '', comm).rstrip('_- ') or comm


def game_ticks(snap):
    """the game's ticks in a snapshot: its `game_ticks` when the threads were left out (a mid-window sample), else the threads' sum"""
    if 'game_ticks' in snap:
        return snap['game_ticks']
    return sum(t[1] for t in snap['threads'].values())


def threads_between(a, b):
    """{group: ticks} the game's threads used between two snapshots (a thread that ended in between is missed: its last ticks are in
    neither; a thread that began is counted from zero)"""
    out = {}
    for tid, (name, ticks) in b['threads'].items():
        before = a['threads'].get(tid, [name, 0])[1]
        g = group_name(name)
        out[g] = out.get(g, 0) + max(0, ticks - before)
    return out


def window_cpu(snaps):
    """summarise a window's snapshots (at least two, in time order):
    {'seconds', 'game_cores', 'machine_cores', 'others_mean', 'others_peak', 'quiet', 'samples': n, 'groups': {group: ticks}, 'game_ticks'}
    others = the machine's busy cores minus the game's; the peak is over consecutive stretches of at least SAMPLE_S seconds (the worker's 5-second samples)."""
    if len(snaps) < 2:
        return None
    first, last = snaps[0], snaps[-1]
    secs = (last['t_ns'] - first['t_ns']) / 1e9
    if secs <= 0:
        return None
    gt = game_ticks(last) - game_ticks(first)
    busy = last['sys_busy'] - first['sys_busy']
    others_mean = (busy - gt) / HZ / secs
    peak, anchor = None, first
    for s in snaps[1:]:
        dt = (s['t_ns'] - anchor['t_ns']) / 1e9
        if dt >= SAMPLE_S:
            o = ((s['sys_busy'] - anchor['sys_busy']) - (game_ticks(s) - game_ticks(anchor))) / HZ / dt
            peak = o if peak is None else max(peak, o)
            anchor = s
    if peak is None:   # (a window shorter than one sample: its one stretch is the sample)
        peak = others_mean
    return {'seconds': secs, 'game_cores': gt / HZ / secs, 'machine_cores': busy / HZ / secs, 'others_mean': others_mean, 'others_peak': peak,
            'quiet': others_mean < QUIET_MEAN and peak <= QUIET_PEAK, 'samples': len(snaps), 'groups': threads_between(first, last), 'game_ticks': gt}
