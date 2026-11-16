---
name: ls-hold-chain
description: >
  Use before queueing a game run, build, suite or proof behind the quiet-window and measuring locks, when a hold is
  queued behind other workers, or when a base moves while you wait: one admission for many stages (chain.py, queue,
  waiter, flock, "admitted", hold of 480 or 1,100 seconds), the lock order and modes, the EDITING/READY markers that let
  a queued waiter be retargeted, state T, how to see the queue and find your own waiter's pid, and how a session waits
  for a hold that may start hours later.
---

# Queued holds: one admission for many stages

The quiet-window queue held 8 to 17 waiters most of 14 to 15 November: a short run cost 22 minutes to over 3 hours
before it started, and a second hold costs a second full wait. Nineteen workers wrote their own `chain.py` for this.
Start from one of theirs, not from nothing.

## 1. Plan: one hold, code and tests first

1. Finish and commit the code and run its game-free tests **before** queueing. A stage that asserts a clean tree blocks
   all editing of the worktree while it waits; build from a detached copy of the commit if you must keep editing.
2. Put every run that does not need an earlier run's answer into **one** hold: build, warm caches, pair session, suites.
   A failed build skips what depends on it; the later independent stages still run (the hold is paid for). If each
   result chooses the next run, say so in the hand-off: several short runs are several waits.
3. Check the pieces without a lock first (`check-only.py`, `prepare.py --check` in the folders below): trees match the
   head, scripts parse, files exist. A mismatch found after admission costs the whole wait.

## 2. The locks (a convention; no script enforces it)

```sh
QUIET=/tmp/longshot-quiet-window.lock       # always exclusive, taken first: every hold, timing run, build or suite that needs
                                            # the machine quiet
MEASURING=/tmp/longshot-measuring.lock      # exclusive when any game runs; -s (shared) for a CPU-only job or an ordinary run
```

Also in use: `/tmp/hookshot-full-build.lock` (`flock` in front of `hs-build-slot`, no `-w`, for a full build) and
`/tmp/longshot-whole-suite.lock` (the whole suite; `reports/tools/verify_heads.py`). One nested command, quiet lock
outermost:

```sh
flock --verbose -w 14400 "$QUIET" flock --verbose -w 14400 [-s] "$MEASURING" CHAIN-OR-GATE-COMMAND
```

- A shared taker passes a waiting exclusive one on Linux, so a CPU job that wants the measuring lock shared takes the
  quiet lock first: `flock -w 14400 "$QUIET" true && flock -w 14400 -s "$MEASURING" CMD`.
  Do not chain shared holds: a second shared `flock` of yours is granted at once while the first is held, which keeps the
  lock without ever queueing again (such a chain kept a game hold that had waited 45 minutes waiting).
- Inside the hold each game stage asks the GPU gate itself (`hs-run.sh ... --prepare|--whole`); a pair session runs
  `hs-gpu-slot --whole timeout --signal=TERM --kill-after=25 N ...`. `--exclusive` on the gate is only for a timing hold
  Main authorised (`hs-gpu-slot --exclusive timeout --kill-after=5 475 ...`). `hs-run.sh` in wrapper mode cannot nest
  inside a gate you already hold.
- A hold is at most 480 seconds unless the task text grants more (co-op proofs: 1,075 to 1,100). `-w 600` timed out
  silently once; use `-w 14400` and `--verbose`, which logs the wait.
- Cloning a game folder (hundreds of thousands of files) is heavy file-system work: do it inside the measuring lock or
  when no hold runs. Two proof holds were stalled for 14 to 38 seconds by another worker's clone; both games' present
  intervals and a process that runs no game code stalling at the same instants tell an outside stall from a game fault.

## 3. Build the chain

Starting points (read first: they carry their own run's names; the third is the simplest for timeboxed plans):

- `reports/Replay18-files/hold/` (`chain.py`, `build-linux.py`, `title-linux.py`, `tests.py`, `check-only.py`) with
  `reports/Integrate18-files/make-hold.py HEAD MAIN BASE`: build, title run, suites; six stages ran in about 1,955 s.
- `reports/PortUpnp-files/hold-src/` (`chain.py`, `warm.py`, `pair.py`, `tests.py`, `prepare.py`): build, warm, pair
  session, suites with `@HEAD@` templates; its last chain was never admitted, so treat it as untried end to end.
- `reports/SaveSettingsOnly-files/hold/` (`queue_hold.py`, `hold_chain.py`, `make_plan.py`, `inner.sh`, `refresh.sh`):
  a plan of tracks with `min_left`, `timeout --kill-after=25` capped at 1,400 s.

For a pair of games alone, `$H/coop-play/coopplay.py run CASE` is the whole hold (see `ls-scripted-play`).

Keep from them: receipts written at each step (`chain-requested.json` makes a second launch refuse, `chain-waiter.json`
holds the waiter's pid, `stage-K-running.json`, `stage-K-exit.json`, one log per stage opened exclusive-create,
`chain-admitted.json` with the queue wait, `chain-result.json`); never replay a stage; a clean environment (drop
`HOS_*`, `RX_*` and the game's name-prefixed variables); the head's hash and every name in **one small constants file** that each stage
reads when it starts; each stage checks the time left before it starts and runs under its own `timeout`.

## 4. Retarget a queued waiter instead of requeueing

Stage scripts are read when their stage starts and `chain.py` only at admission, so stage scripts and the constants file
can be edited while the waiter is queued; a moved base was handled this way once and saved a 2 hour wait.

- `hold/EDITING` (empty file): after admission the chain polls it every second and waits while it exists. Create it
  before you edit `chain.py` itself or swap folders (`swap-dirs.py`), remove it when the files are whole. While it exists
  the hold keeps both locks and idles the machine (about 12 minutes in one case): be quick.
- `hold-N/READY`: `inner.sh` waits up to 120 s holding the locks for it, then exits 3 and gives the hold back. Write the
  plan and chain script to a temporary file and `os.replace` them, so a stub that only checks `READY` can queue first and
  the real plan arrives later. Rerun `make_plan.py` after every rebase: it asserts the trees match the head.
- A rebase that changes the head still needs the checks of section 1 before the stages run.

## 5. See the queue, find your own waiter, cancel

```sh
lslocks -o PID,COMMAND,MODE,M,PATH | grep -E 'quiet-window|measuring'   # WRITE* = waiting, the holder has no star
ps -eo pid,ppid,stat,etime,cmd | grep 'flock.*quiet-window'            # T = paused by the dispatcher
tail /home/mooki/Code/hookshot-wt/reports/tools/gpu_priority.log
```

- Main's dispatcher (`reports/tools/gpu_priority.py`) leaves only the top-priority quiet-lock waiter runnable and
  SIGSTOPs the rest. State `T` is normal. A stopped flock keeps its own `-w` timer: a waiter paused for the whole of
  `-w 14400` exits 1 unadmitted (one did, after 16,150 s); requeue it in a **new** folder. A stopped flock is not shown
  by `lslocks`. Never signal the dispatcher.
- Record the pid of your own waiter when you start it (`chain-waiter.json`). The `$!` of `setsid ... &` inside a subshell
  is a wrapper that exits at once; find the real waiter with `ps --ppid`. Never use `pgrep -f` or `pkill -f` on a lock
  command line: it matches itself and everybody's.
- Cancel before admission by signalling that flock (SIGTERM, then SIGCONT if it is stopped). After admission, killing
  the outer flock only ends the launcher: stop the stage's own process (for a regression hold, `regress.py _hold`) and
  wait for its game to end.

## 6. Start it, and wait for it from a session

- Start the chain as a managed background job (an async `bash` call with `timeout: 0`) or a launcher started with
  `setsid --wait nohup env --default-signal=HUP`. A plain foreground `cmd &` died with its tool call; a
  `setsid nohup bash -c "flock ..." &` launcher vanished without a receipt within minutes.
- A foreground `bash` call is usually moved to a background job after about 60 seconds and `wait` returns after 5 to
  10; neither waits for a hold. One `eval` (Python) cell with `timeout` up to 3600 that loops on the receipt file, or on
  inotify, blocks until the file exists (39 minutes in one run). A reply without a tool call ends the run.
- A hold likely to be admitted hours later, past your request budget: hand Main the queued waiter, its pid and the
  steps for reading the results (`chain-admitted.json`, `chain-result.json`, each `stage-K-exit.json`), and stop.
- `hs-nocore.sh` queued behind someone's exclusive measuring lock prints nothing for minutes; that is the wait, not a
  hang.

Report the admission wait separately from the child's time, with the receipts.
