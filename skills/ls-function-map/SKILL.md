---
name: ls-function-map
description: >
  Use before reading lifted game code and whenever a task meets an `f_<address>` function, a game structure or field
  offset, a `patch_*` hook in nx2/oot_*.py, a frame-rate site, or a game subsystem you do not know (jobs, physics,
  drawing, shaders, textures, input, audio, saves): look the address or the subject up in the function map first, weigh
  the row by how sure it is, and hand back a row for what you worked out. Also for checking the map against the port's code, and
  for a flag's hash or name in a co-op log (game data: section 6).
---

# The function map: look it up first, add what you learn

The game ships without symbols, so the lifted code calls every function `f_<address>`. The map says what the ones
somebody already looked at do: about 1,700 rows over 31 pages, one page per part of the game, each row with how sure it
is. Reading it takes seconds; working a function out again from lifted C takes an hour and may get it wrong.

- Pages and conventions: `/home/mooki/Code/hookshot-workbench/docs/oot/` (`README.md` is the index).
- Tool: `/home/mooki/Code/hookshot-workbench/docs/oot/fnmap.py` (standard library; it only reads files, so it needs
  no lock).
- Published in the workbench repository; written by dyst.

## 1. Look it up

```sh
M=/home/mooki/Code/hookshot-workbench/docs/oot
python3 $M/fnmap.py find f_803edf70        # an address: its page and its one row
python3 $M/fnmap.py find 0x84e98e38        # a data address
python3 $M/fnmap.py find "time of day"     # words: rows that have all of them (first 20)
python3 $M/fnmap.py find BakeProbe         # a thread, class or job name from a log line works the same way
```

- Every function has **one row on one page**. `mentioned, no row` lists the lines that name what you searched for
  without describing it: another function's row, or a page's prose (its *Notes* and *Open questions* often say more
  than its rows).
- No hit at all: find the subject instead. The index in `README.md` says which page covers which part of the game;
  read that page's "In short" before concluding the map has nothing.
- Read the whole row and the page's first paragraph before the lifted code. A row that says `Hooked by patch_x` tells
  you which function in `nx2/oot_*.py` changes it; the patch's own comment says what the patch does.
- **No shell (a read-only worker)?** The tool is a convenience, not a requirement: every function is one line of one
  page, so search the pages themselves for `f_<8 hex digits>` (or `0x<address>`, or the words) under
  `docs/oot/*.md`. Read the line's whole row and its page's "In short".
- **Also search the earlier investigations** before working anything out again: the reports folder (the one the
  brief, `AGENT-BRIEF.md`, lives in; its `*.md` files) holds findings that never became rows (chests and item giving,
  crashes, frame-rate sites). Search it for the address or the game's name for the thing. A report can be older than the map
  and wrong where they differ; say which you used, and hand the report's still-true findings back as rows (section 4).

### When the map has nothing: reading the lifted code

The lifted C of a build is in its work folder, `<worktree>/work/build/sa/gen/code_*.c`; use a retained build of the
commit you are asking about (the brief and `reports/LOG.md` name them; one of main is
`/home/mooki/Code/hookshot-wt/longshot-base-d3bd2805/work/build/sa/gen`). It is game-derived: it never leaves the PC.

- A function starts at `void f_<address>(Ctx *__restrict A64_NOESC c) {` in exactly one file. The files are tens of
  megabytes: find the file first (`grep -l -m1 '^void f_803edf70(Ctx' $G/code_0*.c`), then read that one function.
  Searching many of these files in one call can silently miss matches; search one file at a time.
  The link map names the file in one grep: `grep -m1 '(.text.f_803edf70)' <worktree>/work/build/sa/build/longshot.map`
  shows the object (`code_001.o` is `code_001.c`), which beats searching the code files.
- The guest image is on disk too: `<worktree>/work/build/sa/image/image.bin` (its first byte is guest address
  `0x80000000`, so a file offset is the address minus `0x80000000`; that is the image's base, **not** the main module's,
  which is `0x80020000`: see the bullet on another project's addresses below) holds the relocated vtables, registry
  tables and every string. A vtable slot is `vtable + 8 * slot`; read a string or a table at its address directly
  instead of searching the C for who loads it.
- Which file holds an address is decided per build. Do not work it out from a `chunks.txt` or an image listing that
  belongs to another build's work folder; search the files of the build you are reading.
- Strings are not in the C: a thread or class name is loaded from the game image by address. To get from a name in a
  log to its function, find who references the string's address, or start from a neighbour the map already has.
- Thread names are not unique: a pool's workers share one name (six `BaseProcCreateAndDeleteThread`, one
  `PreActorModelCompose` per core). A name in a log line or a sampler does not identify which worker it was.
- Another project's addresses (a co-op mod's, say) are offsets into the game's main module, which is loaded at
  **`0x80020000`**: our address is their offset **plus `0x80020000`**, and their offset is our address minus
  `0x80020000`. Their `0x57af30` is `f_8059af30`; their `0x33e4200` is `f_83404200` (`0x033e4200 + 0x80020000`), data
  converts the same way (their `0x4e94840` is `0x84eb4840`). Do not use `0x80000000` (the image file's base above): it
  lands `0x20000` too low, and their `0x33e4200` becomes `f_833e4200`, an address where no function starts (once
  copied into reports as the HUD refresh, which is `f_83404200`). A converted address is a function only if a
  `void f_<address>(Ctx` line exists for it: check that before you write a row. Every one checked by reading the
  function was the one their comment names, but treat their name as a lead. An offset can land inside a function
  (their `0x49dcb0` is a place inside `f_804bd8c0`, not a function: find the function that contains it and put the
  place in its row as `at 0x...`), and a name can belong to the next class's function; read it before you trust it.
- What you work out this way is exactly what section 4 is for: hand the rows back.
- A function start is exactly an address with a `void f_<address>(Ctx` line; the owner of a place inside a function is
  the nearest start before it in the link map. `fnmap.py find` prints `nothing` for a place written as `f_<address>`.

## 2. Weigh the row before you build on it

| Sure | What it means | How to use it |
|---|---|---|
| **tested** | seen in the game, or a shipping patch relies on it | rely on it for the role the row states, not beyond |
| **read** | worked out from the lifted code, never run | a strong lead: confirm the part you depend on in the lifted code or in a run |
| **guess** | from names, constants or neighbours | a hint where to look, nothing more |

Nine rows in ten are **read**. The addresses belong to the supported game build, which never changes, so a row does not
go stale by itself; what goes stale is what a row says about the port (`Hooked by`, "the fix is ...", hot ranks). When a
row and the port's code disagree, the code is right: say so in your report and propose the corrected row.

## 3. Use what you found

- In a report to the main session, cite it: page, address, the row's *Sure*. Say whether you confirmed it and how.
- In anything public (a pull request, an issue, a comment, the team room) write what the change needs **in your own
  words**. Never paste pages, tables or runs of rows, and never lifted code. The port's own rule stands: nothing made
  from the game leaves the PC.

## 4. Add what you learn

Add a row when you worked out what a function or a data address does, when a patch or a host file newly names a game
function, when you confirmed a **read** or **guess** row in the game (raise its *Sure*), or when a row is wrong.

A row is an address, a working name of ours in `Owner_Action` form, what it does in our own words, and one of the three
*Sure* values. The format, the page template and what may not go in are in `README.md` ("Conventions", "A page", "What
may go here"). Not allowed in a row: lifted C, disassembly, text or file contents copied from the game, and the state
of a pull request ("open", "fixed in"): describe the game, not the tracker.

```markdown
| `f_80652cb0` | `FloorWeb_Simulate` | The floor web's simulation: `(int)step` steps a frame, clamped to 0..3. Cut to 0 above 60 fps, the web stayed rigid. Its remainder is kept per web (`+2296`). Hooked by `patch_whole_frames` | tested |
```

**A worker does not commit in the workbench repository.** Put this in your final report and the main session adds it:

```markdown
## Function map rows
- page: frame-time.md, table "Sites examined above 60"; current row (from `find`): none | page:line; new | corrects | extends | confirms f_...
- how I know: <the lifted lines you read, the run, the test>
| `f_...` | `Name` | What it does | read |
```

The main session edits the page, runs the check below, commits and pushes.

One address per row, alone in the first cell as `` `f_80652cb0` ``: `fnmap.py` reads a first cell such as
`` `f_8305e390`, `f_8305e4c0` `` or `` `0x84b6ff90` (vtable) `` as no row at all.

## 5. Check the map against the port's code

```sh
LS_REPO=/home/mooki/Code/Hookshot python3 $M/fnmap.py check --rev forge/main
```

It fails when the port's code (`nx2/oot_*.py`, `host/`) names a game function that has no row, when an address has two
rows, when a row is malformed, or when a page names a patch that does not exist; it notes patched functions whose row
does not say `Hooked by`. Run it after the port's main moved and before adding rows. A function the code names without
a row is the usual way the map falls behind: write its row from the patch's own comment and mark it **tested** only if
the patch ships.

The check looks for `f_<8 hex digits>` spellings only: a game function the port reaches through a hexadecimal
call-target constant is not seen, so inventory those by exact address yourself. `--repo` names the port's clone and
`--rev` the revision it reads (default `HEAD`): a row that documents a patch of a branch not yet on `forge/main` fails
against `forge/main`; check against the branch that holds the patch.

## 6. Game data: a flag in a co-op log, and what co-op shares

The game names its flags by hash only. A co-op log line such as `applied progress <8 hex digits> = 1` says which flag by its hash; the
page `docs/oot/flag-names.md` explains how names are recovered from the player's own files, what stays unnamed and why, and how the
co-op layer sorts flags into categories (and which a default room shares). A tool, in `flagdata/`, runs on the player's own game:

```sh
W=$M/../../flagdata    # (M: the map's folder, section 1)
python3 -I -B $W/flagdata.py lookup 1a2b3c4d      # one hash
python3 -I -B $W/flagdata.py build --romfs <RomFS> --repo <LS clone> --rev <commit> --out <folder outside any repository>
```

`flagdata.py lookup` reads the private table `build` made under the user's state folder (`$XDG_STATE_HOME/ls-workbench/flagdata`); without it, it
says how to make one. **The names are game data**: they stay in that table, in `--out` and in your report's files folder, never in a skill,
a pull request, an issue or a comment. Say "a played event of the room's data" or give the hash, not the name; the exceptions are names LS's
own source already quotes. A hash with no name is normal (about a quarter of the flags have none): say so, and say the family.

## When this skill let you down

If a lookup that should have worked did not, a step here was wrong or missing, or you needed something the tool cannot
do, say so under **Skill notes** in your report (see `skill://ls-skill-upkeep`). That is how this page gets better.
