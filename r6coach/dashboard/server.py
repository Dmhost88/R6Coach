"""Second-monitor dashboard: a local web page (127.0.0.1 only) served by FastAPI."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from ..bus import Event
from ..config import save_config

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"

# Events forwarded to the page. vision.read carries the full read, which the detection view uses.
FORWARD = {"state.updated", "screen.changed", "coach.said", "coach.advice", "coach.thinking", "vision.read",
           "llm.usage", "error", "ptt.listening", "ptt.question", "ptt.ready", "match.started",
           "round.ended", "match.ended", "status"}


def _jsonable(ev: Event) -> str:
    return json.dumps({"topic": ev.topic, "ts": ev.ts, "data": ev.data}, default=str)


def build(app: "App") -> FastAPI:
    api = FastAPI(title="R6 Coach", docs_url=None, redoc_url=None)
    clients: set[asyncio.Queue] = set()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def on_event(ev: Event) -> None:
        if ev.topic not in FORWARD:
            return
        loop = loop_holder.get("loop")
        if loop is None:
            return
        msg = _jsonable(ev)
        for q in list(clients):
            loop.call_soon_threadsafe(_put, q, msg)

    def _put(q: asyncio.Queue, msg: str) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass  # a stalled page just misses events

    app.bus.subscribe("*", on_event)

    @api.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @api.get("/api/status")
    def status() -> dict[str, Any]:
        return app.status()

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
        save_config(c)
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

    @api.get("/api/matches")
    def matches() -> list[dict[str, Any]]:
        return app.profile.recent_matches()

    @api.post("/api/control/{action}")
    def control(action: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        if action == "ask":
            text = str(body.get("text", "")).strip()
            if text:
                app.bus.publish("ptt.question", text=text, typed=True)
        elif action in app.controls:
            app.controls[action]()
        else:
            raise HTTPException(404, f"unknown action {action}")
        return app.status()

    @api.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        loop_holder["loop"] = asyncio.get_running_loop()
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
