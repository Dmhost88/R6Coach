"""A tiny thread-safe publish/subscribe bus that connects the components."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Event:
    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Handler = Callable[[Event], None]


# High-frequency topics (about once a second from the HUD reader) are delivered but not kept in
# history, so they can't push errors and coach lines out of the dashboard's backlog.
NOT_IN_HISTORY = {"state.updated", "live.event"}


class Bus:
    def __init__(self, history: int = 300) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()
        self.history: deque[Event] = deque(maxlen=history)
        self._last_state: dict[str, Any] = {}   # for the debug log's state diffs

    def _log_event(self, topic: str, data: dict[str, Any]) -> None:
        """Debug: every event. state.updated logs only the fields that changed (it is published
        about once a second with the whole state); bytes (frames) are shown by size."""
        if topic == "state.updated" and isinstance(data.get("state"), dict):
            new = data["state"]
            changed = {k: v for k, v in new.items() if self._last_state.get(k) != v}
            self._last_state = dict(new)
            if changed:
                log.debug("event state.updated: %s", _short(changed, 800))
            return
        log.debug("event %s: %s", topic, _short(data, 500))

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe to a topic, or "*" for every event."""
        with self._lock:
            self._subs[topic].append(handler)

    def publish(self, topic: str, **data: Any) -> Event:
        ev = Event(topic, data)
        if log.isEnabledFor(logging.DEBUG):
            self._log_event(topic, data)
        with self._lock:
            if topic not in NOT_IN_HISTORY:
                self.history.append(ev)
            handlers = list(self._subs.get(topic, [])) + list(self._subs.get("*", []))
        for h in handlers:
            try:
                h(ev)
            except Exception:  # one bad handler must not break the others
                log.exception("handler for %s failed", topic)
        return ev


def _short(data: Any, n: int) -> str:
    """One line, at most n characters; bytes shown by size, never by content."""
    if isinstance(data, dict):
        data = {k: (f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else v) for k, v in data.items()}
    s = " ".join(str(data).split())
    return s if len(s) <= n else s[:n] + f"... (+{len(s) - n} chars)"
