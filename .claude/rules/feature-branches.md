## Feature branches (R6 Coach)

The player runs the coach from a clone of `main`, so `main` must always work. The human version of this workflow is in CONTRIBUTING.md; this is the version agents follow. When the two disagree, CONTRIBUTING.md wins: fix this file to match.

### Before the first edit

- Check the branch: `git branch --show-current`. If it is `main`, create a branch before editing anything: `git switch -c <type>/<short-kebab-description>`.
- Types: `feature/`, `fix/`, `data/` (knowledge YAML only), `docs/`, `refactor/`, `eval/`, `chore/`. Lowercase, a few words: `fix/killfeed-own-name-match`.
- If uncommitted changes are already on `main` when you start, move them onto a branch (`git switch -c ...` carries them) rather than committing them to `main`.
- One branch per change. Unrelated work you notice goes on its own branch or into a note to the user, not into this one.
- If you are already on a non-`main` branch, stay on it only if the task belongs to that branch's change. Otherwise ask the user, or branch from an up-to-date `main`.

### While working

- Never commit to `main` directly. Never push to `main`. Never force-push `main`. GitHub's "Protect main" ruleset blocks direct and force pushes. The `.githooks/` pre-commit and pre-push hooks stop you earlier, when `core.hooksPath` is set. Never bypass them with `--no-verify`. If `git config core.hooksPath` is not `.githooks`, tell the user once.
- Commit in small steps that each leave `pytest -q` passing. Subject in the imperative, under about 70 characters; a body when the why isn't obvious; end with the `Co-Authored-By:` line for the agent.
- Never commit `.env`, `config.yaml`, anything under `data/`, or blueprint images. Check `git status` before every commit.
- Commit and push only when the user asked for it or the task clearly includes it.

### Before opening a PR

Run and report the results (paste failures, don't summarise them away):

- `.venv/Scripts/python -m pytest -q` (tests that need `data/calibration/` skip; that is expected).
- Knowledge YAML changed: `.venv/Scripts/python tools/check_setups.py`.
- Prompts, models or efforts changed: tell the user `tools/eval_real.py` should be run before and after (it costs about $1); run it only if they agree.
- HUD, screen or capture changed: say whether it was tried with `--replay` or in a real match, and that the user should play-test it.
- Update docs (docs/ARCHITECTURE.md bus table, config.example.yaml, KNOWLEDGE_DATA.md, HANDOVER.md status) in the same branch when behaviour, config, events or formats change.

### Pull requests and merging

- Bring the branch up to date with `origin/main` first (rebase if only you use the branch).
- Open the PR into `main` with `gh pr create`, filling in .github/pull_request_template.md. Title like a commit subject; it becomes the squash commit.
- Merge only when the user says so, with `gh pr merge --squash --delete-branch`. Then `git switch main` and `git pull --ff-only`.
- A change that needs another unmerged change: target that branch, or target `main` and write "merge after #N" in the description.

### Fair-play check on every PR

Confirm in the PR description that the change keeps the invariants in docs/ARCHITECTURE.md: capture only, HUD and menus only, no enemy positions from the 3D world, no input sent to the game, cloud reads gated, dashboard on 127.0.0.1 with no secrets.
