"""System tray icon: open the dashboard, mute, quit."""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import App


def _icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(232, 163, 61, 255))
    d.rectangle((26, 16, 38, 40), fill=(20, 20, 20, 255))   # a simple microphone shape
    d.rectangle((30, 40, 34, 50), fill=(20, 20, 20, 255))
    d.rectangle((22, 48, 42, 52), fill=(20, 20, 20, 255))
    return img


def run_tray(app: "App") -> None:
    """Blocks on the main thread until Quit is chosen."""
    import pystray

    url = f"http://{app.cfg.dashboard.host}:{app.cfg.dashboard.port}/"

    def quit_(icon, _item) -> None:  # noqa: ANN001
        app.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem("Mute / unmute", lambda *_: app.toggle_mute()),
        pystray.MenuItem("Pause / resume screen reading", lambda *_: app.toggle_pause()),
        pystray.MenuItem("Quit", quit_),
    )
    pystray.Icon("r6coach", _icon_image(), "R6 Coach", menu).run()
