## What and why

<!-- What does this change do for the player or the code, and why is it needed? -->

## How it was tested

- [ ] `.venv/Scripts/python -m pytest -q` passes
- [ ] Knowledge YAML changed → `tools/check_setups.py` passes (or n/a)
- [ ] Prompts / models / efforts changed → `tools/eval_real.py` before → after: <!-- scores --> (or n/a)
- [ ] HUD / screen / capture changed → tried with `--replay` or in a real match (or n/a)
- [ ] Dashboard changed → checked in `tools/preview_dashboard.py` (or n/a)

## Fair play

- [ ] Reads only menus and the HUD from the player's own screen; nothing looks for enemies in the 3D world; no input sent to the game
- [ ] Any new cloud call is gated and counted against the match budget

## Docs

- [ ] Updated where behaviour, config, bus events or file formats changed (or n/a)

<!-- Depends on another PR? Write "merge after #N". -->
