"""The co-op progress classifier, run offline over a player's own GameDataList.

`classify_all` mirrors `classify()` and `build_table()` of `host/oot_coop.c` (verified against RULES_VERIFIED in fd_source.py): same
order of tests, same lists (read from the source by fd_source.py), same struct memberships, and the story-scene list of fd_scenes.py.
The game logs how many flags it classified in each category; `counts` gives the same numbers here, so the two can be compared.
"""
from collections import Counter
from dataclasses import dataclass

import fd_romfs

CATEGORIES = ("progress", "items", "songs", "quest", "keys", "rupees", "ammo")
# The room's default modes (coop_rules_default): progress, items, songs and quest shared, keys pooled, rupees and ammunition each
# player's own; story scenes are each player's own unless the room shares them.
DEFAULT_MODE = dict(progress="shared", items="shared", songs="shared", quest="shared", keys="pool", rupees="own", ammo="own")
GAMEDATA_FOLDER = "GameData"
GAMEDATA_NAME = r"GameDataList\.Product\.[0-9]+\.byml\.zs"


@dataclass
class Entry:
    hash: int
    kind: str          # "Bool" or "Int"
    default: int
    reset: int
    file: int


@dataclass
class Result:
    entry: Entry
    category: str      # None: left out
    scene: bool
    reason: str        # why it is left out ("" when it is classified)

    @property
    def mode(self):
        if self.category is None:
            return ""
        return "own" if self.scene else DEFAULT_MODE[self.category]

    @property
    def shared_by_default(self):
        return self.category is not None and self.mode in ("shared", "pool")


class GameTables:
    """What the classifier reads of the GameDataList: the Bool and Int entries and the structs."""

    def __init__(self, data):
        self.kinds = Counter({kind: len(rows) for kind, rows in data.items()})
        self.bools = [Entry(e["Hash"], "Bool", int(bool(e.get("DefaultValue"))), e.get("ResetTypeValue", 0), e.get("SaveFileIndex", 0))
                      for e in data.get("Bool", [])]
        self.ints = [Entry(e["Hash"], "Int", e.get("DefaultValue", 0), e.get("ResetTypeValue", 0), e.get("SaveFileIndex", 0))
                     for e in data.get("Int", [])]
        self.structs = {e["Hash"]: [(m["Hash"], m["Value"]) for m in e.get("DefaultValue", [])] for e in data.get("Struct", [])}

    def members(self, struct_hash):
        return self.structs.get(struct_hash, [])


def load_tables(romfs):
    path = romfs.glob_one(GAMEDATA_FOLDER, GAMEDATA_NAME)
    return GameTables(fd_romfs.byml(romfs.read(path))["Data"])


class Groups:
    """The struct memberships `build_table()` reads: markers, item counts, small keys, capacities and the HUD's map flags."""

    def __init__(self, tables, lists):
        c = lists.constants
        self.first_get = {v for _, v in tables.members(c["S_FIRST_GET"])}
        self.capacity = {v for _, v in tables.members(c["S_CAPACITY"])}
        self.pouch, self.keys, self.hud = set(), set(), set()
        for _, sub in tables.members(c["S_POUCH"]):
            self.pouch.update(v for m, v in tables.members(sub) if m == c["M_STOCK"])
        for _, sub in tables.members(c["S_DUNGEON"]):
            for m, v in tables.members(sub):
                if m == c["M_SMALL_KEYS"]:
                    self.keys.add(v)
                if m in (c["M_GET_MAP"], c["M_GET_COMPASS"], c["M_GET_BOSS_KEY"]):
                    self.hud.add(v)


class Classifier:
    def __init__(self, tables, lists, scenes):
        self.tables, self.lists, self.scenes = tables, lists, scenes
        self.groups = Groups(tables, lists)
        L = lists.lists
        self.song, self.heart, self.unshared = set(L["song_flags"]), set(L["heart_reward_flags"]), set(L["unshared_flags"])
        self.code_read, self.quest, self.own = set(L["code_read_scenes"]), set(L["quest_counts"]), set(L["own_counts"])
        self.ammo = dict(lists.ammo)
        self.boss_own, self.boss_proof = set(), {}
        for b in lists.boss_sets:
            self.boss_proof.update({h: e for h, e in b["proof"]})
            self.boss_own.update(h for h, _ in b["members"])
            self.boss_own.update(b["fight"])

    def bool_entry(self, e):
        """classify() for a Bool: the first test that fails leaves it out."""
        l = self.lists
        if e.file == -1:
            return Result(e, None, False, "never saved (save file index -1)")
        if e.file != 0:
            return Result(e, None, False, "saved in another file than the slot's (save file index %d)" % e.file)
        if e.reset & l.reset_temporary:
            return Result(e, None, False, "temporary (reset at a scene change, a new day or a load)")
        if not e.reset & l.reset_new_game:
            return Result(e, None, False, "not reset by a new game")
        if e.hash in self.heart:
            return Result(e, None, False, "heart reward (stays each player's own)")
        if e.hash in self.unshared:
            return Result(e, None, False, "unshared list (one player's own: a place, a failed try, a refill, the age)")
        if e.hash in self.boss_own:
            return Result(e, None, False, "a boss fight's own flag (its fight or the end set's members)")
        cat = "songs" if e.hash in self.song else "items" if e.hash in self.groups.first_get else "progress"
        scene = (cat == "progress" and e.hash in self.scenes.kept and e.hash not in self.code_read
                 and e.hash not in self.boss_proof)
        return Result(e, cat, scene, "")

    def int_entry(self, e):
        c, g = self.lists.constants, self.groups
        if e.hash == c["F_RUPEES"]:
            return Result(e, "rupees", False, "")
        if e.hash in g.keys:
            return Result(e, "keys", False, "")
        if e.hash in g.pouch:
            if e.hash in self.own:
                return Result(e, None, False, "a count that stays with each player (own_counts)")
            cat = "ammo" if e.hash in self.ammo else "quest" if e.hash in self.quest else "items"
            return Result(e, cat, False, "")
        if e.hash in g.capacity or e.hash == c["F_MAGIC_SIZE"]:
            return Result(e, "items", False, "")
        return Result(e, None, False, "an Int that is in no group the sync knows")

    def all(self):
        return [self.bool_entry(e) for e in self.tables.bools] + [self.int_entry(e) for e in self.tables.ints]


def counts(results):
    """The numbers the game logs: {category: n, 'scenes': n}."""
    c = Counter(r.category for r in results if r.category)
    out = {cat: c[cat] for cat in CATEGORIES}
    out["scenes"] = sum(1 for r in results if r.scene)
    return out
