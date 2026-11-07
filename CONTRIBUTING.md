# Contributing

How we work on R6 Coach: branches, commits, pull requests and merging. The short version:

> **`main` always works. All work happens on a short-lived branch, goes through a pull request, and is squash-merged once the tests pass.**

New here? Read [docs/HANDOVER.md](docs/HANDOVER.md) first, then [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) to get set up.

## Why feature branches

The player runs the coach straight from a clone of `main`. A half-finished change on `main` means a coach that crashes or says wrong things in a Ranked match. Branches let work in progress live somewhere safe, let a reviewer (person or agent) see one change at a time, and give every change a PR that explains why it was made.

## The workflow

1. **Start from an up-to-date `main`.**

   ```bash
   git switch main
   ```

   ```bash
   git pull --ff-only
   ```

2. **Create a branch** named for the change (see naming below).

   ```bash
   git switch -c feature/attack-drone-paths
   ```

3. **Commit in small, working steps.** Each commit should leave the tests passing. Push early so the work is backed up:

   ```bash
   git push -u origin feature/attack-drone-paths
   ```

4. **Keep the branch current.** If `main` moved on, bring it in before opening the PR (rebase if nobody else uses your branch, merge otherwise):

   ```bash
   git fetch origin
   ```

   ```bash
   git rebase origin/main
   ```

5. **Open a pull request** into `main` with `gh pr create` or on GitHub. Fill in the template: what changed, why, how you tested it, and eval scores for prompt/model changes.
6. **Checks before merge** (the author runs them; there is no CI yet):
   - `.venv/Scripts/python -m pytest -q` passes.
   - Knowledge YAML changed → `tools/check_setups.py` passes.
   - Prompts, models or efforts changed → `tools/eval_real.py` before and after, scores in the PR.
   - HUD, screen or capture changes → tried in a real match or with `--replay`, said so in the PR.
   - Docs updated if behaviour, config, events or file formats changed.
7. **Squash-merge** the PR, so `main` gets one commit per change with the PR's title as its message. GitHub deletes the remote branch automatically; delete your local copy with `git branch -D <branch>`.
8. **Pull `main`** locally and restart the coach if you are the player.

## Branch names

`<type>/<short-kebab-description>`, lowercase, a few words:

| Prefix | For | Example |
|---|---|---|
| `feature/` | new behaviour the player notices | `feature/round-debrief-voice` |
| `fix/` | a bug | `fix/killfeed-own-name-match` |
| `data/` | knowledge YAML only (maps, callouts, layout, setups, meta) | `data/season-y11s4` |
| `docs/` | documentation only | `docs/handover-and-workflow` |
| `refactor/` | internal change, same behaviour | `refactor/split-watcher-gate` |
| `eval/` | eval scenarios and tooling | `eval/kanal-ranked-match` |
| `chore/` | dependencies, config, tooling | `chore/bump-anthropic-sdk` |

One branch is one change. If you find an unrelated bug while working, note it (an issue, or a separate `fix/` branch), don't fold it in.

## Commits

- Subject line in the imperative, under about 70 characters, saying what the change does for the player or the code: "Recognise screens locally to cut cloud reads by ~68%".
- A body when the why is not obvious: the problem, the approach, anything surprising.
- Don't commit `.env`, `config.yaml`, anything under `data/`, or Ubisoft blueprint images. `.gitignore` covers these; check `git status` before committing anyway.
- Work written with an AI agent ends with a `Co-Authored-By:` line for that agent.

## Pull requests

- Title: like a commit subject. It becomes the squash commit on `main`.
- Small is good. A PR over about 500 changed lines of code (data files don't count) should usually be split.
- Draft PRs are welcome for early feedback (`gh pr create --draft`).
- A stacked change (B needs A) targets A's branch, or targets `main` with "merge after #A" in the description.
- The reviewer checks: does it keep the fair-play invariants in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#invariants-to-keep)? Does it add cloud calls without a gate? Are tests and docs updated?

## Hotfixes

Something broken on `main` that affects the player right now: branch `fix/<what>` from `main`, smallest possible change plus a test that would have caught it, PR, merge. The same rules apply; the change is just smaller.

## Releases

There are no versioned releases yet. When the coach is shared beyond the owner, tag `main` (`v0.2.0`, …) when it has been played in real matches, and note the changes in the tag's release notes.

## How the rules are enforced

**On GitHub** (already set): squash merging is the only merge method allowed, the squash commit takes the PR's title and description, and branches are deleted automatically once merged.

A ruleset ("Protect main") protects `main`:
- every change goes through a pull request (0 approvals needed, squash only);
- force pushes and deletion are blocked.

Add "require the test check" to it once there is CI.

**On your PC** (once per clone): the repository has git hooks in `.githooks/` that refuse a commit while you are on `main` and refuse any push to `main`. Turn them on:

```bash
git config core.hooksPath .githooks
```

Don't skip them with `--no-verify`. If a hook stops you, create a branch: `git switch -c feature/<what>` carries your changes over.
