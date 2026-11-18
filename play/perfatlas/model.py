#!/usr/bin/env python3
"""The perf atlas's data: a library of viewpoints and walks (local data, written by the user from their own game files) and a
catalogue of conditions (what is switched off or changed to price a part of the frame). Pure: no game, no files but the
library JSON a caller hands over.

A viewpoint is a place to stand still: map position, the camera's yaw (and Link's facing, which only gives the yaw when none is
named), the time of day, how long to dwell. `pos_line` is the live-input line that puts Link there: `!pos X Y Z` (metres, the numbers
of the control bridge's `status.game`). The facing and camera angles of the longer `!pos` form did not take effect in the game
(PosCommand's run and the atlas's first hold, 17 Nov), so the camera is turned by the right stick, closed loop (`driver.aim`). A walk is a list of ground points walked in a closed loop.
A condition is a change to one run: actor rules (`!actors off ...`), settings (`option set`), a launch switch (an environment
variable: a new game process) or the time of day.

Library shape (a JSON object; the file stays outside every repository, it holds game places):

    {"schema_version": 1,
     "areas": {"<key>": {"map": "<the game's map name>", "age": "child"|"adult", "saves": {"day": "<save>", "night": "<save>"}}},
     "viewpoints": [{"id": "...", "area": "<key>", "pos": [x, y, z], "facing": deg, "camera": {"yaw": deg, "pitch": deg},
                     "tod": "day", "dwell_s": 15, "settle_s": 3, "min_frames": 0, "tags": ["crowd"], "note": "..."}],
     "walks": [{"id": "...", "area": "<key>", "start": [x, y, z], "points": [[x, z], ...], "radius": 1.5, "timeout_s": 90, "tod": "day"}],
     "conditions": [<a condition, see Condition>]}
"""
import math
import re
from dataclasses import dataclass, field

ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
MAP_RE = re.compile(r'^[A-Za-z0-9_]{1,64}$')
PATTERN_RE = re.compile(r'^[A-Za-z0-9_*?]{1,64}$')
ENV_NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')
ENV_VALUE_RE = re.compile(r'^\S*$')
OPTION_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
KINDS = ('baseline', 'actors', 'option', 'env', 'time')
AGES = ('child', 'adult')
BIG = 1e6


class LibraryError(ValueError):
    """the library or a condition is not usable; the message names the item and the field"""


def number(value, what, lo=-BIG, hi=BIG):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LibraryError(f'{what}: {value!r} is not a finite number')
    if not lo <= value <= hi:
        raise LibraryError(f'{what}: {value!r} is outside {lo:g}..{hi:g}')
    return float(value)


def ident(value, what):
    if not isinstance(value, str) or not ID_RE.match(value):
        raise LibraryError(f'{what}: {value!r} is not an id (lower case letters, digits, . _ -; at most 64)')
    return value


@dataclass(frozen=True)
class Area:
    key: str
    map: str
    age: str
    saves: dict


@dataclass(frozen=True)
class Viewpoint:
    id: str
    area: str
    pos: tuple
    facing: float = None
    cam_yaw: float = None
    cam_pitch: float = None
    tod: str = 'day'
    dwell_s: float = 15.0
    settle_s: float = 3.0
    min_frames: int = 0
    tags: tuple = ()
    note: str = ''

    def pos_line(self):
        """the live-input line for this viewpoint: the position only"""
        return '!pos ' + ' '.join(f'{n:.3f}' for n in self.pos)

    def aim(self):
        """[camera yaw, pitch] to turn the camera to, or None when the library names neither a camera yaw nor a facing (the camera stays
        where the game puts it). Pitch is carried for the record; the right stick turns the yaw."""
        yaw = self.cam_yaw if self.cam_yaw is not None else self.facing
        return None if yaw is None else [yaw, self.cam_pitch]


@dataclass(frozen=True)
class Walk:
    id: str
    area: str
    points: tuple
    start: tuple = None
    radius: float = 1.5
    timeout_s: float = 90.0
    tod: str = 'day'
    tags: tuple = ()
    note: str = ''


@dataclass(frozen=True)
class Condition:
    """What one block of a run changes.

    kind 'baseline': nothing. 'actors': `rules` is a list of {"do": "off"|"on", "pattern": "<kind or family glob>", "force": false};
    they are sent as `!actors off|on <map> <pattern>` after an `!actors clear`, then the area is reloaded (the game applies the rules at an
    area's next layout read). 'option': `options` maps an Insert-menu key to a value, set live and put back after the block.
    'env': `env` maps switch names to values; they need a new game process, so the planner gives such a condition its own run.
    'time': `hour` (0..23) is set (`!cheat 100+h`) and the area reloaded.
    `family` names the part of the frame this condition prices ('people', 'animals', ...): the cost table groups by it."""
    id: str
    kind: str
    label: str = ''
    family: str = ''
    rules: tuple = ()
    options: tuple = ()
    env: tuple = ()
    hour: int = None
    reload: bool = None
    note: str = ''


@dataclass
class Library:
    areas: dict = field(default_factory=dict)
    viewpoints: dict = field(default_factory=dict)
    walks: dict = field(default_factory=dict)
    conditions: dict = field(default_factory=dict)

    def area_of(self, item_id):
        item = self.viewpoints.get(item_id) or self.walks.get(item_id)
        if item is None:
            raise LibraryError(f'{item_id}: no such viewpoint or walk')
        return self.areas[item.area]

    def save_for(self, area_key, tod):
        saves = self.areas[area_key].saves
        if tod not in saves:
            raise LibraryError(f'area {area_key}: no save for time of day {tod!r} (has {sorted(saves)})')
        return saves[tod]


def parse_viewpoint(d, areas):
    if not isinstance(d, dict):
        raise LibraryError('a viewpoint is not an object')
    vid = ident(d.get('id'), 'viewpoint id')
    area = d.get('area')
    if area not in areas:
        raise LibraryError(f'viewpoint {vid}: area {area!r} is not in the library')
    pos = d.get('pos')
    if not isinstance(pos, (list, tuple)) or len(pos) != 3:
        raise LibraryError(f'viewpoint {vid}: pos must be [x, y, z]')
    pos = tuple(number(v, f'viewpoint {vid} pos', -100000, 100000) for v in pos)
    facing = d.get('facing')
    facing = None if facing is None else number(facing, f'viewpoint {vid} facing', -720, 720)
    cam = d.get('camera') or {}
    if not isinstance(cam, dict):
        raise LibraryError(f'viewpoint {vid}: camera must be an object with yaw and pitch')
    yaw = cam.get('yaw')
    pitch = cam.get('pitch')
    yaw = None if yaw is None else number(yaw, f'viewpoint {vid} camera yaw', -720, 720)
    pitch = None if pitch is None else number(pitch, f'viewpoint {vid} camera pitch', -90, 90)
    tod = d.get('tod', 'day')
    if tod not in areas[area].saves:
        raise LibraryError(f'viewpoint {vid}: tod {tod!r} has no save in area {area} ({sorted(areas[area].saves)})')
    tags = d.get('tags', [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and ID_RE.match(t) for t in tags):
        raise LibraryError(f'viewpoint {vid}: tags must be a list of ids')
    min_frames = d.get('min_frames', 0)
    if isinstance(min_frames, bool) or not isinstance(min_frames, int) or not 0 <= min_frames <= 100000:
        raise LibraryError(f'viewpoint {vid}: min_frames must be a whole number 0..100000')
    return Viewpoint(vid, area, pos, facing, yaw, pitch, tod, number(d.get('dwell_s', 15), f'viewpoint {vid} dwell_s', 3, 300),
                     number(d.get('settle_s', 3), f'viewpoint {vid} settle_s', 0, 60), min_frames, tuple(tags), str(d.get('note', ''))[:200])


def parse_walk(d, areas):
    if not isinstance(d, dict):
        raise LibraryError('a walk is not an object')
    wid = ident(d.get('id'), 'walk id')
    area = d.get('area')
    if area not in areas:
        raise LibraryError(f'walk {wid}: area {area!r} is not in the library')
    pts = d.get('points')
    if not isinstance(pts, list) or not pts:
        raise LibraryError(f'walk {wid}: points must be a list of [x, z]')
    points = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise LibraryError(f'walk {wid}: a point is not [x, z]')
        points.append(tuple(number(v, f'walk {wid} point', -100000, 100000) for v in p))
    start = d.get('start')
    if start is not None:
        if not isinstance(start, (list, tuple)) or len(start) != 3:
            raise LibraryError(f'walk {wid}: start must be [x, y, z]')
        start = tuple(number(v, f'walk {wid} start', -100000, 100000) for v in start)
    tod = d.get('tod', 'day')
    if tod not in areas[area].saves:
        raise LibraryError(f'walk {wid}: tod {tod!r} has no save in area {area}')
    return Walk(wid, area, tuple(points), start, number(d.get('radius', 1.5), f'walk {wid} radius', 0.3, 20),
                number(d.get('timeout_s', 90), f'walk {wid} timeout_s', 5, 600), tod, tuple(d.get('tags', [])), str(d.get('note', ''))[:200])


def parse_condition(d):
    if not isinstance(d, dict):
        raise LibraryError('a condition is not an object')
    cid = ident(d.get('id'), 'condition id')
    kind = d.get('kind')
    if kind not in KINDS:
        raise LibraryError(f'condition {cid}: kind {kind!r} is not one of {KINDS}')
    rules, options, env, hour = (), (), (), None
    if kind == 'actors':
        raw = d.get('rules')
        if not isinstance(raw, list) or not raw:
            raise LibraryError(f'condition {cid}: an actors condition needs rules')
        out = []
        for r in raw:
            if not isinstance(r, dict) or r.get('do') not in ('off', 'on') or not isinstance(r.get('pattern'), str) or not PATTERN_RE.match(r['pattern']):
                raise LibraryError(f'condition {cid}: a rule is {{"do": "off"|"on", "pattern": "<glob>"}}, got {r!r}')
            out.append((r['do'], r['pattern'], bool(r.get('force', False))))
        rules = tuple(out)
    elif kind == 'option':
        raw = d.get('options')
        if not isinstance(raw, dict) or not raw:
            raise LibraryError(f'condition {cid}: an option condition needs options')
        for k, v in raw.items():
            if not OPTION_RE.match(k) or isinstance(v, (dict, list)) or v is None:
                raise LibraryError(f'condition {cid}: option {k!r}={v!r} is not a key and a number, bool or short word')
        options = tuple(sorted(raw.items()))
    elif kind == 'env':
        raw = d.get('env')
        if not isinstance(raw, dict) or not raw:
            raise LibraryError(f'condition {cid}: an env condition needs env')
        for k, v in raw.items():
            if not ENV_NAME_RE.match(k) or not ENV_VALUE_RE.match(str(v)):
                raise LibraryError(f'condition {cid}: {k}={v!r} is not NAME=VALUE (an upper case name, a value without white space)')
        env = tuple(sorted((k, str(v)) for k, v in raw.items()))
    elif kind == 'time':
        hour = d.get('hour')
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
            raise LibraryError(f'condition {cid}: hour must be a whole number 0..23')
    reload = d.get('reload')
    return Condition(cid, kind, str(d.get('label', cid))[:80], str(d.get('family', ''))[:40], rules, options, env, hour,
                     None if reload is None else bool(reload), str(d.get('note', ''))[:200])


def load_library(obj):
    """validate a parsed library (see the module's doc) into a Library; any fault raises LibraryError naming it"""
    if not isinstance(obj, dict) or obj.get('schema_version') != 1:
        raise LibraryError('the library needs "schema_version": 1')
    areas = {}
    for key, a in (obj.get('areas') or {}).items():
        ident(key, 'area key')
        if not isinstance(a, dict) or not isinstance(a.get('map'), str) or not MAP_RE.match(a['map']):
            raise LibraryError(f'area {key}: map must be the game map name (letters, digits, _)')
        if a.get('age') not in AGES:
            raise LibraryError(f'area {key}: age must be one of {AGES}')
        saves = a.get('saves')
        if not isinstance(saves, dict) or not saves or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in saves.items()):
            raise LibraryError(f'area {key}: saves maps a time of day to a save name or path')
        areas[key] = Area(key, a['map'], a['age'], dict(saves))
    if not areas:
        raise LibraryError('the library has no areas')
    lib = Library(areas=areas)
    for d in obj.get('viewpoints') or []:
        v = parse_viewpoint(d, areas)
        if v.id in lib.viewpoints or v.id in lib.walks:
            raise LibraryError(f'{v.id}: id used twice')
        lib.viewpoints[v.id] = v
    for d in obj.get('walks') or []:
        w = parse_walk(d, areas)
        if w.id in lib.viewpoints or w.id in lib.walks:
            raise LibraryError(f'{w.id}: id used twice')
        lib.walks[w.id] = w
    for d in obj.get('conditions') or []:
        c = parse_condition(d)
        if c.id in lib.conditions:
            raise LibraryError(f'{c.id}: condition id used twice')
        lib.conditions[c.id] = c
    return lib


# ---------------------------------------------------------------- the catalogue of conditions

def _actors(cid, family, patterns, label, note=''):
    return {'id': cid, 'kind': 'actors', 'family': family, 'label': label, 'note': note,
            'rules': [{'do': 'off', 'pattern': p} for p in patterns]}


# What the actor tools (the Debug tab's Actors page, `!actors`, HOS_OOT_ACTORS_OFF) offer: the first two words of a kind name plus `*`.
# People tied to a door or an event, and a behaviour group's leader, stay whatever the rule says (the game stalls without them).
ACTOR_FAMILIES = {
    'people': ('Npc_*',),
    'animals': ('Animal_*', 'EnvironmentalAnimal_*'),
    'effects': ('EftLoc_*',),
    'breakables': ('Obj_Pot_*', 'Obj_BrokenBox_*', 'Obj_Plant_*'),
    'enemies': ('Enemy_*',),
}


def builtin_conditions():
    """the standing catalogue: baseline, each actor family off, every offered family off, the cost curves of the render settings
    and the launch switches that take a part of the renderer out. Values are the inventory's (settings keys of the Insert menu,
    switches of gpu/vk/switches.def); a library can add or replace any of them by id."""
    raw = [{'id': 'baseline', 'kind': 'baseline', 'label': 'as the game plays', 'family': 'baseline'}]
    for fam, pats in ACTOR_FAMILIES.items():
        raw.append(_actors('actors-' + fam, fam, pats, f'{fam} off (the actor tools)'))
    raw.append(_actors('actors-all', 'all-actors', [p for pats in ACTOR_FAMILIES.values() for p in pats], 'every offered family off'))
    raw += [
        {'id': 'res-50', 'kind': 'option', 'family': 'resolution', 'label': 'render resolution 50%', 'options': {'resolution': 4}},
        {'id': 'res-33', 'kind': 'option', 'family': 'resolution', 'label': 'render resolution 33%', 'options': {'resolution': 6}},
        {'id': 'shadows-high', 'kind': 'option', 'family': 'shadows', 'label': 'shadow resolution Highest', 'options': {'shadow_resolution': 3}},
        {'id': 'shadows-game', 'kind': 'option', 'family': 'shadows', 'label': 'shadow resolution the game\'s own', 'options': {'shadow_resolution': 1}},
        {'id': 'aniso-off', 'kind': 'option', 'family': 'filtering', 'label': 'anisotropic filtering the game\'s own', 'options': {'anisotropic_filtering': 0}},
        {'id': 'aniso-16', 'kind': 'option', 'family': 'filtering', 'label': 'anisotropic filtering 16x', 'options': {'anisotropic_filtering': 4}},
        {'id': 'upscaler-off', 'kind': 'option', 'family': 'upscaler', 'label': 'upscaler Off', 'options': {'dlss': 6}},
        {'id': 'occlusion-queries', 'kind': 'env', 'family': 'culling', 'label': 'real occlusion query counts', 'env': {'RX_VK_OCC_QUERIES': '1'}},
        {'id': 'no-cull', 'kind': 'env', 'family': 'culling', 'label': 'culling off (RX_VK_NOCULL)', 'env': {'RX_VK_NOCULL': '1'}},
        {'id': 'pass-timing', 'kind': 'env', 'family': 'gpu-passes', 'label': 'GPU pass timing (every span waits for the GPU)', 'env': {'RX_VK_PASS_TIMING': '1'}},
        {'id': 'pass-timing-programs', 'kind': 'env', 'family': 'gpu-passes', 'label': 'GPU pass timing with the programs', 'env': {'RX_VK_PASS_TIMING': '2'}},
        {'id': 'vram-4000', 'kind': 'env', 'family': 'memory', 'label': 'a 4000 MB video-memory budget', 'env': {'RX_VK_VRAM_MB': '4000'}},
        {'id': 'no-mesh', 'kind': 'env', 'family': 'geometry', 'label': 'the mesh path off (RX_VK_NO_MESH=1; the picture changes)', 'env': {'RX_VK_NO_MESH': '1'}},
    ]
    return {c['id']: parse_condition(c) for c in raw}


def conditions_for(lib, ids):
    """the conditions named by ids, from the library first, then the catalogue; an unknown id raises"""
    cat = builtin_conditions()
    out = []
    for i in ids:
        c = lib.conditions.get(i) or cat.get(i)
        if c is None:
            raise LibraryError(f'{i}: no such condition (library: {sorted(lib.conditions)}; catalogue: {sorted(cat)})')
        out.append(c)
    return out
