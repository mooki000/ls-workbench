#!/usr/bin/env python3
"""How long the game's sleeps really last, from a HOS_CHAIN recording: each sleep (the length asked is in its record) against the time to its thread's next wake.

The recorder (`HOS_CHAIN=<file>`, a `<file>.go` made for part of a window) notes every guest sleep and the thread's return from it. A sleep that lasts much longer than
asked is the kernel's timer slack (50 us by default on Linux: a 20 us poll lasted about 70 us) or a busy machine; the per-thread time is what the table adds up.
`--sites N` also lists the N sleeps that cost the most by their caller's guest address: that is game-derived, so keep that output out of repositories.

    python3 sleeps.py <chain file> [--sites N] [--json out.json]
"""
import argparse
import collections
import json
import statistics
import struct
import sys
from pathlib import Path

REC = struct.Struct('<qQQHB5x')   # time ns, key (a sleep: ns asked), caller, thread index, kind
SLEEP, AWAKE, PRESENT = 4, 5, 6
BUCKETS = (('under 100 us', 0, 100_000), ('100 us to 1 ms', 100_000, 1_000_000), ('1 ms to 20 ms', 1_000_000, 20_000_000), ('20 ms and over', 20_000_000, 1 << 62))


def read_chain(path):
    """(thread names by index, records) of a chain file"""
    with open(path, 'rb') as f:
        if f.readline().strip() != b'HOSCHAIN1':
            raise ValueError(f'{path}: not a HOS_CHAIN recording')
        names = {}
        while True:
            line = f.readline().decode('utf-8', 'replace').split()
            if not line:
                raise ValueError(f'{path}: no records line')
            if line[0] == 'records':
                n = int(line[1])
                break
            names[int(line[1])] = line[2]
        data = f.read(n * REC.size)
    n = len(data) // REC.size
    return names, [REC.unpack_from(data, i * REC.size) for i in range(n)]


def group(name):
    """a thread's group: the name without its trailing number ('Worker3' -> 'Worker')"""
    return name.rstrip('0123456789') or name


def analyze(records, sites=0, names=None):
    """{'seconds', 'presents', 'buckets': [...], 'sites': [...]}; each bucket: sleeps a second, asked median us, slept mean / median / p90 us, sleep ms and overshoot ms a frame"""
    if not records:
        raise ValueError('no records')
    t0, t1 = min(r[0] for r in records), max(r[0] for r in records)
    seconds = (t1 - t0) / 1e9
    presents = sum(1 for r in records if r[4] == PRESENT)
    open_, got, by_thread = {}, [], collections.defaultdict(list)   # got: (asked ns, slept us, caller); by_thread: group -> slept us of the sleeps under 100 us
    for t, key, lr, th, kind in ((r[0], r[1], r[2], r[3], r[4]) for r in records):
        if kind == SLEEP:
            open_[th] = (t, key, lr)
        elif kind == AWAKE and th in open_:
            s, asked, caller = open_.pop(th)
            got.append((asked, (t - s) / 1000.0, caller))
            if asked < BUCKETS[0][2]:
                by_thread[group((names or {}).get(th, f'thread {th}'))].append((t - s) / 1000.0)
    frames = max(presents, 1)
    rows = []
    for label, lo, hi in BUCKETS:
        xs = [(a, s) for a, s, _ in got if lo <= a < hi]
        if not xs:
            rows.append({'bucket': label, 'sleeps': 0})
            continue
        slept = sorted(s for _, s in xs)
        rows.append({'bucket': label, 'sleeps': len(xs), 'per_s': len(xs) / seconds, 'asked_median_us': statistics.median(a / 1000.0 for a, _ in xs),
                     'slept_mean_us': statistics.mean(slept), 'slept_median_us': statistics.median(slept), 'slept_p90_us': slept[int(0.9 * (len(slept) - 1))],
                     'sleep_ms_per_frame': sum(slept) / 1000.0 / frames, 'overshoot_ms_per_frame': sum(max(0.0, s - a / 1000.0) for a, s in xs) / 1000.0 / frames})
    out = {'seconds': seconds, 'presents': presents, 'buckets': rows, 'sites': [],
           'threads': [{'group': g, 'sleeps': len(v), 'slept_median_us': statistics.median(v), 'slept_p90_us': sorted(v)[int(0.9 * (len(v) - 1))]}
                       for g, v in sorted(by_thread.items(), key=lambda kv: -len(kv[1]))]}
    if sites:
        by = collections.defaultdict(list)
        for a, s, caller in got:
            by[(caller, a)].append(s)
        top = sorted(by.items(), key=lambda kv: -sum(kv[1]))[:sites]
        out['sites'] = [{'caller': hex(c), 'asked_us': a / 1000.0, 'per_s': len(v) / seconds, 'slept_median_us': statistics.median(v), 'total_ms_per_s': sum(v) / 1000.0 / seconds}
                        for (c, a), v in top]
    return out


def to_markdown(res):
    out = [f"{res['seconds']:.2f} s recorded, {res['presents']} presents. *(One recording: an observation, no speed claim.)*", '',
           '| asked | sleeps a second | asked median us | slept median us | mean | p90 | sleep ms a frame | over what was asked, ms a frame |', '|---|---|---|---|---|---|---|---|']
    for r in res['buckets']:
        if not r['sleeps']:
            out.append(f"| {r['bucket']} | 0 | - | - | - | - | - | - |")
        else:
            out.append(f"| {r['bucket']} | {r['per_s']:.0f} | {r['asked_median_us']:.1f} | {r['slept_median_us']:.1f} | {r['slept_mean_us']:.1f} | {r['slept_p90_us']:.1f} | "
                       f"{r['sleep_ms_per_frame']:.2f} | {r['overshoot_ms_per_frame']:.2f} |")
    if res['threads']:
        out += ['', 'The sleeps under 100 us by thread group (a timer slack set before the threads were made shows as one floor in every group that sleeps):', '',
                '| thread group | sleeps | slept median us | p90 |', '|---|---|---|---|']
        out += [f"| {t['group']} | {t['sleeps']} | {t['slept_median_us']:.1f} | {t['slept_p90_us']:.1f} |" for t in res['threads']]
    if res['sites']:
        out += ['', '| caller (guest) | asked us | a second | slept median us | ms a second |', '|---|---|---|---|---|']
        out += [f"| {s['caller']} | {s['asked_us']:.1f} | {s['per_s']:.0f} | {s['slept_median_us']:.1f} | {s['total_ms_per_s']:.1f} |" for s in res['sites']]
    return '\n'.join(out) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('chain')
    ap.add_argument('--sites', type=int, default=0, help='also the N sleeps that cost most, by caller (game addresses: keep this output local)')
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    names, recs = read_chain(a.chain)
    res = analyze(recs, a.sites, names)
    print(to_markdown(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
