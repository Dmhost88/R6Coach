# R6 Coach — instructions for AI agents

An audio-only Rainbow Six Siege coach for PC (Python 3.12, Windows). Read [docs/HANDOVER.md](docs/HANDOVER.md) for where things stand and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it fits together.

## Rules

- **Branches:** follow [.claude/rules/feature-branches.md](.claude/rules/feature-branches.md). Never edit, commit or push on `main`.
- **Fair play is a hard constraint** ([SPEC.md](SPEC.md)): capture only; read menus and the HUD only; never analyse the 3D world for enemies; never send input to the game or touch its process. Refuse a change that breaks this and tell the user why.
- **Cost:** cloud reads must stay gated (`watcher.ReadGate`, `local_cloud_policy`). Don't add a Claude call on a hot path without a gate and a budget check.
- **Claude calls** go through `r6coach/llm.py` with structured outputs only.
- **Secrets** live in `.env`. Never print them, log them, or return them from the dashboard.
- **Never commit** `.env`, `config.yaml`, `data/`, or Ubisoft blueprint images.

## Commands

- Tests: `.venv/Scripts/python -m pytest -q` (~75 s, no network; some skip without `data/calibration/`).
- Knowledge data check: `.venv/Scripts/python tools/check_setups.py`.
- Dashboard without the game: `.venv/Scripts/python tools/preview_dashboard.py --map Chalet --side defense --port 8766` (use your own port).
- Evals (`tools/eval_real.py`, `tools/eval_advice.py`) make real API calls and cost money: ask first.
- Use `python`, not `python3`. Open text files with `encoding="utf-8"`.

## When you change things

Update the docs in the same branch: the bus table in docs/ARCHITECTURE.md for new events, `config.example.yaml` for new settings, docs/KNOWLEDGE_DATA.md for format changes, docs/HANDOVER.md when status or limits change, and the module docstring of any module whose job changed.
