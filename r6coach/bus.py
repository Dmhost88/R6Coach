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


class Bus:
    def __init__(self, history: int = 300) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()
        self.history: deque[Event] = deque(maxlen=history)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe to a topic, or "*" for every event."""
        with self._lock:
            self._subs[topic].append(handler)

    def publish(self, topic: str, **data: Any) -> Event:
        ev = Event(topic, data)
        with self._lock:
            self.history.append(ev)
            handlers = list(self._subs.get(topic, [])) + list(self._subs.get("*", []))
        for h in handlers:
            try:
                h(ev)
            except Exception:  # one bad handler must not break the others
                log.exception("handler for %s failed", topic)
        return ev
