# Workbench MCP routes

After the operator setup linked in `ls-workbench` (private state root, catalogues, policy and registered roots),
without an MCP client, run these from the installed workbench checkout:

```sh
uv run --project mcp ls-wb list --json
uv run --project mcp ls-wb call workbench_status --args '{}'
uv run --project mcp ls-wb read 'workbench://tools'
```

For a table tool, use `call <tool> --args '<JSON object>'`; add `--profile operate` for its local/run operations.
Read the catalogue's **CLI broker** column: registry/file reads are one-shot, while live sessions and owned jobs
use the private persistent broker. Submission returns a Job immediately, not its completion. Keep the same
configured state root across calls; a CLI timeout does not cancel work or authorize replay. Do not stop a shared
broker to clean up one task.

Use registered IDs, not caller-selected scripts or paths. Check `workbench_status()` and the installed catalogue
first; an integrated group can still lack its operator configuration. `observe` exposes READ tools; local checks/jobs
need `operate` and current policy. A returned Job is not a result: use `job_wait(job_id=...)`, then
`job_read(job_id=...)` and retain its receipt. Follow every cursor and truncation flag.
Pushes, posts, uploads, fork-CI starts and claims remain Main's shell actions; MCP has no route for them.

| Step | Shell route | MCP tool / group / integration | What it saves |
|---|---|---|---|
| Read the whole PR | `gh pr view` plus paginated comments/reviews | `github_thread_read(repository="<owner>/<repo>", number=N)` — knowledge, integrated | One bounded snapshot with head/hash and pagination state; fetch truncated text separately |
| Find conflicting PRs | `"$P" -I -B "$E/pr_overlap.py" --branch <branch>` | No overlap tool exists | Keep the shell hunk review; thread facts do not prove merge conflicts |
| Mechanical pre-push check | `"$H/hs-pr-check.sh" <branch> <title-file> [import options]` | `checks_pr(repo_id=..., commit=..., branch=..., title_artifact=..., idempotency_key=..., imported=[...])` — checks, integrated | Pinned reviewed checkout, parsed result and receipt; `--imported-files` remains shell-only |
| Windows-target fixtures | `"$H/hs-idle.sh" "$H/hs-win-fixture-check.sh" <worktree> <base> --sysroot <sysroot>` | `checks_windows_syntax(repo_id=..., commit=..., base=..., idempotency_key=...)` — checks, integrated | Registered sysroot/gate and explicit unjudged/runtime-not-run result |
| Read PR/fork checks | `"$H/hs-pr-ci.sh" <n> --repo <owner/repo>` | `ci_read(repository=..., pull_request=N)` — checks, integrated | Named failures and pinned main comparison; no CI rerun or fork starter |
| Make panels / scan retained pixels | `"$E/panels.sh" ...`; `"$P" -I -B scripts/framelog.py <capture> <new.png>` from trusted current main | `evidence_panels(images=..., label=..., idempotency_key=...)`; `evidence_scan_frames(capture_artifact_id=..., idempotency_key=...)` — evidence, integrated | Source hashes, new outputs and candidate records; inspection still required |
| Compare / bundle | Shared regression comparison and hand-built evidence table | `evidence_compare(pairing=...)`; `evidence_bundle(request=...)` — evidence, integrated | Existing-run pairs, clock/quiet rejection and private provenance-linked draft; no invented numbers |
| Read reports / promises | Read registered report and `POSTED.md` | `knowledge_search(query=..., collection="reports")`; resources `workbench://reports/<id>` and `workbench://obligations` — knowledge, integrated | Bounded, revision-pinned text and follow-up rows; not a full-body search |
| Inspect queue / start owned windowless session | GPU guard `--status`; `"$H/hs-run.sh" <owned-copy> <tag> <seconds>` | `queue_status()` — status, integrated; `session_launch(build_id=..., profile_id=..., wall_seconds=..., idempotency_key=...)` — session, integrated | Passive queue state; catalogue-bound disposable session, never permission for a hold |
| Watch selected threads | Existing `hs-watch-threads.py` coordinator | `github_watch(threads=..., timeout_seconds=..., idempotency_key=...)` — knowledge, integrated | Owned bounded watch/cursor Job; it neither posts nor replaces a fresh full-thread read |
