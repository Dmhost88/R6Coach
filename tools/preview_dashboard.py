"""Serve the dashboard with a made-up match state, for checking the map and setup briefs without
the game: no capture, no voice, no cloud calls.

    .venv\\Scripts\\python tools\\preview_dashboard.py --map Chalet --side defense --site "Master Bedroom" \\
        --op Kaid --screen prep_phase --where "2F Library" --port 8766

POST /api/control/ask with {"text": "..."} on the page then shows that line as a coach line and lights
up the rooms it names, like a real one.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--map", default="Chalet")
    p.add_argument("--side", default="defense")
    p.add_argument("--site", default="")
    p.add_argument("--op", default="")
    p.add_argument("--screen", default="prep_phase")
    p.add_argument("--where", default="")
    p.add_argument("--port", type=int, default=8766)
    a = p.parse_args()
    # As --offline does: an empty value (not a deleted one) stops load_config from reading .env.
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["R6COACH_OFFLINE"] = "1"

    import uvicorn

    from r6coach.app import App
    from r6coach.config import load_config
    from r6coach.dashboard.server import build
    from r6coach.models import Screen

    cfg = load_config()
    cfg.dashboard.port = a.port
    app = App(cfg, replay=Path(tempfile.mkdtemp()), enable_ptt=False)
    app.speaker.set_muted(True)
    st = app.watcher.state
    st.screen = Screen(a.screen)
    st.map, st.side, st.site = a.map, a.side or None, a.site or None
    st.my_operator, st.my_location = a.op or None, a.where or None
    st.match_id = "preview"
    api = build(app)
    # Typed questions become coach lines here, so the map focus can be tried without Claude.
    app.bus.subscribe("ptt.question", lambda ev: app.bus.publish(
        "coach.said", text=ev.data.get("text", ""), phase="question"))
    print(f"Preview on http://127.0.0.1:{a.port}/  (Ctrl+C to stop)")
    uvicorn.run(api, host="127.0.0.1", port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
