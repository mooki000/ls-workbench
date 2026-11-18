---
name: ls-perf-atlas
description: >
  Use when asked to find where a game area costs frames and why: a heavy place (Castle Town, Kakariko, Hyrule Field, a dungeon with
  effects), "go through the hard-to-render areas", price a part of the frame (people, animals, effects, shadows, resolution, culling),
  rank optimization candidates, or add an area to the suite. Viewpoints and walks, the actor tools and render switches as
  attribution, the cost table, the coverage matrix, and how a proposed optimization is proven. Read ls-workbench and ls-perf-compare first.
---

# The perf atlas: where a frame goes, area by area

**Use for:** a map of where frames are lost and what each part costs, and the work that follows from it. It finds *where to
look* with ordinary runs (counts, shares, pictures) and *proves* with the paired exclusive worker. It is not a second timing
runner: every speed claim stays with `ls-perf-compare` and `ls-regression-hunt`. Read `ls-workbench` (paths, gates, windowless runs),
`ls-perf-compare`, `ls-scripted-play`, `ls-field-data` and `ls-hold-chain` first. Set `H` and `P` as `ls-workbench` does.

The code is `play/perfatlas/` (live: `playthrough/perfatlas/`); its tests use a fake game: `python3 -I -B play/perfatlas/test_perfatlas.py`.
`ATLAS="python3 -I -B <folder>/perfatlas/atlas.py"`; `ATLAS --help` lists `check`, `plan`, `merge`, `hold`, `table`, `coverage`.

## 1. What counts as a result

| Level | What | How it is obtained | May be said |
|---|---|---|---|
| observation | counts (draws, passes, barriers, uploads), pass shares, CPU-versus-GPU split, pictures | ordinary gated run, `atlas.py hold` | "ordinary run, no speed claim"; never a frame rate as a verdict |
| valid observation | a window that was **as asked** (arrived, camera at the aim, still, no event, the setting taken, frames present): `counts`; and also **quiet** over its own span (other work under 3 cores mean, at most 6 in any 5-second stretch, from the CPU record): `timing` | `atlas.py table` marks every row `timing`, `counts` or `NO` | frame ms and CPU ms are quoted from `timing` windows only; draws, passes and the split from `counts` windows; a `NO` window is not used |
| lead | a part that looks expensive, ranked | the cost table | where to spend an exclusive hold |
| priced | what a part costs at a viewpoint | exclusive paired run, off versus on, A B A B | the gap with its same-arm spread |
| proven | an optimization is faster | exclusive paired run, main versus main plus the change, same viewpoint, same settings, pictures equal | the paired result and the rows |

Quiet rule, bands set before the results, every invalid row kept, one waiter: `ls-perf-compare`, section 2. A scene with moving
crowds can fail the 5% same-arm tolerance (an earlier exclusive Market hold had 7.35% between repeats): run the first pair of a scene
**with the same build on both arms** (a null pair). Its spread and gap say whether the scene can decide anything before a real change is spent on it.

## 2. The tools that exist, with what each is good for

| Tool | Use | Switch or command | Known limits |
|---|---|---|---|
| Control bridge | place, aim, wait, pictures, settings | `HOS_CONTROL=<socket in a 0700 folder>`; the control client (`$HS_CTL` of `ls-workbench`: `status`, `press`, `stick`, `shot`, `option get\|set`, `wait`); `status.game`: `pos`, `facing`, `camera`, `event`, `loading`, `age` | no time of day; samples stop while a map loads; `teleport` lands at the destination's start point only |
| `!pos X Y Z` | stand at an exact place (metres, the numbers of `status.game`); arrived to the centimetre in 8 of 8 windows of the first hold | a line in the `HOS_INPUT_LIVE` file | PR 1351 (was `!place`); since its current head `!pos` takes exactly X Y Z (facing and camera were dropped on purpose: the longer form did nothing in the game, the facing logged as turned and recomputed the next update, the camera hook unable to read the view): turn the camera with the right stick instead |
| Camera aim | look in a direction | right stick through the bridge (`stick` with `"stick":"R"`), closed loop on `status.game.camera` (0 toward +z, 90 toward +x): the first push learns the handedness and rate, as hsap's `camera to` does (`driver.aim`) | the camera heading is the only thing to aim: Link's own heading does not matter to what is drawn; `camera_pitch` reads back |
| Actor tools (PR 1426) | take people, animals, effects, breakables out of an area | `!actors off\|on <map\|here\|*> <glob> [force]`, `!actors clear\|list\|reload`; `HOS_OOT_ACTORS_OFF=<map>:<glob>[!],...` at launch | applied at the area's **next layout read**; `reload` is the Teleport list's change to the same map (puts Link at the entrance, refused in an event or within seconds of a load); people tied to a door or an event and a behaviour group's leader stay (a street vendor and some guards stalled the game when removed): the left-out count is in the log line `actors: <map>: ... left out` |
| `!cheat N` | time of day, teleport | `100+h` sets hour h; `1000+i` teleports to destination i | `!pos` is not taken in the same update as a pending `!cheat` |
| FPS counter | the frame's parts on screen | option `fps_counter` 4 = Breakdown, 5 = Dev (draws, dispatches, passes, uploads, GPU slot waits) | headless needs `RX_VK_SHOT_MENU=1` to draw it; the same parts are the frame trace's columns |
| Frame trace | per frame: interval, game, render, GPU wait, pace, waits, the main thread's time (on CPU, blocked, waiting for a core), draw/descriptor/command ms | `RX_VK_FRAME_TRACE=1 HOS_PROFILE_DIR=<folder>` writes `<folder>/frames.csv`; `at_ns` is the host clock (`time.monotonic_ns()` on Linux) | draw/descriptor/command ms time one draw in eight unless `RX_VK_PROF_ALL=1` |
| `/proc` sampling (`cpu.py`) | the game's CPU time a frame by thread group, the cores of everything else (the quiet record) | the driver snapshots `/proc/stat` and `/proc/<game pid>/task/*/stat` while it stands; the pid is found by the `HOS_CONTROL` in the process's environment | ticks of 10 ms: a window needs at least a few seconds; threads that end inside a window are missed. **CPU ms a frame also rises on a busy PC** (89 ms at 3 other cores, 144 ms at 29: spinning and waiting count), less than wall time but not constant: compare only windows whose quiet flag is met |
| Pass timing | GPU time per pass, program and copy | `RX_VK_PASS_TIMING=1\|2`, logged every 600 presents, no file | every span waits for the GPU: spans add up to more than an unprofiled frame, read shares only |
| Scene lines | draws, dispatches, passes, barriers, uploads, textures, GPU time, replay ms by part | always on, every 120 presents | averages of the last 120 presents |
| Draw statistics | one frame's draws by program | `RX_VK_DRAW_STATS=<folder>`, touch `<folder>/draw_stats.now` | one frame |
| `HOS_PROFILE=1` (cpu.csv, all-thread sampler) | where the CPU goes | `scripts/profile_report.py <folder>` | it switches on pass timing and a 2 ms sampler: it perturbs timing, never time with it |
| Perf summaries (#1383) | per-map, per-second frame and GPU ms | player telemetry | position and angle fields are wrong (bin middles, wrong object and units), cutscenes sampled: a coarse survey only |
| Perf monitor (`perf.csv`) | sampled GPU/CPU/memory | `--perf` | the writer is not on main: a main build writes no file |
| Dispatcher | priorities of queued waiters | `reports/tools/gpu_priority.py` (the main session's): a waiter is ranked by the first `ORDER` text found in its working directory or command line; **a new topic needs its own entry** or it ranks 10, behind everything; a waiter whose command line has `nx2navi-gpu-slot --exclusive` stays stopped until 22:00 (night only) | never CONT a waiter; kill only a recorded pid |
| Regression worker | paired timing | `regress.py hunt` or `_hold` with a scenario | `ls-regression-hunt` |
| Route library, pilot, BugHunter | walks, stuck places; detectors flag present gaps and visual faults | `ls-scripted-play` | no perf cell in PlayBench; the Go-Explore archive is unmerged |
| Field data | where players lose frames | `ls_field.py perf --by map`, `hitches --by map` | `ls-field-data`: sessions and hours beside every figure |

No setting turns shadows or draw distance off: shadow size is a menu row, there is no draw-distance switch. Price the passes with pass timing instead.
Dynamic resolution and the Auto preset are on by default: a fixed-cost run sets `HOS_DYNRES=0 RX_VK_AUTO_TUNE=0 RX_VK_AUTO_PRESET=0`
(the atlas adds them, with `HOS_OUTPUT_RES=2048x1152 HOS_FPS_MAX=240 RX_VK_DLSS=0 RX_VK_FRAMEGEN=0` and no periodic pictures).

## 3. The perf tools game (attribution only)

The actor tools, `!pos` and the overlay are not on main until their pull requests merge. For attribution build one owned copy that has them:
a worktree on forge main, merge PR 1426, PR 1358 and the `!pos` branch, resolve the API-number and docs conflicts (each takes a number:
the renderer table grew by 1358 and the host table by 1426; keep both numbers), and build with the donor shortcut of `ls-workbench`
("Incremental host/renderer-only changes"): reflink a donor's `work/build` (same `nx2/` apart from the actor patch) and its built copy
without `runs/` (warm caches: the translator stamp is the same), then `make_game.py` again. Run it through the idle gate
(`hs-idle.sh`), the full-build lock and the build gate; it took 32 seconds. **Never use this copy as a timing arm**: a timing comparison
of an optimization is main against main plus the change, both built the same way. Delete the worktree and copy when the pull requests merge.

## 4. The suite

**Viewpoint:** a place to stand still: position, facing, camera yaw and pitch, time of day, dwell. **Walk:** ground points walked in a
closed loop (the stick angle is the camera's yaw minus the bearing, handedness checked as Link moves). **Condition:** one change to a
block of a run: `actors` (rules, then a reload), `option` (live, put back), `time` (hour, then a reload), `env` (launch switches, so a
process of its own). The catalogue (`atlas.py check` lists it) has each actor family off, all offered families off, the render
resolution, shadow size, anisotropic filtering and upscaler rows, real occlusion queries, culling off, a small texture budget, the mesh
path off and pass timing. Libraries and results stay in local files outside every repository: they hold places.

```sh
$ATLAS check library.json
$ATLAS plan library.json --area <key> --tod day --viewpoints a,b,c --conditions baseline,actors-people,actors-animals --prefix a --out plan-a.json
$ATLAS plan library.json --area <key> --tod day --viewpoints a,b --conditions pass-timing --min-frames 1300 --fps-floor 25 --dwell 20 --prefix p --out plan-p.json   # (conditions that are all launch switches get a process each, no baseline process unless 'baseline' is named; --fps-floor turns a frame count into seconds on a loaded PC)
$ATLAS plan library.json --sweep --area <key> --tod day --viewpoints p01,p02,p03 --yaws 0,60,120,180,240,300 --dwell 2 --prefix s --out plan-s.json
$ATLAS merge plan-a.json plan-p.json --hold-limit 1100 --out plan.json    # one admission for several runs
$ATLAS hold plan.json --hold h1 --copy <owned copy> --out <new folder> --hs-run <path>/hs-run.sh --saves <folder> --hold-seconds 1060
$ATLAS table <out>/<run> ... --library library.json --md cost.md --json cost.json
$ATLAS coverage --field field.json --areas areas.json --cells cells.json --next 8
```

A plan blocks the runs as: baseline, each condition, baseline again (the first and the last block give the **revisit drift**: the same
place after several area reloads), at most `--run-budget` seconds a run (boot 60 s, reload 25 s, 3 s arrival, settle, dwell; estimates
tuned by the first results). `hold` is ordinary-run machinery: it needs the queued waiter's locks (one waiter, `ls-hold-chain`; start it
from a folder named for the topic, freeze `perfatlas/` beside the plan, receipts `admitted.txt`, `hold.log`, `exit.txt`, `out/`). It
starts the game through the run wrapper (which asks the GPU gate itself), makes a private control folder, drives the ops, ends the game
by pid only, and leaves per run: `windows.jsonl` (one record per measured stretch with the host-clock start and end and the log's byte
offsets), `ops.jsonl`, `game_log.txt`, `trace/frames.csv`, `present.csv`, `summary.txt`, `run.json`, `shots/`.
A run is 450 s at most, boot included; an ordinary hold may be longer when the main session grants it, with a time-left check before each run.

**Choosing viewpoints** (local script; the method, not the code): the nav mesh's largest connected component gives places Link can
reach (one face centre in each 6 m cell); the placement files give what stands around (people, animals, effect locators, geometry, other
objects); each place is scored at eight facings by what lies in a 90 degree cone within 80 m, weighted by kind and distance. Take the
top places by people (crowds), by distant geometry (vistas), by total (dense), by animals and effects, the middle of the walkable area,
and the **lowest** score that still has geometry in front (a cheap reference). Keep them 22 m apart. Confirm each by its counts in the first
run: the score is a proxy, the scene line's draws are the truth.

**The first hold's lessons (Castle Town by day and by night, 24 windows, load average 33 to 96, quiet rule not met):** wall frame ms moved with the other
agents' work (26 to 80 ms at the same view; the night run was "faster" only because the machine was calmer), so on a busy PC a window is an observation of
the machine. What stayed readable: the counts (draws, passes), the pictures, the game's split (the game part was 95% of every frame; the renderer's
part 0.3 to 1.1 ms; GPU 4 to 9 ms; the main thread was blocked 74 to 80% of the frame waiting for the job graph and on CPU only 5 to 10 ms) and, with
the CPU record, the **CPU ms a frame by thread group**, which does not move with other work the way wall time does. Use ordinary windows for counts,
pictures, CPU per frame and the split; keep every frame-ms comparison for the exclusive worker.

**Hold 3 (ordinary, render rows at two places): not quiet, counts only.** All ten windows ran beside 20 to 28 other cores; the Render resolution row at 33% was
**refused** with the game's own upscaler (`failed`: its arrow steps do not reach 33 there), so that block ran at the earlier value; the GPU pass timing run
waited ten minutes in the GPU gate behind another agent's 27-minute game and the hold was ended by its owner (never run: pass timing, launch switches).
Draw counts at one view moved 2,494 to 2,686 between windows (1 to 3 scene lines inside a window): a difference under about 150 draws is not a price.
A hold that waits for the gate holds both locks: `atlas.py hold --gate-wait` (default 150 s) ends the run and the hold when no game log appears.

**What a quiet window showed (hold 2, two windows with the quiet rule met, ordinary, no speed claim):** the square view at 2048x1152 on this PC read 16 ms and
14 ms a frame (60 to 70 fps; the field's Castle Town sessions average 34 fps on other hardware); the actor executors (6 threads) took 38 of the 89 CPU ms
a frame, the module workers 14, the renderer's replay and recorder threads 11 and 10; the main thread was on CPU 4.6 ms and blocked 12.4 ms. With people off the next
quiet window read 14.7 ms and 79 CPU ms (the executors 34): about 10% less at that one view, one unpaired pair, a lead for the exclusive worker.

**Places:** a place picked from the nav mesh can be inside a shop or against a wall (5 of the first 8 pictures were); pick from where Link has walked (the route
library's tracks), then let a **scout** choose the views: `plan --sweep --yaws 0,60,...` stands at each place, turns the camera to each yaw and reads the
bridge's `draws` (a count: it does not move with the machine's load). Look at the top views' pictures before keeping them.

## 5. Reading the cost table

Read the **valid** column first (`timing`: as asked and quiet; `counts`: as asked, not quiet or not recorded; `NO`: not as asked) and the header line that
counts them. Only `timing` rows feed the family prices' frame, game, render and GPU ms and the drift of frame ms; draws are averaged over `counts` pairs. A
run made before the driver recorded refused settings gets them back from its ops record. Every row is a window. Frame ms is the renderer trace's interval (headless: CPU after the offscreen submit). `game`, `render`, `gpu wait`,
`pace` are the FPS counter's split; **bound** names the longest of them (`pace` when pacing is over 30% of the frame). Draws, GPU ms
and the replay parts come from the scene lines inside the window. The pass table is the clean pass-timing blocks (the first block in a
window covers earlier frames and is dropped; a window that holds one block is marked partial). The family table is the mean difference of
a condition to **its own run's** baseline (cross-run deltas are marked and never averaged in). A window is flagged when it did not arrive,
the camera is off its aim, Link moved over a metre, an event ran, or it has fewer than 120 frames.
Read with the field: where players lose frames the game's own split puts most of the frame in `game` (about three quarters in the Castle Town sessions): check whether the
window agrees before spending an hour on the renderer.

## 6. Pitfalls found on the way

- The actor tools remove only what the layout offers; geometry, doors, sensors and tied people stay. A table of "people off" is the tool's partial answer: quote the left-out count beside it.
- `!actors reload` puts Link at the entrance, so a viewpoint follows each reload with its `!pos`; two reloads inside seconds are refused.
- Pictures: take them after the window (the bridge `shot` costs a frame); periodic pictures off (`RX_VK_SHOT_EVERY=1000000`).
- Pass timing and `HOS_PROFILE=1` change the frame: run them apart from the numbers you quote as frame ms.
- Auto-tune can change the render resolution during a run: fixed-cost env always.
- A new copy starts cold (4 to 7 minutes of pipelines): reflink a warm copy's caches only when the translator stamp is the same.
- Path-valued switches (`RX_VK_FRAME_LOG`, `HOS_PROFILE_DIR`, `*_FILE`) go in the real environment, not the env file.
- The field is a few players' evenings and its figures are averages under fps caps: use it to rank, never to price.
- One bridge fault ends a run on purpose (an uncertain input is not repeated); a refused or late step is recorded and the run goes on.

## 7. Prove it: the exclusive paired hold at a viewpoint

The ordinary holds find and rank; only the worker decides. For one viewpoint (`timing.py`, `scene.py`; nothing else times anything):

1. **A save for the place.** A build on main has no `!pos`: make a save that starts Link at the viewpoint (`hs_make_save.py --from <entry> --out lib:<name> --start <Map>:x,y,z,yaw --time day`), prove it boots to that place (a start within 3 m of a person or 2.5 m of an object never reaches play), and let the **scene driver** turn the camera to the viewpoint's heading with the right stick before the window. The same scene then runs on any build.
2. **The scenario** (`timing.make_scenario`): fixed-cost env, `anchor: first`, a window of `from`..`to` seconds on the frame clock (the aim must be done two seconds before `from`; the game starts presenting 6 to 12 s after its start and the wall limit counts from the start, so `to` must be at least 12 s below `seconds`: 45 to 95 with 110-second runs fits four runs in 470 s), `atlas_scene` (map, aim yaw, where Link must read, control and ready deadlines), `validator_command` = `scene.py validate {output}` (a run whose camera moved, whose Link was elsewhere, that had an event or a load, or that was not controllable in time is **invalid**, not slow). The decision measure (`mean_ms`) and its bands `good_max < bad_min` are required arguments: write them before any result.
3. **The plan** (`timing.make_plan`): reference, candidate, reference, candidate; fit rule `n * (seconds + grace) + 10 <= 480`; the desktop widget's avoid window; new output folders. The queue: quiet-window lock, measuring lock, then the gate with `--exclusive` and the worker's `_hold` (`ls-hold-chain`, `ls-regression-hunt` section 3), only with the main session's word for that one hold.
4. **The first hold at a scene is a null pair** (`timing.null_pair_plan`: one copy, one commit, all four runs) and its verdict (`timing.null_pair_verdict`): usable when all four runs are valid and not disturbed, both same-arm spreads are at most 5% and the arm gap at most 3%; otherwise the scene cannot decide a change smaller than the larger of those, and the hold is kept as a result (an earlier Market hold had 7.35% between repeats). Every row is kept.
   **The dry run comes first, in an ordinary hold:** the same scene, scene driver, adapter and validator as a `game-check` (pass/fail, relaxed control deadlines) on the stamped
   copy, with a picture of the save's place from the atlas driver (`plan --save-proof`, op `stand`: no `!pos`, Link's place read back). It checks what an exclusive hold must not
   discover: the save boots to the place, the stamp is in the log, the scene is controllable and aimed in time, the frame clock covers the window. Its record names the real times.
5. **The timing base** is a copy built by the shared build adapter from a donor with the same lift key **and** a host that links (a key match was not enough once: the donor's
   generated stubs and a newer `host/` source both defined the same imports and the link failed; build from the donor nearest the target commit, or a full lift). Stamp and
   binary hashes go in the receipt; the perf-tools copy is never an arm.
6. **Then** a pricing pair (a part off versus on, one build, a launch switch only) and proof pairs (main versus main plus the change, both built the same way; the perf-tools game is never an arm).

## 8. Add an area

1. Name the map and its saves: `hs_make_save.py --from <entry> --out lib:<name> --start keep|<Map>:x,y,z,yaw --time day|night` (a new save is untried until a game boots from it).
2. Write the library entry (`areas`, saves by time of day), pick viewpoints as above, `check` it.
3. Plan one baseline run with 8 viewpoints, queue it, read the table: drop the viewpoints that did not arrive or sit inside the cheapest decile.
4. Plan the attribution runs (families, settings, one process each for launch switches) and pass timing at the top three viewpoints.
5. Fill the coverage cells (`planned`, `observed`, `priced`, `finding`) and rank the next ones with `coverage --next`.
6. Rank optimization candidates by price and by where in the code they would go (`ls-function-map` first); each gets an expected gain and a proof plan
   (viewpoint, settings, null pair, A B A B hold, count of holds) before any code.

## Status

The driver and the cost table are tested against a fake game and made-up logs. Hold 1 (three runs, 24 windows, ordinary) ran on the real game: the
driver arrived, reloaded areas with actor rules (people off: 16 left out, animals off: 14, no stall) and took pictures; viewpoint facing and camera did not
apply (above), and the PC was busy. Hold 2 ran the camera aim (6 of 6 sweeps and all 10 windows reached their yaw within 10 degrees), the sweep, the actor families with their left-out counts
(people 16, animals 14, all offered 111) and the CPU record, which also says which windows were quiet (the first two were: other work 2.7 cores mean,
2.8 peak). The PC was then busy again (others up to 30 cores) and the later windows are not comparable.
