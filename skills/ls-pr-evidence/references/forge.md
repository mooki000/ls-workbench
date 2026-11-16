# On the LS forge

The `gh` routes in `SKILL.md` are the GitHub-era ones. LS's pull requests live on the team's forge:

```sh
REPO=MagicBeanDev/Longshot
API=https://git.magicbean.dev/api/v1/repos/$REPO
FORGE="python -I /home/mooki/Code/hookshot-wt/Tooling/scripts/forge.py --repo $REPO"
```

- **Posting is Main's.** `/home/mooki/Code/hookshot-wt/reports/tools/ls_post.py` has `pr`, `update` and `issue`: no
  comment-only command, no title edit, no `--tested-in-game`; it refuses a body with `/home/`, an `hs-` tool name or
  `agent://` (a posted test description names no local path and no tool). `$FORGE` does the rest (`scripts/forge.py` in the
  game repository only forwards to it, and `--repo` goes **before** the subcommand): `$FORGE pr comment N`,
  `pr edit N --title T`, `pr ready N` (runs the description check), `pr status N`, `search WORD ... --state all`.
  `pr create` for a branch that touches `gpu/`, `host/`, `nx2/`, `assets/` or `third_party/` needs
  `--tested-in-game "<where it ran and what was seen>"`, even for a game-free engine the game does not build; `--force`
  only when the person asks.
- **Reading** the public repository needs no credential: `GET $API/pulls/N`, `pulls/N.diff`, `pulls/N/files`,
  `pulls/N/reviews`, `issues/N/comments`, `issues/N/timeline`, `issues/comments/<id>`, `statuses/<sha>`. A 403 means
  stop and tell Main; do not look for a credential. `pr://N` and `issue://N` are GitHub routes and do not read the forge,
  and the page reader of a comment URL returns only the feed title: take the comment from `issues/comments/<id>`.
- **The issue-comments endpoint is not paged**: `page=2` returns the same list, so a `page` loop never ends. Count
  unique ids against the `X-Total-Count` header, and read `pulls/N/reviews` and the inline discussions separately. The
  list endpoints are paged (`pulls?state=open&limit=50&page=N`, at most 50 a page; 242 were open on 2026-11-15).
- **Overlaps**: `pr_overlap.py` is GitHub-only and fetches from the remote. On the forge take each open pull request's
  `pulls/N/files`, fetch its head (`git fetch forge +refs/pull/N/head:refs/remotes/pr/N`) and run
  `git merge-tree --write-tree --name-only <base> <head>` against your branch **and** against forge main: only a conflict
  that appears with your branch is an overlap (21 of 38 already conflicted with main). Without Git against the forge
  remote, apply each public `.diff` with `git apply --check` to pinned main and to your branch and compare.
- **The description** is checked by `scripts/check_pr_description.py` of the checkout (see `SKILL.md`, "The body"); a
  pull request is work in progress (`WIP:` title) until `pr ready N` passes it.
