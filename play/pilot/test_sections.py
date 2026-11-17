#!/usr/bin/env python3
"""Chapters without a game and without a network: the chapter file, the progress markers (flag, map, rupees, region), the world digest over an invented
world, the planner's request and the plans it may return (a stub model), and the whole chapter run by the real pilot against a thread that speaks hsap's
steps in a made-up two-area world with a gap and a door: the plan, the objectives, a failure and the replan after it, the fall back to the chapter's own
outline, the budgets, the timeline and the picture-based wait for the day. Nothing here is game data.

    python3 -I -B test_sections.py        (test_pilot.py calls run() too)
"""
import argparse
import json
import math
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import deciders  # noqa: E402
import hsap  # noqa: E402
import interact  # noqa: E402
import openai_api  # noqa: E402
import pilot  # noqa: E402
import sections  # noqa: E402
import vision  # noqa: E402

FAILS, CHECKS = [], [0]


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILS.append(what)
        print('FAIL', what, flush=True)


CHAPTER = '''
name = "made-up"
aim = "open the chest, cross the gap, go through the door and talk to the friend"
area = "Fake_A"
save = "nothing"
boot = "continue"
wall = 300
budget_seconds = 900
budget_usd = 1.0
max_objectives = 12
max_replans = 4
per_plan = 4
look = false
watch = ["Gap_Crossed"]
hints = ["the door is east of the gap"]

[[progress]]
name = "chest"
what = "the chest is open"
flag = "Chest_Open=1"
[[progress]]
name = "beyond"
what = "Link is in the second area"
area = "Fake_B"
[[progress]]
name = "friend"
what = "the friend was talked to"
flag = "Talked=1"

[[objective]]
name = "seed-chest"
kind = "open_chest"
what = "open the chest"
progress = "chest"
flag = "Chest_Open=1"
targets = [{name = "chest", at = [10.0, 5.0], y = 0.0, kind = "chest", radius = 1.2}]
[[objective]]
name = "seed-cross"
kind = "go_to"
what = "cross the gap to the far bank"
targets = [{name = "bank", at = [26.0, 0.0], y = 0.0, radius = 1.5}]
[[objective]]
name = "seed-door"
kind = "enter"
what = "go through the door"
to = "Fake_B"
progress = "beyond"
targets = [{name = "door", at = [40.0, 0.0], y = 0.0, kind = "door", radius = 2.0}]
[[objective]]
name = "seed-friend"
kind = "talk_to"
what = "talk to the friend"
progress = "friend"
flag = "Talked=1"
targets = [{name = "friend", at = [6.0, 3.0], y = 0.0, kind = "talk", radius = 1.5}]
'''

WORLD = {
    'Fake_A': [
        {'kind': 'chest', 'gyaml': 'Chest_Made', 'pos': [10.0, 0.0, 5.0], 'fixed': True, 'rot': [0.0, 1.5708, 0.0]},
        {'kind': 'exit', 'gyaml': 'Exit_Made', 'name': 'ToSecond', 'pos': [40.0, 0.0, 0.0], 'fixed': True},
        {'kind': 'exit', 'gyaml': 'Exit_Made', 'name': 'ToNowhere', 'pos': [-40.0, 0.0, 0.0], 'fixed': True},
        {'kind': 'person', 'gyaml': 'Person_Made', 'pos': [15.0, 0.0, 0.0], 'fixed': False, 'moves': ['wanders up to 2.0 m (MoveRadius)']},
        {'kind': 'door', 'gyaml': 'Door_Made', 'pos': [30.0, 0.0, 2.0], 'fixed': True},
        {'kind': 'chest', 'gyaml': 'Chest_Made', 'pos': [10.1, 0.0, 5.0], 'fixed': True},   # (a duplicate of the first, 0.1 m away)
        {'kind': 'chest', 'gyaml': 'Chest_Far', 'pos': [900.0, 0.0, 900.0], 'fixed': True},
        {'kind': 'climbable', 'gyaml': 'Ivy_Made', 'pos': [5.0, 3.0, -4.0], 'fixed': True},
        {'kind': 'enemy', 'gyaml': 'Enemy_Made', 'pos': [20.0, 0.0, 6.0], 'fixed': False},
        {'kind': 'enemy', 'gyaml': 'Enemy_Made', 'pos': [22.0, 0.0, 6.0], 'fixed': False},
        {'kind': 'blocker', 'gyaml': 'Wall_Made', 'pos': [1.0, 0.0, 1.0], 'fixed': True},
    ],
    'Fake_B': [{'kind': 'person', 'gyaml': 'Friend_Made', 'pos': [6.0, 0.0, 3.0], 'fixed': True}],
}
LINKS = [{'from': 'Fake_A', 'name': 'ToSecond', 'to_child': 'Fake_B', 'to_adult': 'Fake_B_Adult'},
         {'from': 'Fake_A', 'name': 'ToNowhere', 'to_child': 'Fake_C', 'to_adult': 'Fake_C_Adult'}]


class WithWorld:
    def __enter__(self):
        self.old = (interact._world.get('all'), sections._links.get('all'))
        interact._world['all'], sections._links['all'] = WORLD, LINKS
        return self

    def __exit__(self, *exc):
        for d, v in ((interact._world, self.old[0]), (sections._links, self.old[1])):
            d.pop('all', None) if v is None else d.__setitem__('all', v)


def state(**kw):
    st = {'pos': [0.0, 0.0, 0.0], 'facing': 0.0, 'camera': 0.0, 'event': 0, 'loading': 0, 'prompt': 'Empty', 'music': 'BGM_Test', 'life': 12, 'max_life': 12,
          'rupees': 0, 'area': 'Fake_A', 't': 10.0, 'flags': {}}
    st.update(kw)
    return st


# ---- the chapter file and the markers

def file_checks(tmp):
    path = Path(tmp) / 'ch.toml'
    path.write_text(CHAPTER)
    ch = sections.load(path)
    check(ch.name == 'made-up' and len(ch.marker_specs) == 3 and len(ch.seeds) == 4 and ch.budget_s == 900 and ch.per_plan == 4 and not ch.look,
          'a chapter loads: aim, markers, the outline, budgets')
    check(sections.load_if_chapter(path) is not None and sections.load_if_chapter(Path(tmp) / 'nope.toml') is None, 'load_if_chapter: a chapter, or None for a missing file')
    seg_text = Path(tmp) / 'seg.toml'
    seg_text.write_text('name = "s"\narea = "A"\n[[goal]]\nname = "g"\nflag = "X=1"\nkind = "reach"\ntargets = [{name = "t", at = [0.0, 0.0]}]\n')
    check(sections.load_if_chapter(seg_text) is None, 'an ordinary segment is not a chapter')
    Path(tmp, 'watch.json').write_text(json.dumps(['Standard.One', 'Standard.Two']))
    os.environ['PILOT_WATCH'] = str(Path(tmp, 'watch.json'))
    try:
        seg = sections.segment_for(ch)
    finally:
        del os.environ['PILOT_WATCH']
    check('Chest_Open' in seg.watch and 'Talked' in seg.watch and 'Gap_Crossed' in seg.watch and 'Standard.Two' in seg.watch and len(seg.watch) <= 64,
          f'the segment watches the markers\' flags, the chapter\'s and the standard ones from the private file ({len(seg.watch)} flags)')
    check(sections.standard_watch() == [] or Path(sections.HERE / 'chapters' / 'watch.json').exists(), 'without the private file there is no standard watch')
    check([g.name for g in seg.goals] == ['chest', 'beyond', 'friend'] and seg.goals[0].cond.met({'Chest_Open': 1}) and not seg.goals[1].cond.met({'Chest_Open': 1}),
          'the markers are pseudo-goals whose condition reads flags (for the exported route and the summary)')
    for text, what in (('name = "x"\narea = "A"\n', 'no aim'), ('name = "x"\naim = "y"\narea = "A"\n', 'no marker'),
                       ('name = "x"\naim = "y"\narea = "A"\n[[progress]]\nname = "m"\nflag = "A=1"\n[[objective]]\nkind = "teleport"\nwhat = "z"\n', 'a seed of a kind that does not exist'),
                       ('name = "x"\naim = "y"\narea = "A"\n[[progress]]\nname = "m"\nflag = "A=1"\n[[objective]]\nkind = "open_chest"\nwhat = "z"\n', 'a seed with no target'),
                       ('name = "x"\naim = "y"\narea = "A"\nregion = {area = "A"}\n[[progress]]\nname = "m"\nflag = "A=1"\n', 'a region without a centre')):
        bad = Path(tmp) / 'bad.toml'
        bad.write_text(text)
        try:
            sections.load(bad)
            check(False, f'{what} is refused')
        except ValueError:
            check(True, f'{what} is refused')


def marker_checks():
    m = sections.Marker({'name': 'm', 'flag': 'A=1', 'area': 'X', 'rupees': 10})
    check(not m.check(state(flags={'A': 1}, area='Y', rupees=20)) and not m.check(state(flags={'A': 1}, area='X', rupees=5)) and
          not m.check(state(flags={'A': 1}, area='X', rupees=20, loading=1)) and m.check(state(flags={'A': 1}, area='X', rupees=20, t=33.0)),
          'a marker with a flag, a map and rupees needs all three, and not while a load runs')
    check(m.met_at == 33.0 and m.met_area == 'X' and m.check(state()), 'it keeps when it was met')
    g = sections.Marker({'name': 'g', 'rupees_gain': 10})
    check(not g.check(state(rupees=50)) and not g.check(state(rupees=55)) and g.check(state(rupees=60)), 'rupees_gain counts from the first state')
    n = sections.Marker({'name': 'n', 'area': 'Fake_A', 'near': [10.0, 10.0, 3.0]})
    check(not n.check(state(pos=[0.0, 0.0, 0.0])) and n.check(state(pos=[11.0, 0.0, 9.0])), 'near: within the radius of the place, in the map')
    y = sections.Marker({'name': 'y', 'area': 'Fake_A', 'y_below': 90.0})
    check(not y.check(state(pos=[0.0, 100.0, 0.0])) and y.check(state(pos=[0.0, 80.0, 0.0])), 'y_below: Link\'s height under a line')
    cg = sections.Marker({'name': 'cg', 'count_flag': 'Pouch.Heart.Stock', 'count_gain': 1})
    check(not cg.check(state(flags={'Pouch.Heart.Stock': 2})) and not cg.check(state(flags={'Pouch.Heart.Stock': 2})) and cg.check(state(flags={'Pouch.Heart.Stock': 3}))
          and 'Pouch.Heart.Stock' in cg.watch, 'count_gain: a watched counter that rose since it was first seen (and is watched)')
    check(sections.Marker({'name': 'x'}).text == 'never' and not sections.Marker({'name': 'x'}).check(state()), 'a marker that states nothing is never met')
    cov = sections.Coverage('Fake_A', (0.0, 0.0), 30.0, 10.0)
    c = sections.Marker({'name': 'c', 'coverage': 0.25}, cov)
    n0 = len(cov.cells)
    for x in range(-30, 31, 5):
        cov.add(state(pos=[float(x), 0.0, 0.0]))
        cov.add(state(pos=[float(x), 0.0, 0.0], area='Other'))
    check(0 < cov.fraction() < 1 and len(cov.seen) <= 7 and not c.check(state()), f'coverage counts the cells a run was in, in its own map only ({len(cov.seen)} of {n0})')
    for x in range(-30, 31, 10):
        for z in range(-30, 31, 10):
            cov.add(state(pos=[float(x), 0.0, float(z)]))
    check(cov.fraction() > 0.9 and c.check(state()) and len(cov.unvisited([0.0, 0.0, 0.0])) <= 4, f'after crossing the region the marker is met ({cov.fraction():.2f})')
    first = sections.Coverage('A', (0.0, 0.0), 30.0, 10.0).unvisited([25.0, 0.0, 0.0], 3)
    check(len(first) == 3 and first[0]['dist'] <= first[1]['dist'] <= first[2]['dist'], 'unvisited cells come nearest first')


# ---- the world digest and the plans

def digest_checks():
    with WithWorld():
        def plan(area, a, b, **kw):
            return {'ok': b[0] < 25, 'length': abs(b[0] - a[0]), 'why': None if b[0] < 25 else 'needs-jump',
                    'needed': [] if b[0] < 25 else [{'kind': 'jump', 'allowed': False}, {'kind': 'walk-ok', 'allowed': True}]}
        d = sections.world_digest('Fake_A', state(pos=[0.0, 0.0, 0.0]), plan=plan)
        check(d['area'] == 'Fake_A' and d['age'] == 'child' and [e['ref'] for e in d['exits']] == ['exit:1', 'exit:2'], f'exits are listed, all of them ({[e["ref"] for e in d["exits"]]})')
        check(d['exits'][0]['name'] == 'ToSecond' and d['exits'][0]['to'] == 'Fake_B' and d['exits'][1]['to'] == 'Fake_C', 'an exit names the map it leads to (the age\'s)')
        check(len(d['chests']) == 1 and d['chests'][0]['ref'] == 'chest:1' and d['chests'][0]['dist'] == 11.2, 'a duplicate placement and a chest 900 m away are left out')
        front = d['chests'][0]['front']
        check(abs(front[0] - (10.0 + 0.95)) < 0.02 and abs(front[1] - 5.0) < 0.05, f'a chest\'s front is 0.95 m on its lid side ({front})')
        check(d['people'][0]['ref'] == 'person:1' and d['exits'][0]['way'] == {'ok': False, 'm': 40.0, 'why': 'needs-jump', 'needs': ['jump']}
              and d['chests'][0]['way']['ok'] is True, f'the offline planner\'s word on the way is there ({d["exits"][0]["way"]})')
        check(d['climbables'][0]['what'] == 'Ivy_Made' and d['enemies'] == [{'what': 'Enemy_Made', 'count': 2, 'nearest': 20.9}] and 'blockers' not in d,
              f'climbable things and enemies by kind are summarised, blockers are not ({d.get("enemies")})')
        refs = sections.digest_refs(d)
        check(refs['exit:1']['x'] == 40.0 and 'chest:1' in refs and len(refs) == len([1 for v in d.values() if isinstance(v, list) for _ in v if isinstance(_, dict) and _.get('ref')]),
              'digest_refs finds each entry by its ref')
        check(sections.world_digest('Nowhere', state())['area'] == 'Nowhere' and len(sections.world_digest('Nowhere', state())) == 2, 'an area the model does not know has an empty digest')
        d2 = sections.world_digest('Fake_A', state(pos=[0.0, 0.0, 0.0]), plan=None)
        check('way' not in d2['exits'][0], 'without a planner there is no way column')


class StubClient:
    """The model's side: replies are dicts (or functions of the request's data) in the plan's own shape, served as a Responses answer."""
    real = False

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def post(self, path, body, *, api, model, kind, estimate, timeout=30.0):
        self.requests.append(body)
        data = json.loads(body['input'][1]['content'][0]['text'])
        reply = self.replies.pop(0) if self.replies else {'reason': 'nothing more', 'objectives': []}
        obj = reply(data) if callable(reply) else reply
        ans = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(obj)}]}]}
        return ans, 12.0, {'usage': {'input_tokens': 5000, 'output_tokens': 400}, 'cost_usd': 0.01}


def obj(kind, what, **kw):
    base = {'id': kind, 'kind': kind, 'what': what, 'target_ref': '', 'x': None, 'z': None, 'y': None, 'radius': 0.0, 'flag': '', 'to': '', 'count': 0, 'price': 0, 'song': '',
            'count_flag': '', 'direction': '', 'how': 'auto', 'until': '', 'meters': 0.0, 'max_seconds': 0, 'progress': ''}
    base.update(kw)
    return base


def plan_checks(tmp):
    path = Path(tmp) / 'ch.toml'
    path.write_text(CHAPTER)
    ch = sections.load(path)
    with WithWorld():
        digest = sections.world_digest('Fake_A', state(), plan=None)
        watched = {'Chest_Open', 'Talked'}
        reply = {'reason': 'chest first', 'objectives': [
            obj('open_chest', 'open the chest', target_ref='chest:1', flag='Chest_Open=1', progress='chest', max_seconds=60),
            obj('enter', 'through the door', target_ref='exit:1', progress='beyond'),
            obj('go_to', 'to a place named by coordinates', x=26.0, z=0.0, y=0.0, radius=2.0, how='jump'),
            obj('talk_to', 'a ref that is not there', target_ref='person:9'),
            obj('open_chest', 'a flag nobody watches', target_ref='chest:1', flag='Unwatched=1'),
            obj('wait_time', 'wait for the day', until='day'),
            obj('go_to', 'the fifth valid one is cut', x=1.0, z=1.0)]}
        objs, rejected, why = sections.parse_plan(json.dumps(reply), ch, digest, watched, 3)
        check([o.kind for o in objs] == ['open_chest', 'enter', 'go_to', 'open_chest'] and why == 'chest first', f'a plan is parsed, cut to per_plan ({[o.kind for o in objs]})')
        check(len(rejected) == 1 and 'person:9' in rejected[0][1] and rejected[0][0].startswith('p3-4'), f'an objective whose ref is not in the digest is rejected with the reason ({rejected})')
        a, b, c, d = objs
        check(a.id.startswith('p3-1-') and a.targets[0]['at'] == [10.0, 5.0] and a.targets[0]['front'] == digest['chests'][0]['front'] and a.targets[0]['kind'] == 'chest'
              and a.flag == 'Chest_Open=1' and a.max_s == 60 and a.progress == 'chest', 'a chest objective takes its place and front from the digest, its flag, its time')
        check(b.params.get('to') == 'Fake_B' and b.targets[0]['kind'] == 'door' and b.max_s == sections.DEFAULT_SECONDS['enter'], 'an enter objective takes the destination map from the exit')
        check(c.targets[0]['at'] == [26.0, 0.0] and c.targets[0]['y'] == 0.0 and c.targets[0]['radius'] == 2.0 and c.params['how'] == 'jump', 'a place by coordinates, a radius and how')
        check(d.flag == '', 'a flag that is not watched is dropped: it could never be seen')
        bare = sections.parse_plan(json.dumps({'reason': '', 'objectives': [obj('open_chest', 'bare flag', target_ref='chest:1', flag='Chest_Open'),
                                                                           obj('open_chest', 'compared flag', target_ref='chest:1', flag='Talked>=1')]}), ch, digest, watched, 1)[0]
        check([o.flag for o in bare] == ['Chest_Open=1', 'Talked>=1'], f'a bare flag name asks for it to be set ({[o.flag for o in bare]})')
        t = sections.goal_table(a)
        check(t['kind'] == 'open_chest' and t['flag'] == 'Chest_Open=1' and t['targets'][0]['front'] == a.targets[0]['front'], 'the goal table is the segment\'s own')
        gt = sections.goal_table(c)
        check(gt['kind'] == 'cross' and gt['how'] == 'jump', 'go_to is the cross goal')
        seg = pilot.Segment(path, spec={'name': 'x', 'what': 'w', 'area': 'Fake_A', 'goal': [t, gt]})
        check([g.kind for g in seg.goals] == ['open_chest', 'cross'] and all(hasattr(g.cond, 'met_state') for g in seg.goals), 'pilot.Segment plays objectives from a dict')
        w = sections.objective_from_dict({'kind': 'wait_time', 'what': 'wait', 'until': 'night'}, ch, 'seed')
        check(w.targets[0].get('here') and sections.goal_table(w, [3.0, 4.0, 5.0])['targets'][0]['at'] == [3.0, 5.0], 'a target that is where Link stands (here) is made at the start of the objective')
        # the request and its schema
        planner = sections.ChapterPlanner(StubClient([reply]), 'gpt-6-astra')
        body = planner.body({'aim': 'x'}, image='data:image/jpeg;base64,AAAA')
        user = body['input'][1]['content']
        check(body['text']['format']['strict'] is True and body['model'] == 'gpt-6-astra' and user[1]['type'] == 'input_image' and body['input'][0]['role'] == 'developer',
              'the request: a strict schema, the big model, a developer text, the data and a picture')
        sch = sections.plan_schema()['properties']['objectives']['items']
        check(set(sch['required']) == set(sch['properties']) and sch['properties']['kind']['enum'] == list(sections.MODEL_KINDS), 'strict: every property is required, kinds are the pilot\'s')
        text, info = planner.ask({'aim': 'x'})
        check(json.loads(text)['reason'] == 'chest first' and info['cost_usd'] == 0.01 and info['model'] == 'gpt-6-astra', 'ask returns the text and what it cost')
        check(set(sections.MODEL_KINDS) - {'wait_time', 'go_to'} <= set(interact.GOAL_KINDS), 'every objective kind the model may name is a goal kind the pilot plays')


# ---- the time of day

def daylight_checks(tmp):
    dark = Path(tmp) / 'dark.png'
    bright = Path(tmp) / 'bright.png'
    dark.write_bytes(vision.write_png(40, 20, bytes([8, 10, 30] * 800)))
    bright.write_bytes(vision.write_png(40, 20, bytes([200, 210, 230] * 800)))
    a, b = sections.daylight_of(dark), sections.daylight_of(bright)
    check(a is not None and b is not None and a < 0.16 and b > 0.5, f'the brightness of the sky band: dark {a}, bright {b}')
    check(sections.day_class(a) == 'night' and sections.day_class(b) == 'day' and sections.day_class(0.2) == 'between' and sections.day_class(None) is None, 'night, day, between, unknown')
    half = Path(tmp) / 'half.png'
    rows = bytes([240, 240, 240] * 40 * 8) + bytes([0, 0, 0] * 40 * 12)
    half.write_bytes(vision.write_png(40, 20, rows))
    check(sections.daylight_of(half) > 0.5, 'only the upper 40 percent counts (the ground and the HUD are below)')


# ---- the chapter played by the real pilot against a made-up game

class WorldGame(threading.Thread):
    """hsap's other end for a made-up world. Area Fake_A: a chest at (10, 5) (prompt Open within 1.5 m), a gap between x = 20 and x = 21 that a walk cannot cross (it
    ends stuck at 19.6) and a jump from within 1.5 m of its edge does, a door at (40, 0) (enter goes to Fake_B), a person nobody asks about. Area Fake_B: a friend at (6, 3)."""

    def __init__(self, live, hard_chest=False):
        super().__init__(daemon=True)
        self.live, self.pos, self.area, self.facing = Path(live), [0.0, 0.0], 'Fake_A', 0.0
        self.flags, self.watch, self.n, self.tick, self.stop, self.log = {'Chest_Open': 0, 'Talked': 0, 'Gap_Crossed': 0}, [], 0, 0, False, []
        self.rupees, self.hard_chest, self.chest_open = 0, hard_chest, False

    def emit(self, **e):
        with open(self.live / 'out.jsonl', 'a') as f:
            f.write(json.dumps(e) + '\n')

    def prompt(self):
        if self.area == 'Fake_A' and not self.chest_open and not self.hard_chest and math.hypot(self.pos[0] - 10.0, self.pos[1] - 5.0) <= 1.5:
            return 'Open'
        if self.area == 'Fake_B' and not self.flags['Talked'] and math.hypot(self.pos[0] - 6.0, self.pos[1] - 3.0) <= 1.7:
            return 'Talk'
        return 'Empty'

    def state(self, last):
        self.tick += 180
        self.emit(ev='state', why='wait', n=self.tick, t=self.tick / 60, pos=[self.pos[0], 0.0, self.pos[1]], facing=self.facing, camera=0.0, area=self.area, prompt=self.prompt(),
                  music='BGM_Test', scene=False, life=12, max_life=12, rupees=self.rupees, event=0, loading=0, last=last,
                  flags={k: self.flags.get(k) for k in self.watch}, changed=[])

    def step(self, src):
        w = src.split()
        result, extra = 'done', {}
        if w[0] in ('walk', 'run', 'pickup'):
            x, z = float(w[1]), float(w[2])
            if self.area == 'Fake_A' and self.pos[0] < 20.0 <= x:
                f = (19.6 - self.pos[0]) / (x - self.pos[0])   # (the way as far as the gap's edge)
                self.pos = [19.6, self.pos[1] + (z - self.pos[1]) * f]
                result = 'stuck'
            else:
                self.pos, result = [x, z], 'reached'
        elif w[0] == 'faceat':
            self.facing = math.degrees(math.atan2(float(w[1]) - self.pos[0], float(w[2]) - self.pos[1]))
        elif w[0] == 'jump':
            x, z = float(w[1]), float(w[2])
            if self.area == 'Fake_A' and abs(self.pos[0] - 19.6) <= 1.5 and x >= 21.0:
                self.pos, result = [x, z], 'landed'
                self.flags['Gap_Crossed'] = 1
            else:
                result = 'none'
        elif w[0] == 'chest':
            if self.prompt() == 'Open':
                self.chest_open, self.flags['Chest_Open'], self.rupees, result = True, 1, self.rupees + 20, 'opened'
                extra = {'changes': {'rupees': [0, 20], 'text': 'closed', 'flags': {'Chest_Open': [0, 1]}}}
            else:
                result = 'no-prompt'
        elif w[0] == 'talk':
            if self.prompt() == 'Talk':
                self.flags['Talked'], result, extra = 1, 'talked', {'changes': {'text': 'closed'}}
            else:
                result = 'no-prompt'
        elif w[0] == 'enter':
            if self.area == 'Fake_A' and math.hypot(self.pos[0] - 40.0, self.pos[1]) <= 3.0:
                self.area, self.pos, result = 'Fake_B', [0.0, 0.0], 'entered'
            else:
                result = 'blocked'
        elif w[0] == 'advance':
            result = 'none'
        elif w[0] == 'until':
            name, _, val = w[2].partition('=')
            result = 'met' if str(self.flags.get(name)) == val else 'timeout'
        self.n += 1
        self.log.append((src, result))
        return {'ev': 'step', 'n': self.n, 'in': self.lines, 'src': src, 'result': result, 'secs': 1.0, 'moved': 0.0, 'pos': list(self.pos), 'detail': '', **extra}

    def run(self):
        self.lines, off, buf = 0, 0, ''
        self.emit(ev='start', tick=0)
        self.emit(ev='gameplay', tick=1, pos=list(self.pos))
        waiting = False
        while not self.stop:
            data = (self.live / 'steps.txt').read_text()[off:]
            off += len(data)
            buf += data
            *whole, buf = buf.split('\n')
            last = None
            for line in whole:
                self.lines += 1
                w = line.split()
                if not w:
                    continue
                if w[0] == 'watch':
                    self.watch += w[1:]
                elif w[0] == 'end':
                    self.emit(ev='done', tick=self.tick)
                    return
                else:
                    e = self.step(line)
                    self.emit(**e)
                    last = {'src': e['src'], 'result': e['result'], 'secs': 1.0, 'moved': 0.0, 'detail': '', **({'changes': e['changes']} if 'changes' in e else {})}
                    waiting = False
            if not waiting and (last or not self.n):
                self.state(last)
                waiting = True
            time.sleep(0.005)


def start(tmp, chapter_text=CHAPTER, hard_chest=False, replies=(), budget_s=None, decider=None, no_planner=False):
    """A chapter pilot on a made-up game: (ChapterPilot, game, channel, first state, run dir)."""
    path = Path(tmp) / f'chapter-{time.time_ns()}.toml'
    path.write_text(chapter_text if budget_s is None else chapter_text.replace('budget_seconds = 900', f'budget_seconds = {budget_s}'))
    ch = sections.load(path)
    seg = sections.segment_for(ch)
    live = Path(tmp) / f'live-{time.time_ns()}'
    chan = pilot.Channel(live)
    chan.send(pilot.watch_lines(seg.watch))
    game = WorldGame(live, hard_chest=hard_chest)
    game.start()
    assert chan.wait(lambda e: e['ev'] == 'gameplay', 5)
    st = chan.wait(lambda e: e['ev'] == 'state', 5)
    run_dir = Path(tmp) / f'run-{time.time_ns()}'
    run_dir.mkdir()
    client = StubClient(list(replies))
    cp = sections.ChapterPilot(seg, decider or deciders.Rules(), chan, run_dir, run_dir.name, lambda: True, None, None, copy=None, client=client, args=argparse.Namespace(),
                               chapter=ch)
    cp.planner = None if no_planner else sections.ChapterPlanner(client, 'gpt-6-astra')
    return cp, game, chan, st, run_dir, client


def finish(game, chan):
    chan.send(['end'])
    chan.wait(lambda e: e['ev'] == 'done', 5)


def plan_one(data):
    """The model: a plan for what is not met yet, from the digest it was given (the chest, then the gap, then the door, then the friend)."""
    met = {m['name'] for m in data['markers'] if m['met']}
    link = data['link']
    if link['area'] == 'Fake_B':
        return {'reason': 'in the second area: the friend', 'objectives': [obj('talk_to', 'talk to the friend', x=6.0, z=3.0, y=0.0, radius=1.5, progress='friend', flag='Talked=1')]}
    chest = next(iter(data['digest'].get('chests') or [{'ref': 'chest:1'}]))
    exit1 = next(e for e in data['digest']['exits'] if e['to'] == 'Fake_B')
    out = []
    if 'chest' not in met:
        out.append(obj('open_chest', 'open the chest', target_ref=chest['ref'], flag='Chest_Open=1', progress='chest'))
    out.append(obj('go_to', 'cross the gap to the far bank', x=26.0, z=0.0, y=0.0, radius=1.5, how='jump'))
    out.append(obj('enter', 'go through the door', target_ref=exit1['ref'], progress='beyond'))
    return {'reason': 'the chest, the gap, the door', 'objectives': out}


def run_checks(tmp):
    with WithWorld():
        # 1. the model plans, the pilot plays every objective, the chapter is solved
        cp, game, chan, st, run_dir, client = start(tmp, replies=[plan_one, plan_one])
        outcome = cp.run(st)
        finish(game, chan)
        s = cp.write()
        check(outcome == 'solved' and all(m['met'] for m in s['markers']), f'the planned chapter is solved ({outcome}; markers {[(m["name"], m["met"]) for m in s["markers"]]})')
        check([o['kind'] for o in s['timeline']] == ['open_chest', 'go_to', 'enter', 'talk_to'] and all(o['goal_status'] == 'met' for o in s['timeline']),
              f'the timeline: four objectives, each met ({[(o["kind"], o["outcome"]) for o in s["timeline"]]})')
        check(game.chest_open and game.flags['Gap_Crossed'] == 1 and game.area == 'Fake_B' and game.flags['Talked'] == 1, 'in the made-up world: the chest open, the gap crossed, the door taken, the friend talked to')
        check(len(client.requests) == 2 and s['plans'][0]['why'] == 'start' and s['plans'][1]['why'] == 'queue empty' and s['plan_cost_usd'] == 0.02,
              f'two plans: the start and the queue running empty ({[p["why"] for p in s["plans"]]}), {s["plan_cost_usd"]} USD')
        second = json.loads(client.requests[1]['input'][1]['content'][0]['text'])
        check([o['kind'] for o in second['objectives_so_far']] == ['open_chest', 'go_to', 'enter'] and second['link']['area'] == 'Fake_B' and second['aim'].startswith('open the chest')
              and 'Chest_Open' in second['watched_flags'], 'the second request has the objectives so far, with their outcomes, and the state')
        check(second['markers'][0]['met'] and not second['markers'][2]['met'] and second['left']['game_seconds'] < 900 and second['left']['usd'] > 0.9, 'it says which markers are met and what is left of the budgets')
        check((run_dir / 'chapter.json').exists() and (run_dir / 'timeline.md').exists() and len((run_dir / 'plans.jsonl').read_text().splitlines()) == 2, 'chapter.json, timeline.md and plans.jsonl are written')
        check([g.status for g in cp.seg.goals] == ['met', 'met', 'met'], 'the markers\' pseudo-goals say met in the summary')
        md = (run_dir / 'timeline.md').read_text()
        check('# Chapter made-up: solved' in md and '| 1 |' in md and 'open the chest' in md and 'Where it got stuck' not in md, 'the timeline reads as a table with the outcome')
        tr = [json.loads(x) for x in (run_dir / 'transcript.jsonl').read_text().splitlines()]
        check(sum(e['kind'] == 'goal' and e.get('status') == 'met' for e in tr) == 4 and any(e['kind'] == 'decision' and e['choice'] == 'jump-gap' for e in tr),
              'the objectives\' decisions are in the one transcript (the jump among them)')
        first = json.loads(client.requests[0]['input'][1]['content'][0]['text'])
        check(first['digest']['exits'][0]['ref'] == 'exit:1' and first['why_now'] == 'start' and first['hints'] == ['the door is east of the gap'] and len(first['outline']) == 4,
              'the first request: the digest, why now, the chapter\'s hints and its outline')

        # 2. an objective fails, the planner is asked again with what went wrong and finds another way
        def bad_then_good(data):
            if not data['objectives_so_far']:
                return {'reason': 'try the chest first', 'objectives': [obj('open_chest', 'open the far chest', x=500.0, z=500.0, y=0.0, radius=1.0, max_seconds=40, progress='chest')]}
            return plan_one(data)
        cp, game, chan, st, run_dir, client = start(tmp, replies=[bad_then_good, plan_one, plan_one])
        outcome = cp.run(st)
        finish(game, chan)
        s = cp.write()
        again = json.loads(client.requests[1]['input'][1]['content'][0]['text'])
        check(outcome == 'solved' and s['timeline'][0]['goal_status'] != 'met' and again['why_now'].startswith('objective p1-1') and again['objectives_so_far'][0]['outcome'] != 'solved',
              f'a failed objective is followed by a replan that is told it failed ({again["why_now"]}; {again["objectives_so_far"][0]["outcome"]})')
        check('Where it got stuck' in (run_dir / 'timeline.md').read_text() and s['timeline'][0].get('steps'), 'the timeline says where it got stuck, with the last steps')

        # 3. no planner: the chapter's own outline is played in order (a seed already met by a marker is skipped)
        cp, game, chan, st, run_dir, client = start(tmp, no_planner=True)
        outcome = cp.run(st)
        finish(game, chan)
        check(outcome == 'solved' and [o['source'] for o in cp.timeline] == ['seed'] * 4 and cp.n_plan == 0, f'with no planner the outline is played ({outcome}, {len(cp.timeline)} objectives)')

        # 4. a planner that returns nothing playable, twice: the outline takes over, then nothing is left
        bare = CHAPTER.split('[[objective]]')[0]
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=bare, replies=[{'reason': 'none', 'objectives': []}, {'reason': 'none', 'objectives': []}, {'reason': 'none', 'objectives': []}])
        outcome = cp.run(st)
        finish(game, chan)
        check(outcome == 'gave up: no objective to play', f'a model with nothing to say and no outline ends the chapter ({outcome})')

        # a marker that only an objective's own proof can show (a dive, a ride, a night): met when the objective that serves it was met
        by = CHAPTER.replace('[[progress]]\nname = "friend"\nwhat = "the friend was talked to"\nflag = "Talked=1"', '[[progress]]\nname = "friend"\nwhat = "the friend was talked to"\nby_objective = true')
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=by, no_planner=True)
        check(cp.markers[2].by_objective and not cp.markers[2].check(state(flags={'Talked': 1})), 'a by_objective marker is not met by the state alone')
        outcome = cp.run(st)
        finish(game, chan)
        check(outcome == 'solved' and cp.markers[2].met_at is not None, f'an objective-proved marker is credited when its objective is met ({outcome})')

        # 5. budgets: game seconds
        cp, game, chan, st, run_dir, client = start(tmp, replies=[plan_one] * 4, budget_s=8)
        outcome = cp.run(st)
        finish(game, chan)
        check(outcome.startswith('budget: 8 s of game time') and len(cp.timeline) >= 1, f'the time budget ends the chapter ({outcome})')
        # ... and the number of objectives
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=CHAPTER.replace('max_objectives = 12', 'max_objectives = 1'), replies=[plan_one] * 4)
        outcome = cp.run(st)
        finish(game, chan)
        check(outcome == 'budget: 1 objectives' and len(cp.timeline) == 1, f'the objective budget ends it ({outcome})')
        # ... and the money
        cp, game, chan, st, run_dir, client = start(tmp, replies=[plan_one] * 4)
        cp.ledger0 = cp.spent() - 5.0
        outcome = cp.run(st)
        finish(game, chan)
        check(outcome == 'budget: 1 USD', f'the cost budget ends it ({outcome})')


ROAM = '''
name = "roam"
aim = "walk about the area and cover its ground"
area = "Fake_A"
budget_seconds = 900
budget_usd = 1.0
max_objectives = 20
max_replans = 12
per_plan = 1
look = false
region = {area = "Fake_A", center = [0.0, 0.0], radius = 15.0, cell = 7.5}
[[progress]]
name = "covered"
what = "half of the region was visited"
coverage = 0.5
'''


def roam_checks(tmp):
    """A free roam: no story marker, only the share of a region visited; the model is told which cells are not and walks there."""
    with WithWorld():
        asked = []

        def roam_plan(data):
            asked.append(data['region'])
            cell = data['region']['unvisited_nearest'][0]
            return {'reason': 'the nearest ground not visited', 'objectives': [obj('go_to', f'walk to ({cell["x"]}, {cell["z"]})', x=cell['x'], z=cell['z'], y=0.0, radius=2.0, max_seconds=40)]}
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=ROAM, replies=[roam_plan] * 12)
        outcome = cp.run(st)
        finish(game, chan)
        s = cp.write()
        check(outcome == 'solved' and s['coverage'] >= 0.5 and len(s['timeline']) >= 3, f'a free roam walks to unvisited ground until half the region was visited ({outcome}, coverage {s["coverage"]}, {len(s["timeline"])} objectives)')
        check(all(a['unvisited_nearest'] for a in asked) and asked[-1]['visited'] > asked[0]['visited'], 'each request says how much was visited and which cells are nearest and not')
        check('region visited' in (run_dir / 'timeline.md').read_text(), 'the timeline reports the share of the region')


def wait_time_checks(tmp):
    dark = Path(tmp) / 'd.png'
    bright = Path(tmp) / 'b.png'
    dark.write_bytes(vision.write_png(40, 20, bytes([8, 10, 30] * 800)))
    bright.write_bytes(vision.write_png(40, 20, bytes([200, 210, 230] * 800)))
    with WithWorld():
        def plan_wait(data):
            return {'reason': 'wait for the day', 'objectives': [obj('wait_time', 'wait until the day', until='day', max_seconds=200)]}
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=CHAPTER.replace('[[objective]]', '[[objective_unused]]', 0), replies=[plan_wait])
        shots = [dark, dark, bright]

        def capture():
            p = shots.pop(0) if len(shots) > 1 else shots[0]
            return p, 40, 20, 'image/png', 'bridge'
        cp.capture = capture
        cp.markers = [sections.Marker({'name': 'day', 'flag': 'Talked=1'})]   # (never met: the objective alone is looked at)
        cp.chapter.max_objectives = 1
        outcome = cp.run(st)
        finish(game, chan)
        rec = cp.timeline[0]
        check(rec['kind'] == 'wait_time' and rec['outcome'] == 'solved' and rec['goal_status'] == 'met' and [s['result'].split()[0] for s in rec['steps']] == ['night', 'night', 'day'],
              f'wait_time: looks until the brightness says day ({[s["result"] for s in rec["steps"]]}; {rec["outcome"]})')
        check(sum(1 for src, _ in game.log if src == 'wait 12') == 2, 'between looks the pilot waits 12 s, no input otherwise')
        shots[:] = [dark]
        cp, game, chan, st, run_dir, client = start(tmp, replies=[plan_wait])
        cp.capture = capture
        cp.markers = [sections.Marker({'name': 'day', 'flag': 'Talked=1'})]
        cp.chapter.max_objectives = 1
        cp.run(st)
        finish(game, chan)
        check(cp.timeline[0]['outcome'] == 'budget: time' and cp.timeline[0]['goal_status'] == 'open', 'a night that does not end within the objective\'s time is a budget outcome')


YOFF = '''
name = "y-off"
aim = "walk to a place whose height the map got wrong"
area = "Fake_A"
boot = "continue"
wall = 300
budget_seconds = 900
budget_usd = 1.0
max_objectives = 3
max_replans = 1
per_plan = 2
look = false

[[progress]]
name = "there"
what = "Link is near (15, 0)"
area = "Fake_A"
near = [15.0, 0.0, 3.0]

[[objective]]
name = "walk-there"
kind = "go_to"
what = "walk to (15, 0)"
progress = "there"
max_seconds = 120
targets = [{name = "spot", at = [15.0, 0.0], y = 9.0, radius = 1.5}]
'''


def marker_ends_objective_checks(tmp):
    """The marker an objective serves is the chapter's own word for it: an objective whose goal condition cannot be met (here the height the map gave is 9 m off, so
    the cross goal's `dy` of 2 m never holds) ends when its marker holds. (A rehearsal against a fake game walked 317 m to the marker and then played 24 more decisions.)"""
    with WithWorld():
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=YOFF, no_planner=True)
        outcome = cp.run(st)
        finish(game, chan)
        rec = cp.timeline[0] if cp.timeline else {}
        check(outcome == 'solved' and rec.get('goal_status') == 'met' and rec.get('game_s', 999) < 60.0,
              f"an objective ends as met when its marker holds, though its own height check never does ({outcome}; {rec.get('outcome')}, {rec.get('game_s')} game s, {rec.get('decisions')} decisions)")
        check(cp.markers[0].met_at is not None, 'and the marker is met')

    # ... but only for a plain walk. A chest, a talk or a sign is done when its own proof says so, even when the marker it was tied to (here: Link within 3 m of the
    # chest) holds on the way. (In the first hold of chapters a read-the-sign objective was "solved" because Link had walked 100 m: the sign was never read.)
    near_chest = YOFF.replace('walk to a place whose height the map got wrong', 'open the chest').replace('near = [15.0, 0.0, 3.0]', 'near = [10.0, 5.0, 3.0]') \
        .replace('Link is near (15, 0)', 'Link is near the chest') \
        .replace('kind = "go_to"\nwhat = "walk to (15, 0)"', 'kind = "open_chest"\nwhat = "open the chest"\nflag = "Chest_Open=1"') \
        .replace('targets = [{name = "spot", at = [15.0, 0.0], y = 9.0, radius = 1.5}]', 'targets = [{name = "chest", at = [10.0, 5.0], y = 0.0, kind = "chest", radius = 1.2}]')
    with WithWorld():
        cp, game, chan, st, run_dir, client = start(tmp, chapter_text=near_chest, no_planner=True)
        cp.run(st)
        finish(game, chan)
        check(game.chest_open and cp.timeline and cp.timeline[0]['goal_status'] == 'met',
              f"a chest objective tied to a place marker is still opened ({[(o['kind'], o['outcome']) for o in cp.timeline]}, chest open: {game.chest_open})")


def anomaly_checks(tmp):
    cp, game, chan, st, run_dir, client = start(tmp, no_planner=True)
    finish(game, chan)
    a = state(t=10.0, pos=[0.0, 5.0, 0.0])
    cp.detect(a, state(t=10.4, pos=[12.0, 5.0, 0.0]))
    cp.detect(a, state(t=10.4, pos=[1.0, -40.0, 0.0]))
    cp.detect(a, state(t=10.4, pos=[12.0, 5.0, 0.0], area='Fake_B'))
    cp.detect(a, state(t=10.4, pos=[12.0, 5.0, 0.0], loading=1))
    cp.detect(a, state(t=10.4, pos=[12.0, 5.0, 0.0], event=1))
    cp.detect(state(life=4), state(life=0))
    check([x['kind'] for x in cp.anomalies] == ['position-jump', 'fell-out', 'life-zero'], f'anomalies: a jump of place, a fall, a death; not a map change, a load or a cutscene ({[x["kind"] for x in cp.anomalies]})')
    check(len((run_dir / 'anomalies.jsonl').read_text().splitlines()) == 3, 'each is a line of anomalies.jsonl')


def cmd_run_checks(tmp):
    """`pilot.py run` on a chapter file, end to end, with a fake hsap (the made-up world behind hsap's command line)."""
    fake = Path(tmp) / 'fake_hsap.py'
    fake.write_text("import sys\nfrom pathlib import Path\n" f"sys.path.insert(0, {str(HERE)!r})\nimport test_sections\n"
                    "a = sys.argv[1:]\nlive = Path(a[a.index('--live') + 1])\nlive.mkdir(parents=True, exist_ok=True)\ntest_sections.WorldGame(live).run()\n")
    seg_path = Path(tmp) / 'chapter-cmd.toml'
    seg_path.write_text(CHAPTER)
    saved = pilot.HSAP
    pilot.HSAP = fake
    try:
        with WithWorld():
            args = argparse.Namespace(segment=str(seg_path), decider='rules', replay=None, planner=False, copy=str(Path(tmp) / 'copy'), tag=f'chapter-cmd-{time.time_ns()}',
                                      attach=None, save=None, wall=60, max_decisions=None, follow=[], end_hold=0.0, shots=0, api_base=None, allowance_usd=None,
                                      allowance_since='2026-01-01', matches=None, no_chapter_planner=True)
            code = pilot.cmd_run(args)
    finally:
        pilot.HSAP = saved
    run_dir = pilot.HERE / 'runs' / args.tag
    summary = json.loads((run_dir / 'summary.json').read_text())
    check(code == 0 and summary['outcome'] == 'solved' and summary['chapter']['objectives_met'] == 4 and summary['chapter']['outcome'] == 'solved',
          f"pilot.py run on a chapter: {summary['outcome']}, {summary.get('chapter')}")
    check((run_dir / 'chapter.json').exists() and (run_dir / 'timeline.md').exists() and summary['decisions'] >= 8, 'the run folder has the chapter\'s files and the decisions count')
    return run_dir


def run():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        saved = (pilot.FRAGMENTS, pilot.PLANNER_LOG, pilot.HERE, openai_api.LEDGER, openai_api.LEDGER_STANDIN, openai_api.CAPS)   # (nothing of a test goes into the real files)
        pilot.FRAGMENTS, pilot.PLANNER_LOG, pilot.HERE = tmp / 'fragments.jsonl', tmp / 'planner.jsonl', tmp
        openai_api.LEDGER, openai_api.LEDGER_STANDIN, openai_api.CAPS = tmp / 'ledger.jsonl', tmp / 'ledger-standin.jsonl', tmp / 'caps.json'
        openai_api.CAPS.write_text(json.dumps({'per_run_usd': 2, 'per_night_usd': 50, 'total_usd': 400, 'planner_share_of_night': 0.4, 'model': None}))
        try:
            run_all(tmp)
        finally:
            pilot.FRAGMENTS, pilot.PLANNER_LOG, pilot.HERE, openai_api.LEDGER, openai_api.LEDGER_STANDIN, openai_api.CAPS = saved
    print(f'test_sections: {CHECKS[0]} checks, {len(FAILS)} failed')
    return not FAILS


def run_all(tmp):
    if True:
        file_checks(tmp)
        marker_checks()
        digest_checks()
        plan_checks(tmp)
        daylight_checks(tmp)
        run_checks(tmp)
        marker_ends_objective_checks(tmp)
        roam_checks(tmp)
        wait_time_checks(tmp)
        anomaly_checks(tmp)
        cmd_run_checks(tmp)


def main():
    ok = run()
    print('test_sections: all passed' if ok else 'test_sections: FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
