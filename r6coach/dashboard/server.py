"""Second-monitor dashboard: a local web page (127.0.0.1 only) served by FastAPI."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import yaml

from .. import config as config_mod
from ..bus import Event
from ..config import DashboardCfg, save_config

if TYPE_CHECKING:
    from ..app import App
    from ..config import Config

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"

# Events forwarded to the page. vision.read carries the full read, which the detection view uses.
FORWARD = {"state.updated", "screen.changed", "coach.said", "coach.advice", "coach.thinking", "vision.read",
           "llm.usage", "error", "ptt.listening", "ptt.question", "ptt.ready", "match.started",
           "round.ended", "match.ended", "status", "killfeed", "player.died", "map.focus"}

TEST_LINE = "Voice check. 3 v 2. They have to come to you."


def _jsonable(ev: Event) -> str:
    return json.dumps({"topic": ev.topic, "ts": ev.ts, "data": ev.data}, default=str)


def _has_env(name: str) -> bool:
    return bool(os.environ.get(name))


def _without_run_overrides(cfg: "Config") -> "Config":
    """A copy of cfg to write to config.yaml, keeping the dashboard section as it is on disk.

    --port and --no-browser change cfg.dashboard for one run only; saving settings from the page
    must not make them permanent.
    """
    out = cfg.model_copy(deep=True)
    for path in (config_mod.config_path(), config_mod.ROOT / "config.example.yaml"):
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except OSError:
            continue
        except yaml.YAMLError:
            break
        out.dashboard = DashboardCfg.model_validate(data.get("dashboard") or {})
        break
    return out


def setup_checklist(app: "App") -> list[dict[str, Any]]:
    """What is configured, as booleans and plain labels. Never includes a key's value."""
    c = app.cfg
    items: list[dict[str, Any]] = [
        {"id": "anthropic", "label": "Claude API key", "ok": _has_env("ANTHROPIC_API_KEY"),
         "hint": "ANTHROPIC_API_KEY in .env. Without it the coach reads the HUD locally and stays quiet."},
    ]
    prov = c.voice.provider
    if prov == "openai":
        items.append({"id": "voice", "label": "OpenAI voice key", "ok": _has_env("OPENAI_API_KEY"),
                      "hint": "OPENAI_API_KEY in .env. Without it the offline Windows voice is used."})
    elif prov == "elevenlabs":
        items.append({"id": "voice", "label": "ElevenLabs voice key and voice id",
                      "ok": _has_env("ELEVENLABS_API_KEY") and bool(c.voice.elevenlabs_voice_id),
                      "hint": "ELEVENLABS_API_KEY in .env and voice.elevenlabs_voice_id in config.yaml."})
    else:
        items.append({"id": "voice", "label": "Voice on this PC", "ok": True,
                      "hint": f"Voice provider is {prov}; no key needed."})
    items += [
        {"id": "player", "label": "In-game name", "ok": bool(c.player_name),
         "hint": "player_name in config.yaml, so the coach can tell your kills and deaths apart."},
        {"id": "maps", "label": "Map data", "ok": len(app.knowledge.maps) > 0,
         "hint": f"{len(app.knowledge.maps)} maps loaded."},
        {"id": "operators", "label": "Operator data", "ok": len(app.knowledge.operators) > 0,
         "hint": f"{len(app.knowledge.operators)} operators loaded."},
        {"id": "pool", "label": "Operator pool", "ok": bool(c.operator_pool.attack and c.operator_pool.defense),
         "hint": "At least one attacker and one defender in your operator pool."},
        {"id": "ptt", "label": "Push-to-talk", "ok": bool(app.ptt) and bool(app.ptt_ready),
         "hint": f"Hold {c.push_to_talk.key} to ask." if app.ptt else "Off for this run (--no-ptt)."},
    ]
    return items


def build(app: "App") -> FastAPI:
    api = FastAPI(title="R6 Coach", docs_url=None, redoc_url=None)
    clients: set[asyncio.Queue] = set()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}
    speech = {"said": False, "started": False}

    def send(msg: str) -> None:
        loop = loop_holder.get("loop")
        if loop is None:
            return
        for q in list(clients):
            loop.call_soon_threadsafe(_put, q, msg)

    def on_event(ev: Event) -> None:
        if ev.topic not in FORWARD:
            return
        if ev.topic == "coach.said" and ev.data.get("audio"):
            speech["said"] = True
        send(_jsonable(ev))

    def speaking() -> bool:
        return bool(getattr(app.speaker, "_playing", False))

    def watch_speech() -> None:
        """Tell the page when a spoken line has finished, so it can drop the is-speaking state.

        The speaker announces a line (coach.said) just before it plays and clears its playing flag
        when playback ends. Watching that flag here keeps the voice code untouched. The event goes
        to open pages only, not onto the bus.
        """
        while True:
            time.sleep(0.15)
            if speech["said"] and not speaking():
                speech["said"] = False
                send(json.dumps({"topic": "coach.done", "ts": time.time(), "data": {}}))

    def _put(q: asyncio.Queue, msg: str) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass  # a stalled page just misses events

    app.bus.subscribe("*", on_event)

    @api.middleware("http")
    async def revalidate(request, call_next):
        # The page and its assets change with app updates; make the browser check instead of guessing.
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-cache")
        return resp

    @api.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    def full_status() -> dict[str, Any]:
        st = app.status()
        st["speaking"] = speaking()
        st["voice_provider"] = app.cfg.voice.provider
        return st

    @api.get("/api/status")
    def status() -> dict[str, Any]:
        return full_status()

    @api.get("/api/setup")
    def setup() -> list[dict[str, Any]]:
        return setup_checklist(app)

    @api.get("/api/memory")
    def memory() -> dict[str, Any]:
        mem = getattr(app, "memory", None)
        if mem is None:
            return {"rounds": [], "current": None, "killers_of_me": {}}
        with mem._lock:
            return {
                "rounds": [dataclasses.asdict(r) for r in mem.rounds],
                "current": dataclasses.asdict(mem.cur),
                "killers_of_me": dict(mem.killers_of_me.most_common()),
            }

    @api.get("/api/history")
    def history() -> list[dict[str, Any]]:
        return [json.loads(_jsonable(e)) for e in app.bus.history if e.topic in FORWARD]

    @api.get("/api/frame.jpg")
    def frame() -> Response:
        jpeg = app.watcher.current_jpeg()
        if jpeg is None:
            raise HTTPException(404, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @api.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        c = app.cfg
        return {
            "operator_pool": c.operator_pool.model_dump(),
            "not_owned": c.not_owned,
            "chattiness": c.coach.chattiness,
            "skill_level": c.coach.skill_level,
            "volume": c.voice.volume,
            "coach_model": c.coach.model,
            "vision_model": c.vision.model,
            "ptt_key": c.push_to_talk.key,
            "operators": sorted({o["name"] for o in app.knowledge.operators.values()}),
            "operators_by_side": {
                side: sorted({o["name"] for o in app.knowledge.operators.values() if o.get("side") == side})
                for side in ("attack", "defense")
            },
            "voice": {
                "provider": c.voice.provider,
                "voice": {"openai": c.voice.openai_voice, "elevenlabs": c.voice.elevenlabs_voice_id,
                          "kokoro": getattr(c.voice, "local_voice", "")}.get(c.voice.provider, ""),
                "model": {"openai": c.voice.openai_model, "elevenlabs": c.voice.elevenlabs_model,
                          "kokoro": getattr(c.voice, "local_model", "")}.get(c.voice.provider, ""),
                "output_device": c.voice.output_device or "",
            },
            "player_name": c.player_name,
        }

    @api.post("/api/settings")
    def set_settings(body: dict[str, Any] = Body(...)) -> dict[str, str]:
        c = app.cfg
        if "operator_pool" in body:
            c.operator_pool = c.operator_pool.model_validate(body["operator_pool"])
        if "not_owned" in body:
            c.not_owned = [str(x) for x in body["not_owned"]]
        if "chattiness" in body:
            c.coach.chattiness = {k: max(0, min(3, int(v))) for k, v in body["chattiness"].items()}
        if "skill_level" in body and body["skill_level"] in ("new", "intermediate", "advanced"):
            c.coach.skill_level = body["skill_level"]
        if "volume" in body:
            c.voice.volume = max(0.0, min(1.0, float(body["volume"])))
        save_config(_without_run_overrides(c))
        return {"ok": "saved"}

    @api.get("/api/maps")
    def maps() -> list[dict[str, Any]]:
        return [{"id": mid, "name": m.get("name", mid), "sites": [s.get("name") for s in m.get("sites") or []]}
                for mid, m in sorted(app.knowledge.maps.items())]

    @api.get("/api/maps/{map_id}")
    def map_detail(map_id: str) -> dict[str, Any]:
        m = app.knowledge.maps.get(map_id)
        if m is None:
            raise HTTPException(404)
        return {"data": m, "notes": app.knowledge.notes(map_id)}

    @api.post("/api/maps/{map_id}/notes")
    def save_notes(map_id: str, body: dict[str, str] = Body(...)) -> dict[str, str]:
        if map_id not in app.knowledge.maps:
            raise HTTPException(404)
        app.knowledge.save_notes(map_id, body.get("notes", ""))
        return {"ok": "saved"}

    @api.get("/api/layout")
    def layout(map: str = "") -> dict[str, Any]:
        """The current (or named) map's floors and room boxes, plus where the player is."""
        if map:
            m = app.knowledge.find_map(map)
            mid = next((k for k, v in app.knowledge.maps.items() if v is m), None) if m else None
        else:
            mid = app.map_id()
        lay = app.layouts.get(mid)
        if not lay:
            return {"map": mid, "floors": []}
        floors = [{"floor": f["floor"], "size": f.get("size"), "rooms": f.get("rooms") or {},
                   "url": f"/api/blueprint/{mid}/{f['image']}" if app.layouts.image_path(mid, f["image"]) else None}
                  for f in lay["floors"]]
        return {"map": mid, "floors": floors,
                "you": app.layouts.locate(mid, app.watcher.state.my_location)}

    @api.get("/api/blueprint/{map_id}/{image}")
    def blueprint(map_id: str, image: str) -> Response:
        """A blueprint floor image, scaled to 1600 px wide and cached as JPEG (Calypso's are 10 MB PNGs)."""
        src = app.layouts.image_path(map_id, image)
        if src is None:
            raise HTTPException(404)
        web = src.parent / "web" / (src.stem + ".jpg")
        if not web.exists() or web.stat().st_mtime < src.stat().st_mtime:
            import cv2
            import numpy as np
            img = cv2.imdecode(np.fromfile(str(src), np.uint8), cv2.IMREAD_COLOR)
            h, w = img.shape[:2]
            if w > 1600:
                img = cv2.resize(img, (1600, round(h * 1600 / w)), interpolation=cv2.INTER_AREA)
            web.parent.mkdir(exist_ok=True)
            cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(str(web))
        return FileResponse(web, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @api.get("/api/matches")
    def matches() -> list[dict[str, Any]]:
        return app.profile.recent_matches()

    @api.get("/api/matches/{match_id}")
    def match_report(match_id: str) -> dict[str, Any]:
        rep = app.profile.match_report(match_id)
        if rep is None:
            raise HTTPException(404, "no such match")
        return rep

    @api.post("/api/control/{action}")
    def control(action: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        if action == "ask":
            text = str(body.get("text", "")).strip()
            if text:
                app.bus.publish("ptt.question", text=text, typed=True)
        elif action == "test_voice":
            st = full_status()
            if app.speaker.muted:
                st["test_voice"] = "muted"
            else:
                app.speaker.say(TEST_LINE, priority=0, ttl=10.0, phase="test")
                st["test_voice"] = "queued"
            return st
        elif action in app.controls:
            app.controls[action]()
        else:
            raise HTTPException(404, f"unknown action {action}")
        return full_status()

    @api.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        loop_holder["loop"] = asyncio.get_running_loop()
        if not speech["started"]:
            speech["started"] = True
            threading.Thread(target=watch_speech, name="dashboard-speech", daemon=True).start()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        clients.add(q)
        try:
            while True:
                msg = await q.get()
                await sock.send_text(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            clients.discard(q)

    # Stylesheets, scripts, fonts and the design system's assets. Mounted last so the API routes win.
    api.mount("/static", StaticFiles(directory=STATIC), name="static")
    return api


def serve(app: "App") -> threading.Thread:
    import uvicorn

    api = build(app)
    config = uvicorn.Config(api, host=app.cfg.dashboard.host, port=app.cfg.dashboard.port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="dashboard", daemon=True)
    t.start()
    return t
