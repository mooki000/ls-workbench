# play/: the step drivers, the pilot and its benchmark

Everything here plays a game that is already running on your machine. Nothing in it is game data, and nothing in it
needs a network except the one optional model backend (below). The game, its saves and every file made from them stay
yours and stay local.

| Path | What |
|---|---|
| `hsap.py` | The game side. Starts one game through `hs-run.sh` (the GPU gate, headless) and plays steps on it through the control bridge: walking, timed presses, waits, the interaction steps (`interact`, `chest`, `talk`, `advance`, `pickup`, `enter`, `faceat`, `ztarget`, `camera`, `ocarina`, `item`, `rendezvous`), watched flags, the sampled state (`oot` subscription). Every step ends with a result word and what changed. `python3 -I -B play/hsap.py -h` lists the steps. |
| `hsap_file.py`, `tapcheck.py` | The earlier file-tap driver, and the helper that puts a run's samples beside its pictures. |
| `pilot/pilot.py` | The pilot. Plays one *segment* (a TOML file of goals with targets) by deciding, whenever the game side has no step left, which candidate step comes next. Writes a transcript, a summary and a route file per run. |
| `pilot/deciders.py` | `rules` (a fixed policy, no model, no network), `replay` (a recorded transcript) and `openai-decisions` (a model chooses among the candidates; an optional planner call asks for route help). |
| `pilot/interact.py` | The interaction goal kinds (`open_chest`, `both_open_same_chest`, `collect`, `talk_to`, `enter`, `play_song`, `fight`, `explore_interact`): their candidates, their rules policy, their conditions. |
| `pilot/recover.py`, `pilot/humanize.py` | The stuck ladder, the per-map bad-point memory and the guard in front of the decider; seeded human-like movement (`--humanize 0..3`). |
| `pilot/vision.py` | The pilot's eyes: at chosen moments a fixed set of questions about the game's own picture goes to a vision model (`--vision on`). |
| `pilot/metrics.py` | The numbers of a run, from its own files: stuck steps per km, time to each goal, decisions per minute, cost per minute and per goal, interactions, looks. |
| `pilot/openai_api.py`, `pilot/standin.py` | The one place that talks to the API (key, caps, ledger), and a loopback stand-in server for tests. |
| `pilot/follow_feed.py` | Keeps one pilot's follow file from the other pilot's state journal (two pilots in one co-op room). |
| `pilot/tactics.py`, `pilot/tactics_seed.py`, `pilot/lessons.py` | The tactics library (ways out of a stuck place, keyed by the cause of the block, ranked by how often each worked, every attempt written back with its context), the seeding of that library from earlier runs, and the few short lessons put into a planner's request. |
| `pilot/routelib.py`, `pilot/routes.py`, `pilot/frontier.py` | The route library: what every run learned about where Link can walk, kept per map; its command line; and the frontier push that starts from the farthest walked point and tries the alternatives a stuck cause suggests. |
| `pilot/selfplay.py` | The nightly self-play hold: it puts the pilot a few metres before the open stuck places of the route library where the tactics library knows least and lets its ladder try what has been tried little there. No model, no human. |
| `playbench/` | The capability scoreboard: a fixed set of small cells, the arms that play them, a referee that judges on the game's own state, a results store, a trend table and chart, and the nightly job. Its own `README.md` has the details. |
| `bench/` | The benchmark course and its runner: `course.py` (entries and arms), `mkcourse.py` (cuts the entries' segments from your own), `ghost.py` (a leader that walks a route for the follow entry), `run.py` (`plan`, `play`, `report`, `sheet`). |
| `test_*.py`, `pilot/test_*.py`, `bench/test_bench.py` | The game-free tests. |

## What you supply, and what stays private

The repository has no segment, no save, no route, no world model, no song or prompt list and no key. You make or
supply these locally; the code finds them next to itself or through the environment, and the folder's `.gitignore`
lines keep them out of a commit.

| Private input | Where the code looks | Without it |
|---|---|---|
| A built game copy (a folder named the way `harness/hs_names.py` names copies) and its save library (user folders) | `--copy` / `PILOT_COPY`; the segment's `save` (a library entry, a user folder, or `kept:<tag>`) | nothing to play |
| Segments (`pilot/segments/*.toml`) | the path you give `pilot.py run` | nothing to play |
| An offline world model (`world/`: objects per area, a planner) | `PILOT_WORLD`, else `world/` beside `play/` | no `near` objects, no planned routes: straight lines and the prompt only |
| Prompt words the game really uses | `pilot/prompts.json` or `PILOT_PROMPTS` | the generic verbs only |
| What the benchmark's six placeholder entries stand for (your segment, its goals, a save, a scripted route, a leader's route) | `bench/course-local.json`, or the file `PILOT_BENCH_LOCAL` names: `{"<entry id>": {"source": ..., "goals": {...}, "save": ..., "ghost_source": ..., "ghost_goal": ..., "tas": ...}}` | the cells are `MISSING` in `bench/run.py plan` |
| Songs (name to note buttons) | `pilot/songs.json` or `PILOT_SONGS` | a goal names its notes itself |
| The caps file `pilot/caps.json` | the pilot's own folder | `--decider openai-decisions` says so and the run plays on the rules |
| The main lane's lock files (for `selfplay.py` and `frontier.py` when they queue a hold) | `PILOT_QUIET_LOCK` and `PILOT_MEASURING_LOCK`; no default | the waiter is refused, naming what is missing |
| The route and tactics libraries (`routelib/`, `tactics/`, the self-play folder) | `ROUTELIB_DIR`, `PILOT_TACTICS_DIR`, `PILOT_SELFPLAY_DIR`, else beside the harness | the pilot plans with the world model alone and learns nothing |
| The scoreboard's private settings (game copies, locks, folders, key file, tracker address, cells with their saves and proofs) | `playbench-local/` (`playbench/README.md`) | the scoreboard refuses, naming what is missing |
| The key | `OPENAI_API_KEY`, or a mode-600 one-line file named by `PILOT_OPENAI_KEY_FILE` (default `$XDG_CONFIG_HOME/ls-pilot/openai.key`) | `rules` and `replay` do not need one; `--decider openai-decisions` says `no key` and the run plays on the rules |
| Run output (`pilot/runs/`, `pilot/ledger.jsonl`, `pilot/cache/`, `bench/runs/`) | made by the tools | |

Transcripts, pictures, ledgers, bad-point files and cut benchmark segments are game-derived or machine-private: keep
them out of commits and posts.

## Running the pilot

A game-free check that everything imports and the tests pass (no game, no network, no key):

```sh
cd play
for t in test_hsap.py test_hsap_file.py pilot/test_pilot.py pilot/test_interact.py pilot/test_recover.py \
         pilot/test_eyes.py pilot/test_metrics.py pilot/test_tactics.py pilot/test_tactics_seed.py pilot/test_lessons.py \
         pilot/test_routelib.py pilot/test_frontier.py pilot/test_selfplay.py playbench/test_playbench.py \
         bench/test_bench.py; do python3 -I -B "$t" || break; done
```

`pilot/test_pilot.py` runs most of the others; a duel-segment check there is skipped when `pilot/segments/duel*.toml`
(private) are absent, and says so.

One segment, with the fixed policy (no model):

```sh
python3 -I -B play/pilot/pilot.py run <segment.toml> --decider rules --tag <new tag> --copy <game copy> --save <user folder> --wall 400
```

A tag is never reused (`pilot.py` refuses an existing `pilot/runs/<tag>`). The launch asks the GPU gate itself: do not
wrap it in another one, and keep a hold within the workbench's limits. `--wall` counts from the game's start; a segment's
`budget_seconds` counts from Link being in control (`gameplay_after_s` in `summary.json` says what the start-up cost).

With a model:

```sh
export PILOT_OPENAI_KEY_FILE=<a mode-600 file with the key>     # or: export OPENAI_API_KEY=...
python3 -I -B play/pilot/pilot.py run <segment.toml> --decider openai-decisions --planner --tag <new tag> --copy <game copy> --save <user folder>
```

What leaves the machine, and only with `--decider openai-decisions`: the request bodies written to the run's
`transcript.jsonl` (the state line as JSON, the goal and target names, what was tried, the candidate steps). With
`--planner` the planner's questions too; with `--vision on` the game's own frame, downscaled, with one fixed instruction
text and fixed questions: no path, log line, flag or coordinate. The key is read at the moment of each request, sent
only to the API host, scrubbed from error texts and never printed, logged, written to a transcript or put on a command
line. A stand-in server (`--api-base http://127.0.0.1:<port>/v1`, `pilot/standin.py`) never gets the real key.

### Caps and the ledger

`pilot/caps.json` (yours to write; the pilot reads it before every request and stops asking when a limit is reached):

```json
{"per_run_usd": 3.0, "per_night_usd": 50.0, "total_usd": 4000.0, "planner_share_of_night": 0.4,
 "model": null, "planner_model": null, "vision_model": null, "vision_backend": null}
```

The limits above are the ones the pilot is run with (3 USD a run, 50 USD a night, 4,000 USD in all): a run stops asking at its own
cap, a night at the night's, whoever spent the rest. They sit above what the benchmark measured (about 0.003 USD a minute
for the cheaper arms, 0.08 USD a minute for the flagship model, 1.64 USD for the dearest 13 minute run). There is no built-in
default: a tree with no `pilot/caps.json` plays on the rules.

Every request is one line of `pilot/ledger.jsonl` with the token counts the API reported and the cost computed from them
at the prices in `openai_api.py` (named `Price`s; update them when the published prices change). `pilot.py ledger [--run <id>]`
prints what a run spent. `PILOT_PLANNER_MODEL`, `PILOT_VISION_MODEL` and `PILOT_VISION_BACKEND` override the file for one run.

### Interaction goals, recovery, human-like movement, eyes

* Goal kinds, the prompt the game shows (`Open`, `Talk`, `Check`, ...) as the sure answer to "would A do something", and
  the near probe from the world model: the docstring of `pilot/interact.py`. A goal is met by the game's own evidence (a
  flag, a rupee or item count, a map change, the battle music, a message read to its end), never by a button having been
  pressed; a `play_song` goal that names a `flag` is met by that flag only.
* `recover.py`: a move that ended stuck is answered without a question by a ladder (sidestep, back up and come from an
  angle, roll, hop, next route point, local re-plan); where it happened is remembered per map and later routes bend round
  it. A guard in front of the decider takes the one valid candidate without asking, does not ask again about a state that
  did not change, and turns a long run of identical answers into recovery. `--no-recover` gives the older pilot.
* `humanize.py`: `--humanize 0..3` (default 2 for an exploration run; a segment can set its own; 0 sends every line as made),
  seeded by `--humanize-seed` or the run id.
* `vision.py`: `--vision on [--vision-every <s>] [--vision-backend decisions|responses] [--vision-model <m>]`, default off.
  The pictures sent are kept in the run's `vision/` folder with one row per look.

### Learning from earlier runs: tactics, lessons, the route library, self-play

* **Tactics** (`tactics.py`): the recovery ladder's rungs are entries of a catalogue, each applicable to some causes of a block (a ledge, a
  person's text, a crowd, a door's wrong side, a wall, ...). `recover.py` asks the library for the order to try them in; every attempt is
  written back with the map, the age, the cause and the geometry round the place. The score is `(wins + prior * K) / (attempts + K)`, each
  attempt weighted by how alike its context is, the prior being the static order's scaled by the cause's pooled success rate: an empty library ranks as the
  fixed ladder always did. `tactics_seed.py` fills the library from earlier runs (`seed`) and measures it offline (`evaluate`, leave one run out).
  `--tactics ranked|fixed|explore|off`, `--tactics-readonly`, `$PILOT_TACTICS_DIR`.
* **Lessons** (`lessons.py`): before a planner call, the few most similar past blocks and what finally worked there, as short sentences in a small token
  budget (`--lessons on|off`, `$PILOT_LESSONS`; `$PILOT_LEARN=off` switches both off, so a scoreboard arm can name its own switches). Off, or with a library that
  has seen nothing like the call, the request is exactly what it was before.
* **The route library** (`routelib.py`, `routes.py`): tracks, verified edges, stuck points with a cause, map links and the targets' frontier, kept per map
  in a private folder (`$ROUTELIB_DIR`); with it set, `pilot.py` plans with the library's known way first and imports its own run when it ends.
  `routes.py` imports finished runs, shows what is known and prints a route; `frontier.py` plans, checks and runs a push for a target
  (and queues its one waiter); both go through the game copy and the gate like any other run.
* **Self-play** (`selfplay.py`): `plan`, `check`, `saves`, `run`, `queue`, `stop`, `report` and `nightly`. A hold is at most 1,100 s, one session after another,
  each a `pilot.py run` with the rules decider; every attempt goes into the tactics library as source `selfplay`. It refuses a copy that is not warm or not
  stamped, a spend over the caps and a second waiter beside a live one.

Game-derived data (maps, places, saves, the libraries) stays in the private folders; the code names none.

## The benchmark

`bench/course.py` names six entries (walk, hard route, door-talk, follow, chest, fight) as cuts of segments *you* have (placeholders until `course-local.json` says which),
and four arms: `rules`, `decisions`, `decisions+vision`, `decisions+vision-big`. `bench/mkcourse.py` makes the cut segments
into `bench/segments/` from your local `pilot/segments/`; `bench/run.py` plays the cells (one game copy per cell at a
time, two copies for two at once), writes `results.json` and `table.md` (quality and cost per minute, per goal met and per
km for each entry and arm) and contact sheets, and never starts a cell that cannot finish inside `--hold-s`. It does not
take the quiet-window or measuring locks: the hold that runs it does. Wall-clock cells are functional runs, not frame-time
measurements.

```sh
python3 -I -B play/bench/mkcourse.py --segments <your pilot/segments> --out play/bench/segments --bench-id b1
python3 -I -B play/bench/run.py play --out <new folder> --bench-id b1 --copies <copy-a>,<copy-b> \
    --pilot-dir play/pilot --segments-dir play/bench/segments --arms rules,decisions --hold-s 1500 [--tas]
python3 -I -B play/bench/run.py report --out <that folder> --segments-dir play/bench/segments
```
