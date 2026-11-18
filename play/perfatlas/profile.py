#!/usr/bin/env python3
"""A profile run's window: the game's own profiling mode (`HOS_PROFILE=1`, the game repository's `scripts/profile_report.py`) names a part of a
run in seconds from its first present; the atlas names it by the host clock (`windows.jsonl`'s `t0_ns`, `t1_ns`). This turns one into the other.

`atlas.py hold` copies the folder the profiling mode made (`<copy>/logs/profile-<stamp>/`) into `<run>/profile/<name>/` when the run set
HOS_PROFILE, with the run's game log as `game.log` beside it (the report reads that). The report's origin is the first row of `presents.csv`
with a positive `return_ns`, on the same host clock as the windows.
"""
import csv
from pathlib import Path
import json


def first_present_ns(profile_dir):
    """the host-clock nanoseconds of the first present in a profile folder's presents.csv, or None"""
    p = Path(profile_dir) / 'presents.csv'
    if not p.exists():
        return None
    with open(p, newline='') as fh:
        rd = csv.reader(fh)
        head = next(rd, None)
        if not head or 'return_ns' not in head:
            return None
        i = head.index('return_ns')
        for r in rd:
            try:
                v = int(r[i])
            except (ValueError, IndexError):
                continue
            if v > 0:
                return v
    return None


def profile_folders(run_dir):
    return sorted(p for p in (Path(run_dir) / 'profile').glob('profile-*') if p.is_dir())


def window_args(run_dir, window=1):
    """{'folder', 'from_s', 'to_s', 'seconds'} for the n-th measured window of a profile run (default the first), or raise ValueError"""
    folders = profile_folders(run_dir)
    if not folders:
        raise ValueError(f'{run_dir}: no profile folder (the run did not set HOS_PROFILE, or it made none)')
    folder = folders[-1]
    origin = first_present_ns(folder)
    if origin is None:
        raise ValueError(f'{folder}: no presents.csv with a first present')
    wins = [json.loads(ln) for ln in (Path(run_dir) / 'windows.jsonl').read_text().splitlines() if ln.strip()]
    win = next((w for w in wins if w.get('n') == window), None)
    if win is None:
        raise ValueError(f'{run_dir}: no window {window}')
    lo, hi = (win['t0_ns'] - origin) / 1e9, (win['t1_ns'] - origin) / 1e9
    if hi <= 0 or lo < -1:
        raise ValueError(f'the window is not inside the profile (from {lo:.1f} s to {hi:.1f} s of its first present)')
    return {'folder': str(folder), 'from_s': round(max(lo, 0.0), 2), 'to_s': round(hi, 2), 'seconds': round(hi - max(lo, 0.0), 2)}


def report_command(args, script, maps, python='python3'):
    """the argv of the game repository's report for that window; `script` is its scripts/profile_report.py, `maps` a game folder of the same build whose
    binaries name the samples' frames (the profile may have been copied away from its own game folder)"""
    return [python, '-I', str(script), args['folder'], '--maps', str(maps), '--from-s', str(args['from_s']), '--to-s', str(args['to_s']), '--top', '30', '--no-trace']
