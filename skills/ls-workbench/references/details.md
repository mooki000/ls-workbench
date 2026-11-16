# Workbench details: donor builds and fixtures

Reference material for `SKILL.md`; read the part the task touches.

## Donor builds: what `--dry-run` shows and the silent stops

- `nx2/build.py --dry-run` is the start-up with no lift: it lists the files the compile would read and fails on a
  missing one (`dry run: N files ..., M missing`). `lifted code ... (0 files)` there is not a broken donor.
- The new/cached split (`@@plan <new> <total>`) is printed only by the real compile, and only with
  `LONGSHOT_TIMES=<build_times.json>` (`nx2/make_game.py`'s record of earlier builds), so it cannot be read beforehand.
- `nx2/kit.py`'s `wait_for_quiet` sleeps in 60-second steps while `<worktrees>/.bench-lock` exists (build and dry run).
- `make_game.drop_stale_objects` deletes the object cache when the compiler's first version line or
  `/usr/include/stdio.h` changed since the donor was built: an unseen hours-long rebuild. Try the build on a copy first.
- A newer main base can recompile every host unit and most lifted units although the lift identity is equal (new
  compile defines, host-source changes): predict a long build, not a minutes-long one.
- The tested form of the donor recipe is `/home/mooki/Code/hookshot-wt/reports/tools/wincross/native-resume.py` with the
  registered lift-identity proofs in `donors.json` beside it; `reports/BuildHeads-files/preflight.py` holds the per-head
  preflight and its summaries. Mirror their `HOME`, profile path and identity checks rather than the hand recipe in
  `SKILL.md` (a donor row for a head whose `nx2/` equals a lifted one needs a **wrapped** identity proof, not the bare
  `lift-identity.py` output).
- The version the game logs is `git describe --tags --always --dirty` of the build checkout (`nx2/version.py`; also
  `LONGSHOT_VERSION` or a `VERSION` file), so an assignment's quoted tag can be one release behind. Take the expected
  text from the build's own receipt or from `python -I nx2/version.py` in that folder, never from a literal.
- Unverified here, check on the head's `nx2/build.py` before relying on either way: whether the renderer objects hit
  the donor's cache from a work folder other than `<checkout>/work/build` (the renderer's profile is unpacked into
  `<work>/longshot_vk.shipped.profdata`; a comment on the object keys in `nx2/build.py` suggests the path is kept out of
  them, but two workers rebuilt the renderer objects from another folder).

## Fixtures: text-cutting tests, build lists, fuzzing

- Many fixtures cut functions or tables out of source files by their text (`host/oot_coop.c` pieces by marker,
  `host/nn_fs.c`, `host/posix/win32_posix.c`, the language counts in `gpu/vk/menu_i18n.h`). After a rebase, a replay or a
  change inside such text, run them on the new head **before** any queued hold; `git grep -l '<changed file>' tests`
  lists the drivers that read it. A suite run on an older base is not the suite of the integrated head.
- A host `.c` file that another one uses must be added to every driver that compiles the other
  (`git grep -l coop_portmap.c tests` found eleven build lists). `gcc -MM -I host ... tests/<fixture>.c` lists a
  fixture's include closure without compiling. The suite shows a missing file only as a link error.
- The fuzz test (`tests/test_coop_fuzz.py`; its docstring lists the smoke, `--campaign` and `--replay`) stops each
  harness at its first crash, so a campaign that ends early found something.
- `python3 -I` leaves the script's own folder out of `sys.path`: a module placed beside a script must be imported after
  `sys.path.insert(0, HERE)`.

## The Windows-target syntax check

`BASE` is a git revision (the checker runs `git diff BASE -- tests`), not a worktree path. Exit 0: every captured compile
passed; 1: a failure; **2 (`INCOMPLETE`) is normal** whenever a changed driver skips Windows (`RUN_ALL_SKIP`), runs a
Python script or never reaches a compile call: those are listed as `UNJUDGED`. The checker answers success to every
captured compiler call, so it cannot judge a driver whose assertions need a compile to fail (a driver such as
`tests/test_option_ids.py` shows a failing unit for its deliberate negative case): judge that one with a real mingw
compile and say so. It finds drivers by the source files they read, so an unchanged driver of main's that reads a file
the branch changes joins the run; a failure main has too is inherited: run the checker on a worktree of the base
(`--test tests/<name>.py`) and keep both rows.
