"""Stuck recovery for the playthrough pilot: notice that Link is not getting anywhere, climb a ladder of ways out, remember
where it happened, and never ask a model a question whose answer is already known.

What it does, in the order the pilot meets it:

  1. `Progress` watches the travel steps (go, go-near, the step round an obstacle): the position and the distance to the
     target over a window. A step that ended `stuck` is a block at once; steps that gain no ground (a few in a row, or
     a window of game time) are no progress; `reached` at a stand-off point short of the target's radius is `short`.
  2. `Recovery.action` is the ladder, one rung per decision, no model call: sidestep, back up and come at it from an
     angle, roll, a jump-attack-free hop, skip the stuck route point, and last a local re-plan (the pilot calls its
     planner) with the point marked bad and the target left. Any ground gained ends the ladder: the rung that worked is
     remembered for that spot (`soft`), a ladder that failed marks the spot `bad`.
  3. `BadPoints` is the memory, one file per map under the cache folder (pilot/cache, or $PILOT_CACHE): a point, a radius,
     what happened and which rung got past it. `reroute` bends a route's waypoints around the points before they are
     sent, `nudge_segment` moves a checkpoint out of a bad point's circle, and the planner reads what was tried.
  4. `Recovery.gate` is the guard in front of the decider: a move that already ended stuck here is not offered again; one
     valid candidate is taken without a question; a state that did not change since the last identical decision is
     not asked about again (a move that went nowhere is recovery, not another try); a cap on identical answers in a
     row turns into recovery instead of another call.

No game and no network are used here: positions, steps and results are plain data.
"""
import fcntl
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

LADDER = ('sidestep', 'angle', 'roll', 'hop', 'next-point', 'replan')
TRAVEL = ('go', 'go-near', 'back-off', 'side-left', 'side-right')   # moves meant to cover ground (recovery labels start `recover:`)
OK_RESULTS = ('reached', 'done', 'met')
NEUTRAL_RESULTS = ('held', 'cut', 'bad', 'no-opponent', 'refused')   # nothing about ground gained or lost
# an answer that may be given again from the last one when nothing changed (a talk, a scene, a wait)
REPEAT_OK = ('wait', 'wait-goal', 'wait-prompt', 'wait-a-moment', 'wait-friend', 'settle', 'talk-through', 'talk-until-goal',
             'press-a-through', 'press-a')
BAD_RADIUS = {'bad': 2.5, 'soft': 1.5}
MERGE_M = 1.5
NO_REPEAT = ('go', 'go-near')   # a candidate whose lines already ended stuck from here is not offered again
# what the eyes (vision.py) may offer at a stuck point; the first of them goes before the ladder's own rungs, once per block
VISION_RUNGS = ('vision-side-left', 'vision-side-right', 'vision-wait-scene', 'vision-talk', 'vision-fight')
ARRIVE_SLACK_M = 0.25          # Link stands this far outside a target's radius after a move that reached it (he drifts a little)
DIRS = [(0, 'up'), (45, 'up-right'), (90, 'right'), (135, 'down-right'), (180, 'down'), (-135, 'down-left'),
        (-90, 'left'), (-45, 'up-left')]


def wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def yaw_to(dx, dz):
    return math.degrees(math.atan2(dx, dz))


def heading(yaw):
    """(dx, dz) of a unit step along the yaw (0 along +z, 90 along +x)."""
    r = math.radians(yaw)
    return math.sin(r), math.cos(r)


def is_travel(label):
    return label in TRAVEL or str(label).startswith(('recover:', 'vision-side'))


def parse_move(line):
    w = line.split()
    if len(w) < 3 or w[0] not in ('walk', 'run'):
        return None
    try:
        return w[0], float(w[1]), float(w[2]), w[3:]
    except ValueError:
        return None


def move_line(verb, x, z, radius, timeout):
    return f'{verb} {x:.2f} {z:.2f} radius {radius:g} timeout {int(timeout)}'


def run_timeout(metres):
    return int(max(6, metres / 3 + 5))


def seg_dist(p, a, b):
    """(distance, t) of point p to the segment a-b, t in 0..1 along it."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dz * dz
    t = 0.0 if ll < 1e-9 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / ll))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dz)), t


def stick_for(yaw, camera, sign=1.0, mag=1.0):
    """(lx, ly) in -32767..32767 that moves Link toward the world yaw: the stick's angle is the camera's yaw minus the wanted
    yaw, right positive (the game side's walk steers the same way; `sign` is its learned handedness)."""
    want = math.radians(wrap(camera - yaw) * sign)
    return round(mag * 32767 * math.sin(want)), round(mag * 32767 * math.cos(want))


def dir_name(yaw, camera, sign=1.0):
    want = wrap(camera - yaw) * sign
    return min(DIRS, key=lambda d: abs(wrap(d[0] - want)))[1]


def is_blocked(result, moved=0.0, left=None):
    """A travel step that ended against something: `stuck` with a metre or more still to the step's own point, or a timeout
    that gained hardly any ground."""
    if result == 'stuck':
        return left is None or left >= 1.0
    return result == 'timeout' and moved < 2.0


# ---- the memory

class BadPoints:
    """Where Link got stuck, one JSON file per map in the cache folder: [{x, z, kind 'bad'|'soft', radius, count, why, fix,
    runs, targets, first, last}]. `bad` is a ladder that failed, `soft` a block that a rung got past (`fix`). The files are
    game-derived: they stay out of version control and out of the publication."""

    def __init__(self, folder=None):
        self.dir = Path(folder or os.environ.get('PILOT_CACHE') or HERE / 'cache')
        self._seen = {}

    @staticmethod
    def safe(area):
        return re.sub(r'[^A-Za-z0-9_.-]', '_', str(area))

    def path(self, area):
        return self.dir / f'badpoints-{self.safe(area)}.json'

    def load(self, area):
        p = self.path(area)
        try:
            stamp = p.stat().st_mtime_ns
        except OSError:
            return []
        hit = self._seen.get(area)
        if hit and hit[0] == stamp:
            return hit[1]
        try:
            rows = json.loads(p.read_text())
        except (OSError, ValueError):
            rows = []
        rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get('x'), (int, float)) and isinstance(r.get('z'), (int, float))]
        self._seen[area] = (stamp, rows)
        return rows

    def add(self, area, x, z, kind='bad', why='', run=None, target=None, fix=None):
        """Record a point (merged with one within MERGE_M, the position averaged, a `bad` stays `bad`); returns the entry."""
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S')
        path = self.path(area)
        with open(self.dir / f'badpoints-{self.safe(area)}.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                rows = [dict(r) for r in json.loads(path.read_text())] if path.exists() else []
            except ValueError:
                rows = []
            near = min((r for r in rows if math.hypot(r['x'] - x, r['z'] - z) <= MERGE_M),
                       key=lambda r: math.hypot(r['x'] - x, r['z'] - z), default=None)
            if near is None:
                near = {'x': round(x, 2), 'z': round(z, 2), 'kind': kind, 'radius': BAD_RADIUS[kind], 'count': 0,
                        'why': why, 'fix': None, 'runs': [], 'targets': [], 'first': stamp}
                rows.append(near)
            c = near['count']
            near['x'], near['z'] = round((near['x'] * c + x) / (c + 1), 2), round((near['z'] * c + z) / (c + 1), 2)
            near['count'] = c + 1
            if kind == 'bad':
                near['kind'], near['why'] = 'bad', why or near.get('why', '')
            near['radius'] = BAD_RADIUS[near['kind']]
            if fix:
                near['fix'] = fix
            near['last'] = stamp
            if run and run not in near['runs']:
                near['runs'] = (near['runs'] + [run])[-5:]
            if target and target not in near['targets']:
                near['targets'] = (near['targets'] + [target])[-5:]
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(rows, indent=1))
            os.replace(tmp, path)
            fcntl.flock(lock, fcntl.LOCK_UN)
        self._seen.pop(area, None)
        return near

    def circles(self, area):
        return [(r['x'], r['z'], float(r.get('radius') or BAD_RADIUS.get(r.get('kind'), 2.0)), r) for r in self.load(area)]

    def near(self, area, x, z, margin=0.0):
        """The entry whose circle (plus margin) holds the point, the nearest one, else None."""
        hits = [(math.hypot(cx - x, cz - z), e) for cx, cz, r, e in self.circles(area) if math.hypot(cx - x, cz - z) <= r + margin]
        return min(hits, key=lambda h: h[0])[1] if hits else None

    def nudge(self, area, x, z, toward=None):
        """The nearest point outside every circle that holds (x, z): pushed away from the circle's centre, or straight toward
        `toward` when it sits at the centre."""
        for _ in range(4):
            e = self.near(area, x, z)
            if e is None:
                break
            r = float(e.get('radius') or BAD_RADIUS.get(e.get('kind'), 2.0))
            dx, dz = x - e['x'], z - e['z']
            d = math.hypot(dx, dz)
            if d < 1e-3:
                dx, dz = (toward[0] - e['x'], toward[1] - e['z']) if toward else (1.0, 0.0)
                d = math.hypot(dx, dz) or 1.0
            x, z = e['x'] + dx / d * (r + 0.6), e['z'] + dz / d * (r + 0.6)
        return x, z

    def reroute(self, area, start, lines, max_detours=3):
        """The move lines bent around the circles that a leg crosses: a waypoint beside the circle, on the side away from its
        centre (the side a past fix names, if any), before the leg that crosses it. A leg that ends inside a circle or starts in
        one is left as it is (the target is there; the ladder deals with the rest). Returns (lines, detours)."""
        circles = self.circles(area)
        if not circles or start is None:
            return list(lines), []
        out, detours, prev = [], [], (start[0], start[1])
        for line in lines:
            m = parse_move(line)
            if not m:
                out.append(line)
                continue
            verb, x, z, _ = m
            end = (x, z)
            while len(detours) < max_detours:
                hit = None
                for cx, cz, r, e in circles:
                    if math.hypot(cx - end[0], cz - end[1]) <= r or math.hypot(cx - prev[0], cz - prev[1]) <= r:
                        continue
                    d, t = seg_dist((cx, cz), prev, end)
                    if d < r and 0.0 < t < 1.0:
                        hit = (cx, cz, r, e)
                        break
                if not hit:
                    break
                cx, cz, r, e = hit
                ll = math.hypot(end[0] - prev[0], end[1] - prev[1]) or 1.0
                ux, uz = (end[0] - prev[0]) / ll, (end[1] - prev[1]) / ll
                nx, nz = -uz, ux   # left of the leg
                cross = ux * (cz - prev[1]) - uz * (cx - prev[0])
                prefer = {'sidestep-left': 1.0, 'sidestep-right': -1.0}.get(e.get('fix'))
                side = prefer if prefer is not None else (-1.0 if cross > 0 else 1.0)
                way = None
                for s in (side, -side):
                    for margin in (1.0, 2.0):
                        w = (cx + s * nx * (r + margin), cz + s * nz * (r + margin))
                        if not any(math.hypot(ox - w[0], oz - w[1]) <= orr for ox, oz, orr, _ in circles):
                            way = w
                            break
                    if way:
                        break
                if not way:
                    break
                out.append(move_line(verb, way[0], way[1], 1.2, run_timeout(math.hypot(way[0] - prev[0], way[1] - prev[1]))))
                detours.append({'around': [e['x'], e['z']], 'via': [round(way[0], 2), round(way[1], 2)], 'kind': e.get('kind')})
                prev = way
            out.append(line)
            prev = end
        return out, detours


def nudge_segment(seg, bad, area=None):
    """Checkpoints that lie inside a `bad` point's circle are moved to its edge (the radius grows by the move, so arriving there
    counts). Returns [{target, from, to}] for the transcript. Follow targets (another player) are never moved."""
    moved = []
    area = area or seg.area
    for g in seg.goals:
        for t in g.targets:
            if t.follow:
                continue
            e = bad.near(area, t.x, t.z)
            if e is None or e.get('kind') != 'bad':
                continue
            nx, nz = bad.nudge(area, t.x, t.z)
            shift = math.hypot(nx - t.x, nz - t.z)
            moved.append({'target': t.name, 'from': [t.x, t.z], 'to': [round(nx, 2), round(nz, 2)]})
            t.x, t.z, t.radius = round(nx, 2), round(nz, 2), max(t.radius, shift + 0.5)
    return moved


# ---- the monitor

class Progress:
    """Position and distance to the target across the travel steps of one target. `observe` after each travel step; the
    verdict says whether Link is blocked (a step ended stuck), has gained no ground for `steps` steps or `window_s` of game
    time, or stands at a stand-off point short of the target's radius. Ground gained is the distance to the target
    shrinking by `gain_m`, arriving, or a step that reached its point `roam_m` away from where the last ground was gained."""

    def __init__(self, gain_m=1.5, roam_m=4.0, window_s=10.0, steps=3):
        self.gain_m, self.roam_m, self.window_s, self.steps = gain_m, roam_m, window_s, steps
        self.anchor_t = self.anchor_pos = self.best = None
        self.stalled_obs = 0
        self.gained = False
        self.verdict = self.fresh()

    @staticmethod
    def fresh():
        return {'stuck': False, 'why': None, 'stalled_obs': 0, 'stalled_s': 0.0, 'short': False}

    def anchor(self, t, pos, dist):
        self.anchor_t, self.anchor_pos, self.best = t, pos, dist
        self.stalled_obs = 0

    def observe(self, t, pos, dist, result=None, moved=0.0, left=None, radius=None, travel=True, ladder=False):
        """The verdict after a travel step that ended `result` having moved `moved` metres, `left` still from its own point
        (pos is (x, z)). `ladder`: a recovery rung ran, so no ground gained is a failed rung whatever the counts."""
        self.gained = False
        if pos is None:
            return self.verdict
        if self.anchor_pos is None:
            self.anchor(t, pos, dist)
            return self.verdict
        if not travel or result in NEUTRAL_RESULTS:
            return self.verdict
        arrived = dist is not None and radius is not None and dist <= radius + ARRIVE_SLACK_M
        gain = arrived or (dist is not None and self.best is not None and dist <= self.best - self.gain_m)
        roam = result in OK_RESULTS and math.hypot(pos[0] - self.anchor_pos[0], pos[1] - self.anchor_pos[1]) >= self.roam_m
        blocked = is_blocked(result, moved, left) and not arrived   # (stuck inside the target's radius is an arrival: nothing to climb out of)
        self.gained = bool(gain or roam) and (gain or not blocked)
        stalled_s = round((t or 0) - (self.anchor_t or 0), 2) if t is not None and self.anchor_t is not None else 0.0
        if gain or roam:
            self.anchor(t, pos, dist if dist is not None else self.best)
            stalled_s = 0.0
        else:
            self.stalled_obs += 1
        why = None
        if blocked:
            why = 'blocked'
        elif not (gain or roam) and (self.stalled_obs >= self.steps or (self.stalled_obs >= 2 and stalled_s >= self.window_s) or ladder):
            why = 'no-progress'
        short = (not why and result == 'reached' and dist is not None and radius is not None and dist > radius + ARRIVE_SLACK_M)
        self.verdict = {'stuck': bool(why), 'why': why or ('short' if short else None), 'stalled_obs': self.stalled_obs,
                        'stalled_s': stalled_s, 'short': short}
        return self.verdict


@dataclass
class Action:
    """What a recovery rung does: step lines for the pilot to send, or `replan` (the pilot calls its planner, marks the point
    and leaves the target)."""
    label: str
    lines: list
    text: str
    rung: str = ''
    kind: str = 'lines'
    at: tuple = None


@dataclass
class RecState:
    """Per target: the monitor, the ladder's place and what is known about the block being worked on."""
    progress: Progress = field(default_factory=Progress)
    tried: list = field(default_factory=list)       # rungs tried in the current block
    side: int = 1                                    # +1 left of the way to the target, -1 right: the next sidestep's side
    blocked: dict = None                             # {pos, t, label, lines, idx}
    last_go: dict = None                             # the last travel candidate's lines
    closed_in: bool = False
    streak: int = 0                                  # identical answers in a row without ground gained
    streak_key: tuple = None
    last_key: tuple = None
    last_label: str = None
    last_ask: bool = False
    reuse: int = 0
    episodes: list = field(default_factory=list)
    learned: str = None
    vision_used: bool = False                        # the eyes' own rung was taken in this block
    episode: dict = None

    @property
    def verdict(self):
        return self.progress.verdict


def signature(st):
    """What counts as the same state for 'nothing changed': a cell of 0.3 m, the map, the prompt, an event, a load, the
    watched flags and the life."""
    pos = st.get('pos')
    cell = (round(pos[0] / 0.3), round(pos[2] / 0.3)) if pos else None
    flags = tuple(sorted((k, str(v)) for k, v in (st.get('flags') or {}).items()))
    return (cell, st.get('area'), st.get('prompt'), st.get('event'), st.get('loading'), flags, st.get('life'))


@dataclass
class Config:
    window_s: float = 10.0
    steps: int = 3
    gain_m: float = 1.5
    roam_m: float = 4.0
    side_m: float = 2.5
    back_m: float = 2.0
    angle_deg: float = 40.0
    cap: int = 3             # identical moves in a row without ground gained: recovery, not another call
    cap_other: int = 6       # identical answers that are not moves (a press, a wait) with nothing changed: the target is left
    reuse_max: int = 2       # an unchanged state is answered from the last answer this often, then asked again
    replans_max: int = 12


class Recovery:
    def __init__(self, bad=None, cfg=None):
        self.bad = bad
        self.cfg = cfg or Config()

    def new_state(self):
        c = self.cfg
        return RecState(progress=Progress(c.gain_m, c.roam_m, c.window_s, c.steps))

    # ---- the ladder

    def available(self, rs, ctx):
        """The rungs not yet tried in this block that can be tried here, in order (a rung learned for this spot first)."""
        names = list(LADDER)
        if rs.learned in names:
            names.remove(rs.learned)
            names.insert(0, rs.learned)
        out = []
        for n in names:
            if n in rs.tried:
                continue
            if n == 'hop' and (not ctx.get('hop_ok', True) or ctx.get('camera') is None):
                continue
            if n == 'roll' and ctx.get('camera') is None:
                continue
            if n == 'next-point' and not self.next_lines(rs, ctx):
                continue
            out.append(n)
        if 'replan' not in out and 'replan' not in rs.tried:
            out.append('replan')
        return out

    @staticmethod
    def next_lines(rs, ctx):
        """The route points after the one that ended stuck: of the lines that were blocked, else of the plan from here."""
        b = rs.blocked or {}
        lines, idx = b.get('lines') or [], b.get('idx')
        if idx is not None and idx + 1 < len(lines) and all(parse_move(x) for x in lines[idx + 1:]):
            return list(lines[idx + 1:])
        go = ctx.get('go_lines') or []
        if len(go) >= 2 and all(parse_move(x) for x in go):
            return list(go[1:])
        return []

    def action(self, rs, ctx):
        """The next rung as an Action, marked tried. ctx: pos (x, z), camera, sign, target {x, z, radius}, go_lines (the plan
        to the target from here), hop_ok."""
        cfg = self.cfg
        name = (self.available(rs, ctx) or ['replan'])[0]
        rs.tried.append(name)
        px, pz = ctx['pos']
        tx, tz = ctx['target']['x'], ctx['target']['z']
        radius = ctx['target'].get('radius', 1.0)
        d = math.hypot(tx - px, tz - pz)
        theta = yaw_to(tx - px, tz - pz) if d > 1e-3 else 0.0
        go = list(ctx.get('go_lines') or [move_line('run', tx, tz, radius, run_timeout(d))])
        at = (round(px, 2), round(pz, 2))
        cam, sign, side = ctx.get('camera'), ctx.get('sign', 1.0), rs.side
        rx, rz = (rs.blocked or {}).get('pos') or (px, pz)   # the sidestep and the angle are measured from where the block was
        dref = math.hypot(tx - rx, tz - rz)
        tref = yaw_to(tx - rx, tz - rz) if dref > 1e-3 else theta
        if name == 'sidestep':
            hx, hz = heading(tref + 90.0 * side)
            lines = [move_line('walk', rx + cfg.side_m * hx, rz + cfg.side_m * hz, 0.6, 4)] + go
            text = f"step {cfg.side_m:g} m to the {'left' if side > 0 else 'right'} of the way to the target, then on to it"
        elif name == 'angle':
            bx, bz = heading(tref + 180.0)
            back = (rx + cfg.back_m * bx, rz + cfg.back_m * bz)
            length = max(4.0, min(10.0, dref * 0.5))
            hx, hz = heading(tref - side * cfg.angle_deg)   # the other side from the sidestep
            via = (back[0] + length * hx, back[1] + length * hz)
            lines = [move_line('walk', back[0], back[1], 0.4, 4), move_line('walk', via[0], via[1], 1.5, 6)] + go
            text = (f"back up {cfg.back_m:g} m from the block and come at it {cfg.angle_deg:g} degrees to the "
                    f"{'right' if side > 0 else 'left'}, then on to the target")
        elif name == 'roll':
            lines = [f'roll {dir_name(theta, cam, sign)}', 'wait 0.4'] + go
            text = 'roll toward the target, then on to it'
        elif name == 'hop':
            lx, ly = stick_for(theta, cam, sign)
            lines = [f'raw 0:LX={lx}+LY={ly}/800,0.15:X/100 1.2', 'wait 0.3'] + go
            text = 'jump (X, no sword) toward the target, then on to it'
        elif name == 'next-point':
            lines = self.next_lines(rs, ctx)
            text = 'skip the route point that ended stuck and go to the next'
        else:
            return Action('recover:replan', [], 'plan again from here, mark the point bad, leave the target', 'replan', 'replan', at)
        return Action(f'recover:{name}', lines, text, name, 'lines', at)

    def close_in(self, ctx):
        """A reach target's stand-off point was reached short of its radius: walk onto the target itself."""
        t = ctx['target']
        px, pz = ctx['pos']
        d = math.hypot(t['x'] - px, t['z'] - pz)
        return Action('recover:close-in', [move_line('walk', t['x'], t['z'], max(0.5, 0.7 * t.get('radius', 1.0)), run_timeout(d))],
                      f'walk the last {d:.1f} m onto the target itself (the planned way stopped short of it)', 'close-in', 'lines',
                      (round(px, 2), round(pz, 2)))

    # ---- what a step's result teaches

    def observe(self, rs, area, target, label, lines, results, result, moved, left, st, run=None):
        """After a step: update the monitor and the ladder's book-keeping. Returns the events to log: dicts with `status`
        blocked | recovered | no-ground, `rung`, `at`, `s` (the block's length in game seconds), `why`."""
        events = []
        pos, t = st.get('pos'), st.get('t')
        if not pos:
            return events
        xz = (pos[0], pos[2])
        dist = math.hypot(target.x - xz[0], target.z - xz[1])
        travel = is_travel(label)
        if label in ('go', 'go-near'):
            rs.last_go = {'lines': list(lines)}
        if label == 'recover:close-in':
            rs.closed_in = True
        ladder = bool(rs.blocked) and label.startswith(('recover:', 'vision-')) and label != 'recover:close-in'
        rs.progress.observe(t, xz, dist, result, moved, left, target.radius, travel, ladder)
        v = rs.verdict
        if rs.blocked and rs.progress.gained:
            events.append(self.recovered(rs, area, st, run, target.name))
        if v['stuck'] and rs.blocked is None:
            events.append(self.start_block(rs, area, xz, t, label, lines, results, target.name, v['why']))
        if rs.progress.gained:
            rs.streak, rs.streak_key = 0, None
        return events

    def start_block(self, rs, area, xz, t, label, lines, results, target_name, why):
        idx = None
        for i, r in enumerate(results):
            if r and r.get('ev') == 'step' and r['src'].split()[0] in ('walk', 'run') and r.get('result') in ('stuck', 'timeout'):
                idx = i
                break
        rs.blocked = {'pos': (round(xz[0], 2), round(xz[1], 2)), 't': t, 'label': label, 'lines': list(lines), 'idx': idx}
        rs.tried, rs.learned, rs.vision_used = [], None, False
        if self.bad is not None:
            e = self.bad.near(area, xz[0], xz[1], margin=1.0)
            fix = (e or {}).get('fix') or ''
            if fix.startswith('sidestep-'):
                rs.learned, rs.side = 'sidestep', (1 if fix.endswith('left') else -1)
            elif fix in LADDER:
                rs.learned = fix
        rs.episode = {'target': target_name, 'at': list(rs.blocked['pos']), 't0': t, 't1': None, 's': None, 'why': why, 'rungs': [],
                      'how': None}
        rs.episodes.append(rs.episode)
        return {'status': 'blocked', 'at': list(rs.blocked['pos']), 'why': why, 'learned': rs.learned}

    def recovered(self, rs, area, st, run, target_name):
        """Ground was gained after a block: the rung that did it (the last tried) is kept for the spot as a `soft` point."""
        b, t = rs.blocked, st.get('t')
        rung = rs.tried[-1] if rs.tried else None
        fix = (f"sidestep-{'left' if rs.side > 0 else 'right'}" if rung == 'sidestep' else rung)
        if self.bad is not None and fix:
            self.bad.add(area, b['pos'][0], b['pos'][1], 'soft', why=f"blocked by {b['label']}", run=run, target=target_name, fix=fix)
        self.close_episode(rs, t, 'recovered')
        ev = {'status': 'recovered', 'rung': rung, 'at': list(b['pos']), 's': rs.episodes[-1]['s'] if rs.episodes else None}
        rs.blocked, rs.tried, rs.learned = None, [], None
        return ev

    def marked_bad(self, rs, area, st, run, target_name, why='no rung got past it', dist=None):
        """The ladder is spent: the spot is marked `bad` and the block ends."""
        b = rs.blocked or {'pos': (st['pos'][0], st['pos'][2]), 'label': ''}
        if self.bad is not None:
            self.bad.add(area, b['pos'][0], b['pos'][1], 'bad', why=why, run=run, target=target_name)
        self.close_episode(rs, st.get('t'), 'marked-bad')
        ev = {'status': 'marked-bad', 'at': list(b['pos']), 'rungs': list(rs.tried), 's': rs.episodes[-1]['s'] if rs.episodes else None}
        rs.blocked, rs.tried, rs.learned = None, [], None
        rs.progress.anchor(st.get('t'), (st['pos'][0], st['pos'][2]), dist)
        return ev

    def close_episode(self, rs, t, how):
        e = rs.episode
        if e and e.get('how') is None:
            e['t1'], e['how'] = t, how
            e['s'] = round((t - e['t0']), 2) if t is not None and e.get('t0') is not None else None
            e['rungs'] = list(rs.tried)
        rs.episode = None

    # ---- the guard in front of the decider

    def gate(self, rs, cands, target, st, mem, dist=None):
        """(cands, forced): the candidates without a move that already ended stuck from here, and what to do instead of asking:
        None (ask as usual), ('recover', why) (the ladder acts), ('close-in', why), or ('choice', label, why) (no question)."""
        verdict = rs.verdict
        if verdict['stuck']:
            return cands, ('recover', verdict['why'])
        if rs.streak >= self.cfg.cap and rs.last_label in NO_REPEAT:
            return cands, ('recover', 'identical answers')
        if rs.streak >= self.cfg.cap_other and rs.last_label not in NO_REPEAT and rs.last_key and rs.last_key[1] == signature(st):
            leave = next((c.label for c in cands if c.label in ('next-target', 'give-up-goal')), None)
            if leave:   # the same thing again and again with nothing changing in the game: leave the target
                return cands, ('choice', leave, 'identical answers')
        if verdict['short'] and not rs.closed_in and target.kind in ('reach', 'item') and not target.follow:
            return cands, ('close-in', 'short')
        pos = st.get('pos')
        tried_here = mem.get('blocked_lines') or []
        if pos:
            kept = [c for c in cands if not (c.label in NO_REPEAT and any(
                math.hypot(pos[0] - b[0][0], pos[2] - b[0][1]) <= 1.5 and list(c.lines) == b[1] for b in tried_here))]
            cands = kept or cands
        if dist is not None and dist <= target.radius + ARRIVE_SLACK_M and target.kind in ('reach', 'item') and not target.follow:
            if mem.get('last'):
                cands = [c for c in cands if c.label not in ('go', 'go-near', 'back-off', 'side-left', 'side-right')] or cands
            else:
                cands = [c for c in cands if c.label in ('talk-through', 'next-target')] or cands
        if len(cands) == 1:
            return cands, ('choice', cands[0].label, 'one valid candidate')
        key = (tuple(c.label for c in cands), signature(st))
        if rs.last_key == key and rs.last_label is not None:
            if is_travel(rs.last_label):
                return cands, ('recover', 'unchanged')
            if rs.last_ask and rs.reuse < self.cfg.reuse_max and rs.last_label in [c.label for c in cands]:
                return cands, ('choice', rs.last_label, 'state unchanged')
        return cands, None

    def note_answer(self, rs, label, lines, st, cands, asked):
        """After a decision: the streak of identical answers (same label and lines, no ground gained between) and the key of
        this decision."""
        same = (label, tuple(lines)) == rs.streak_key
        rs.streak = rs.streak + 1 if same else 1
        rs.streak_key = (label, tuple(lines))
        key = (tuple(c.label for c in cands), signature(st))
        again = rs.last_key == key
        rs.reuse = rs.reuse + 1 if (again and not asked) else 0
        rs.last_ask = asked or (again and rs.last_ask)   # (a reuse is still an answer that came from a question)
        rs.last_key, rs.last_label = key, label
