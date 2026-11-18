#!/usr/bin/env python3
"""A profile's samples charged to named families, as CPU a frame and as time on the frame's chain.

The game's profiling mode (`HOS_PROFILE=1`) writes every game thread's stack every few milliseconds (`all.txt`). This reads it with the game
repository's own `scripts/samples.py` and `scripts/prof.py` (they name the frames from the build's symbols) and a **families file** the
caller writes: an ordered list of `{"name": ..., "markers": [...]}`. A running sample goes to the first family with a marker that is a substring
of the name of any frame in its stack, else to `other`. Two numbers come out, each in ms of the frame:

* **cpu**: the samples of the game threads that were running, over the instants sampled, times the frame ms: the CPU a frame the family used.
* **chain**: only the instants when the main thread is blocked (it is waiting for the job graph): each running game thread then counts 1/k, k the
  number of game threads running at that instant. A family that runs alone while the main thread waits is what the frame waits for; one that runs
  beside five others costs CPU but little frame. `chain_le2` is the part of it at k of 1 or 2: the tail.

It is an attribution of one sampled run (the sampler itself costs CPU), never a price: a price is a paired timing of the part switched off. A
chain share is an upper bound for what removing the family could give, since the wake-ups between jobs stay.

    python3 families.py <profile folder> --scripts <game repo>/scripts --maps <game folder> --families families.json --frame-ms 19.2 [--from-s 25 --to-s 85]
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

from . import profile as profmod


def family_of(names, families):
    """the first family (by the file's order) with a marker inside any frame name; None for other"""
    for fam in families:
        for marker in fam['markers']:
            if any(marker in n for n in names):
                return fam['name']
    return None


def attribute(ticks, config, frame_ms):
    """ticks: {instant: {thread: (running, [frame names, leaf first])}}; config: {'game_threads': [prefix], 'main_thread': name, 'families': [...]}.
    Returns {'instants', 'cpu_ms', 'rows': [{name, cpu_ms, cpu_share, chain_ms, chain_le2_ms}], 'main_blocked_share', 'running_hist'}"""
    fams = config['families']
    game = tuple(config['game_threads'])
    main = config['main_thread']
    n = len(ticks)
    if not n:
        raise ValueError('no instants in the window')
    cpu, chain, few = collections.Counter(), collections.Counter(), collections.Counter()
    blocked, hist = 0, collections.Counter()
    for t in ticks.values():
        run = {th: v[1] for th, v in t.items() if v[0] and th.startswith(game)}
        for th, names in run.items():
            cpu[family_of(names, fams) or 'other'] += 1
        m = t.get(main)
        if m and not m[0]:
            blocked += 1
            k = len(run)
            hist[min(k, 9)] += 1
            for th, names in run.items():
                f = family_of(names, fams) or 'other'
                chain[f] += 1.0 / k
                if k <= 2:
                    few[f] += 1.0 / k
    total = sum(cpu.values())
    names = [f['name'] for f in fams] + ['other']
    rows = [{'name': nm, 'cpu_ms': cpu[nm] / n * frame_ms, 'cpu_share': cpu[nm] / total if total else 0.0, 'chain_ms': chain[nm] / n * frame_ms,
             'chain_le2_ms': few[nm] / n * frame_ms} for nm in names]
    return {'instants': n, 'cpu_ms': total / n * frame_ms, 'chain_ms': sum(chain.values()) / n * frame_ms, 'rows': rows,
            'main_blocked_share': blocked / n, 'running_hist': {k: v / blocked for k, v in sorted(hist.items())} if blocked else {}}


def to_markdown(res, frame_ms):
    out = [f"{res['instants']} instants; the frame {frame_ms:.1f} ms; game CPU {res['cpu_ms']:.1f} ms a frame; the main thread blocked in {100 * res['main_blocked_share']:.0f}% "
           f"of the instants; chain {res['chain_ms']:.1f} ms a frame. *(One sampled run: an attribution, no speed claim.)*", '',
           '| family | CPU ms a frame | % of CPU | chain ms | chain at k<=2 ms |', '|---|---|---|---|---|']
    for r in res['rows']:
        out.append(f"| {r['name']} | {r['cpu_ms']:.2f} | {100 * r['cpu_share']:.1f} | {r['chain_ms']:.2f} | {r['chain_le2_ms']:.2f} |")
    return '\n'.join(out) + '\n'


def load_ticks(samples_file, scripts, maps, lo_ms=None, hi_ms=None):
    """the sampler's file, symbolized with the game repository's own modules; instants lo_ms..hi_ms of the sampler's clock"""
    os.environ['RX_MAPS'] = str(maps)
    sys.path.insert(0, str(scripts))
    try:
        import prof      # noqa: E402  (the game repository's scripts/prof.py)
        import samples   # noqa: E402  (its scripts/samples.py)
    finally:
        sys.path.remove(str(scripts))
    import bisect
    syms = prof.load_symbols()
    cache = {}

    def name(frame):
        if frame in cache:
            return cache[frame]
        mod, _, off = frame.rpartition('+')
        r = mod
        if mod in syms:
            addrs, names = syms[mod]
            i = bisect.bisect_right(addrs, int(off, 16)) - 1
            if i >= 0:
                r = prof.module_name(mod) + ':' + prof.demangle(names[i])
        cache[frame] = r
        return r
    ticks = collections.defaultdict(dict)
    for line in samples.lines(str(samples_file)):
        head, _, stack = line.rstrip('\n').partition('|')
        ms, _, thread = head.partition(' ')
        if not stack or not ms.isdigit():
            continue
        t = int(ms)
        if (lo_ms is not None and t < lo_ms) or (hi_ms is not None and t > hi_ms):
            continue
        frames = stack.split(';')
        names = [name(f) for f in frames]
        ticks[t][thread] = (not prof.waiting(frames[0].rpartition('+')[0], names), names)
    return ticks


def window_ms(folder, from_s, to_s):
    """the sampler's clock (ms from its `# t0_ns` line) of the seconds-from-the-first-present window the report uses"""
    folder = Path(folder)
    with (folder / 'all.txt').open() as fh:
        head = fh.readline().split()
    if head[:2] != ['#', 't0_ns']:
        raise ValueError(f'{folder}/all.txt has no "# t0_ns" first line')
    origin = profmod.first_present_ns(folder)
    if origin is None:
        raise ValueError(f'{folder}: no first present in presents.csv')
    base = (origin - int(head[2])) / 1e6
    return base + (from_s or 0.0) * 1000.0, (base + to_s * 1000.0) if to_s is not None else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('folder', help='a profile folder (all.txt, presents.csv)')
    ap.add_argument('--scripts', required=True, help="the game repository's scripts folder (samples.py, prof.py)")
    ap.add_argument('--maps', required=True, help='a game folder of the same build (its binaries name the frames)')
    ap.add_argument('--families', required=True, help='JSON: game_threads (name prefixes), main_thread, families [{name, markers}]')
    ap.add_argument('--frame-ms', type=float, required=True, help="the window's mean frame ms (the report's At a glance)")
    ap.add_argument('--from-s', type=float)
    ap.add_argument('--to-s', type=float)
    ap.add_argument('--json', help='also write the result here')
    a = ap.parse_args(argv)
    config = json.loads(Path(a.families).read_text())
    lo, hi = window_ms(a.folder, a.from_s, a.to_s)
    ticks = load_ticks(Path(a.folder) / 'all.txt', a.scripts, a.maps, lo, hi)
    res = attribute(ticks, config, a.frame_ms)
    print(to_markdown(res, a.frame_ms))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
