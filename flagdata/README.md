# flagdata: flags, their names and what co-op does with them

A tool for working with the game's flags (its "game data") on **your own** copy of the game. The method, the families of flags and
what stays unnamed are in [docs/oot/flag-names.md](../docs/oot/flag-names.md).

| Tool | What it does |
|---|---|
| `flagdata.py build` | Runs the co-op progress classifier of LS's `host/oot_coop.c` over your GameDataList and RomFS, recovers the flags' names by hashing, and writes `flags.csv`, `excluded.csv` and `flags.md` (counts, families, maps, every small category in full, what is left out and why). Also writes a private cache that `lookup` reads. |
| `flagdata.py lookup HASH ...` | A flag's name, family, category and default mode from that cache. `flagdata.py hash NAME ...` prints the game's hash of a name. |

## Nothing of the game is here

No file, name, hash list or table of the game is in this repository, and the tools never write one into it. The inputs are yours (your
RomFS folder, an LS checkout); the outputs hold names read from your files, so **`build` refuses an `--out` or `--state` folder inside
this checkout**, and the cache lives under your own state folder (`$LS_FLAGDATA_STATE`, else `$XDG_STATE_HOME/ls-workbench/flagdata`,
else `~/.local/state/ls-workbench/flagdata`), mode 0600 in a 0700 folder. Keep `--out` outside any repository you push. The tests make a
made-up game in a temporary folder.

## Use

```sh
# the numbers the game logs, reproduced, and the reports (the game's own files stay where they are)
python3 -I -B flagdata/flagdata.py build --romfs <your RomFS folder> --repo <LS clone> --rev <commit> --out <a folder outside this repo> \
    --compare-log <a game log with the `playing a save:` line>
#   or: --source <host/oot_coop.c>  (coop_scenes.c beside it is read too)
#   --names FILE   names to try, one a line (# comments; `Part{000..127}` expands a numbered range): each is checked by hash
#   --sweep DIR    text that quotes names (the LS source, your notes): a stricter filter applies

python3 -I -B flagdata/flagdata.py lookup 1a2b3c4d
```

`--compare-log` fails the build (exit 1) if the log's `N progress flags (M of them story scenes ...)` numbers or its story-scene list
(`list <stamp>`) differ from what the tools compute; that is the proof that the reproduction is the game's own. A log made by another
build of the game, or by an LS commit with another list, will differ: pass the matching `--rev`.

## What it reads

- **GameDataList** (`GameData/GameDataList.Product.<n>.byml.zs`): the Bool and Int entries (hash, default, reset kind, save file index)
  and the structs. zstd is decoded with the game's own dictionaries (`Pack/ZsDic.pack.zs`); a file is recognised by its frame, not its name.
- **The story-scene list**: the EventSetting table and the folders `host/coop_scenes.c` scans (read from that source; defaults if it is
  absent), the same pass that feeds the name search.
- **The classifier's lists** from `host/oot_coop.c`: songs, heart rewards, unshared flags, boss sets, code-read scenes, quest and own
  counts, ammunition and the `#define`d struct and member hashes. They are parsed from the C text, not copied.
- The classifier's **rules** are code. `fd_classify.py` mirrors `classify()` as of `fd_source.RULES_VERIFIED`; when the source's
  `classify()` loses a fragment the mirror depends on, `build` warns (and the report says so). Update `fd_classify.py` first, then the
  fragment list and the revision.

## Files

| File | What |
|---|---|
| `fd_romfs.py` | MurmurHash3, zstd with dictionaries (Python 3.14's `compression.zstd`, the `zstandard` package or the `zstd` program), SARC, BYML, the RomFS folder |
| `fd_source.py` | The lists, constants and comments of `host/oot_coop.c`; the scan settings of `host/coop_scenes.c`; the rule check |
| `fd_scenes.py` | The story-scene list and its stamp |
| `fd_classify.py` | The classifier over the GameDataList; the categories and the room's default modes |
| `fd_names.py` | Name recovery: sources, order of trust, struct hierarchy, placed-actor ids, the chance-match filter |
| `fd_report.py` | Rows, families, maps, `flags.csv`, `excluded.csv`, `flags.md` |
| `fd_cache.py` | The private cache `lookup` reads |
| `flagdata.py` | The program |

Tests: `python3 -I -B tests/test_flagdata.py` (a made-up game; one test needs Python 3.14's zstd and is
skipped without it). Standard library only. Written on Linux; not run on Windows.
