"""Local HUD reader: reads the in-round HUD on your PC, for free, about once a second.

Everything here comes from fixed HUD positions on your own screen: timer, scores, team
reinforcements, your room callout, your health, the big centre banners ("5 VS 2", "WON ROUND 3",
"DEFUSER PLANTED"...) and the killfeed. It never looks at the 3D game world.

Cost control: fixed boxes use recognition-only OCR (~20 ms each). Banners and the killfeed need
text detection (~0.7-2 s), so they only run when a cheap pixel check sees white banner text or
coloured killfeed name boxes change.

Regions were measured on 3440x1440 (21:9). They are stored relative to the screen centre in
units of screen height, which is how Siege lays out its HUD; other aspect ratios are unverified.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

REF_W, REF_H = 3440, 1440


def _rel(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """Convert fractions measured on 3440x1440 into centre-relative height units."""
    k = REF_W / REF_H
    return ((x0 - 0.5) * k, y0, (x1 - 0.5) * k, y1)


# Fixed single-line boxes (recognition only).
TIMER = _rel(0.484, 0.010, 0.516, 0.058)
SCORE_US = _rel(0.460, 0.010, 0.474, 0.058)
SCORE_THEM = _rel(0.526, 0.010, 0.540, 0.058)
REINF = _rel(0.326, 0.012, 0.345, 0.056)
ROOM = _rel(0.430, 0.765, 0.570, 0.800)
HEALTH = _rel(0.150, 0.893, 0.200, 0.928)
# Variable regions (detection + recognition).
BANNER = _rel(0.30, 0.12, 0.70, 0.58)
KILLFEED = _rel(0.66, 0.16, 0.90, 0.38)

BANNER_SCALE = 0.5
KILLFEED_SCALE = 0.75
MIN_CONF = 0.6
NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{3,16}$")


def crop(img: np.ndarray, region: tuple[float, float, float, float]) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = region
    cx = w / 2
    xa, xb = int(max(0, cx + x0 * h)), int(min(w, cx + x1 * h))
    return img[int(y0 * h):int(y1 * h), xa:xb]


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


FLOOR_RE = re.compile(r"(EXT|1F|2F|3F|B)(?=\s*[A-Z][a-z])")


def pretty_place(text: str) -> Optional[str]:
    """OCR drops spaces in callouts: '2FMasterBedroom' -> '2F Master Bedroom'.
    Junk before the floor prefix (icons read as letters) is dropped."""
    m = FLOOR_RE.search(text)
    if not m:
        return None
    rest = text[m.end():].strip()
    rest = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" :•◆♦")
    return f"{m.group(1)} {rest}" if rest else None


def join_lines(items: list[tuple[list, str, float]]) -> list[str]:
    """Merge OCR boxes that sit on the same line ('3VS' + '2' -> '3VS 2'), top to bottom."""
    boxes = []
    for pts, text, conf in items:
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        boxes.append((min(ys), max(ys), min(xs), text))
    boxes.sort()
    lines: list[list] = []
    for y0, y1, x0, text in boxes:
        mid = (y0 + y1) / 2
        for line in lines:
            if line[0] <= mid <= line[1]:
                line[2].append((x0, text))
                line[0], line[1] = min(line[0], y0), max(line[1], y1)
                break
        else:
            lines.append([y0, y1, [(x0, text)]])
    return [" ".join(t for _, t in sorted(parts)) for _, _, parts in lines]


@dataclass
class Kill:
    killer: str
    killer_team: Optional[str]   # "us" | "them"
    victim: str
    victim_team: Optional[str]
    headshot: Optional[bool] = None


@dataclass
class HudRead:
    ts: float
    timer: Optional[int] = None          # seconds left
    timer_hidden: bool = False           # top bar visible but no timer (defuser icon or similar)
    score_us: Optional[int] = None
    score_them: Optional[int] = None
    reinforcements: Optional[int] = None
    room: Optional[str] = None
    health: Optional[int] = None
    banners: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    alive_us: Optional[int] = None
    alive_them: Optional[int] = None
    site: Optional[str] = None
    side: Optional[str] = None                        # from banner subtitles
    kills: list[Kill] = field(default_factory=list)   # lines visible now (not deduplicated)

    @property
    def in_round(self) -> bool:
        return self.timer is not None or self.timer_hidden


def parse_timer(text: str) -> Optional[int]:
    m = re.search(r"(\d)\s*[:.]\s*(\d{2})", text)
    if not m:
        return None
    mins, secs = int(m.group(1)), int(m.group(2))
    return mins * 60 + secs if secs < 60 and mins <= 4 else None


def parse_int(text: str, lo: int = 0, hi: int = 99) -> Optional[int]:
    m = re.search(r"\d+", text.replace("O", "0").replace("o", "0"))
    if not m:
        return None
    v = int(m.group())
    return v if lo <= v <= hi else None


def parse_banners(texts: list[str]) -> tuple[list[str], dict]:
    """Turn banner OCR lines into events. Returns (events, extras)."""
    events: list[str] = []
    extras: dict = {}
    joined = [_squash(t) for t in texts]
    for i, t in enumerate(joined):
        m = re.search(r"(?<!\d)(\d)VS(\d)(?!\d)", t)
        if m:
            events.append("vs")
            extras["alive_us"], extras["alive_them"] = int(m.group(1)), int(m.group(2))
        elif t.startswith("WONROUND"):
            events.append("won_round")
        elif t.startswith("LOSTROUND"):
            events.append("lost_round")
        elif t == "PREPARATIONPHASE":
            events.append("prep")
        elif t == "ACTIONPHASE":
            events.append("action")
        elif t == "DEFUSERPLANTED":
            events.append("planted")
        elif "DROPPEDTHEDEFUSER" in t:
            events.append("defuser_dropped")
        elif t == "OPERATORSLOCKED":
            events.append("operators_locked")
        elif "ENEMIESFOUNDTHEBOMBS" in t:
            events.append("enemies_found_bombs")
        elif t == "ROLESWAP":
            events.append("role_swap")
        elif t == "BOMBSFOUND":
            events.append("bombs_found")
            # The next one or two lines name the site pair.
            site = [p for p in (pretty_place(texts[j]) for j in range(i + 1, min(i + 3, len(texts)))) if p]
            if site:
                extras["site"] = " / ".join(site)
        elif "MATCHPOINT" in t:
            events.append("match_point")
        # Banner subtitles reveal the player's side.
        if any(k in t for k in ("SEARCHFORBOMBS", "PLANTTHEDEFUSER", "LOCATEBOMBSITES")):
            extras["side"] = "attack"
        elif any(k in t for k in ("FORTIFYBOMBSITES", "DEFENDBOMBSITES", "DEFENDTHEBOMB")):
            extras["side"] = "defense"
    return events, extras


def _team_colour(bgr_patch: np.ndarray) -> Optional[str]:
    """Blue name box = our team, red = enemy."""
    if bgr_patch.size == 0:
        return None
    hsv = cv2.cvtColor(bgr_patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    sat = hsv[(hsv[:, 1] > 90) & (hsv[:, 2] > 60)]
    if len(sat) < max(10, len(hsv) * 0.08):
        return None
    hue = sat[:, 0]
    blue = np.count_nonzero((hue >= 90) & (hue <= 130))
    red = np.count_nonzero((hue <= 10) | (hue >= 165))
    if max(blue, red) < len(sat) * 0.4:
        return None
    return "us" if blue > red else "them"


def coloured_pixels(region_img: np.ndarray) -> int:
    """Cheap killfeed trigger: count saturated blue/red pixels (the name boxes)."""
    small = cv2.resize(region_img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = (s > 110) & (v > 70) & (((h >= 95) & (h <= 125)) | (h <= 8) | (h >= 168))
    return int(np.count_nonzero(mask))


def white_text_pixels(region_img: np.ndarray) -> int:
    """Cheap banner trigger: count bright, unsaturated pixels."""
    small = cv2.resize(region_img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    return int(np.count_nonzero((hsv[..., 2] > 235) & (hsv[..., 1] < 35)))


class HudReader:
    """Stateless per-frame reading plus light state for triggers. Not thread-safe; one per thread."""

    def __init__(self, player_name: str = "") -> None:
        from rapidocr_onnxruntime import RapidOCR

        # Defaults upscale every crop to a 736 px short side before detection, which made banner
        # reads take seconds. "max" only ever downscales. Two threads keep the game's CPU free.
        self.ocr = RapidOCR(det_limit_side_len=960, det_limit_type="max",
                            intra_op_num_threads=2, inter_op_num_threads=1)
        self.player_name = player_name.lower()
        self._last_banner_sig = -1
        self._last_banner_ts = 0.0
        self._last_kf_sig = -1
        self._last_kf_ts = 0.0
        self._last_banners: list[str] = []
        self._last_in_round_ts = 0.0
        self._banner_found = False
        self._last_kills: list[Kill] = []

    # ------------------------------------------------------------------ OCR helpers
    def _rec(self, img: np.ndarray) -> tuple[str, float]:
        if img.size == 0:
            return "", 0.0
        res, _ = self.ocr(img, use_det=False, use_cls=False, use_rec=True)
        if not res:
            return "", 0.0
        text, conf = res[0][0], float(res[0][1])
        return text.strip(), conf

    def _detect(self, img: np.ndarray, scale: float) -> list[tuple[list, str, float]]:
        if img.size == 0:
            return []
        if scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        res, _ = self.ocr(img, use_cls=False)
        out = []
        for box, text, conf in res or []:
            pts = [[p[0] / scale, p[1] / scale] for p in box]
            out.append((pts, text, float(conf)))
        return out

    # ------------------------------------------------------------------ main read
    def read(self, img: np.ndarray, force: bool = False) -> HudRead:
        now = time.time()
        r = HudRead(ts=now)

        text, conf = self._rec(crop(img, TIMER))
        if conf >= MIN_CONF:
            r.timer = parse_timer(text)
        if r.timer is not None or self._top_bar_visible(img):
            for region, attr in ((SCORE_US, "score_us"), (SCORE_THEM, "score_them")):
                t, c = self._rec(crop(img, region))
                if c >= 0.5:
                    setattr(r, attr, parse_int(t, 0, 9))
            if r.timer is None:
                r.timer_hidden = True
            t, c = self._rec(crop(img, REINF))
            v = parse_int(t, 0, 10) if c >= 0.5 else None
            r.reinforcements = v
        if r.in_round:  # menus put unrelated text in these boxes
            t, c = self._rec(crop(img, ROOM))
            if c >= 0.5 and len(t.strip()) >= 4:
                # OCR sometimes drops the floor prefix; keep the room name anyway.
                r.room = pretty_place(t) or re.sub(r"\s+", " ", t).strip()
            t, c = self._rec(crop(img, HEALTH))
            if c >= MIN_CONF:
                m = re.match(r"\s*(\d{1,3})\s*[/／]", t)
                if m:
                    r.health = int(m.group(1))

        self._read_banners(img, r, now, force)
        # The killfeed only exists in a round (incl. kill cams and spectating, where the timer can be
        # hidden). Gating on "recently in a round" keeps menu screens from producing fake kills.
        if r.in_round or r.events:
            self._last_in_round_ts = now
        if force or now - self._last_in_round_ts < 30:
            self._read_killfeed(img, r, now, force)
        return r

    def _team_of(self, name: str, colour: Optional[str]) -> Optional[str]:
        if self.player_name and name.lower() == self.player_name:
            return "us"
        return colour

    def _top_bar_visible(self, img: np.ndarray) -> bool:
        """The top bar has a blue block left of the timer and a red block right of it."""
        left = crop(img, _rel(0.40, 0.005, 0.45, 0.06))
        right = crop(img, _rel(0.55, 0.005, 0.60, 0.06))
        return _team_colour(left) == "us" and _team_colour(right) == "them"

    def _read_banners(self, img: np.ndarray, r: HudRead, now: float, force: bool) -> None:
        region = crop(img, BANNER)
        sig = white_text_pixels(region)
        changed = abs(sig - self._last_banner_sig) > max(40, 0.25 * max(self._last_banner_sig, 1))
        # Bright scenes (fog, snow, loading screens) look like banner text. Re-run an unchanged
        # region only while it actually held banner text last time.
        due = sig > 25 and (changed or (self._banner_found and now - self._last_banner_ts > 2.5))
        if not force and (not due or now - self._last_banner_ts < 1.0):
            r.banners = []
            return
        self._last_banner_sig, self._last_banner_ts = sig, now
        items = [it for it in self._detect(region, BANNER_SCALE) if it[2] >= MIN_CONF]
        # Unknown lines (teammate name tags, pickups) are simply ignored by parse_banners.
        lines = join_lines(items)
        r.banners = lines
        r.events, extras = parse_banners(lines)
        self._banner_found = bool(r.events)
        r.alive_us = extras.get("alive_us")
        r.alive_them = extras.get("alive_them")
        r.site = extras.get("site")
        r.side = extras.get("side")

    def _read_killfeed(self, img: np.ndarray, r: HudRead, now: float, force: bool) -> None:
        region = crop(img, KILLFEED)
        sig = coloured_pixels(region)
        changed = abs(sig - self._last_kf_sig) > max(25, 0.2 * max(self._last_kf_sig, 1))
        if not force and (sig < 30 or not changed or now - self._last_kf_ts < 0.8):
            r.kills = [] if sig < 30 else self._last_kills
            if sig < 30:
                self._last_kf_sig = sig
            return
        self._last_kf_sig, self._last_kf_ts = sig, now
        items = self._detect(region, KILLFEED_SCALE)
        names = []
        for pts, text, conf in items:
            t = text.strip()
            if conf < MIN_CONF or not NAME_RE.match(t):
                continue  # system messages ("Friendly Fire ...") are long or have spaces
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x0, x1, y0, y1 = int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys))
            team = _team_colour(region[max(0, y0 - 4):y1 + 4, max(0, x0 - 6):x1 + 6])
            names.append((((y0 + y1) / 2), (x0 + x1) / 2, t, team, y1 - y0))
        kills = []
        names.sort()
        used = set()
        for i, (yc, xc, t, team, hgt) in enumerate(names):
            if i in used:
                continue
            same = [j for j in range(i + 1, len(names)) if j not in used and abs(names[j][0] - yc) < hgt * 0.6]
            if not same:
                continue
            j = min(same, key=lambda k: abs(names[k][0] - yc))
            a, b = sorted([names[i], names[j]], key=lambda n: n[1])   # left = killer
            ta, tb = self._team_of(a[2], a[3]), self._team_of(b[2], b[3])
            # The player's own line has a paler box; if one side is unclear, assume opposite teams.
            if ta is None and tb is not None:
                ta = "us" if tb == "them" else "them"
            if tb is None and ta is not None:
                tb = "us" if ta == "them" else "them"
            if ta is None:
                continue
            used.update({i, j})
            kills.append(Kill(killer=a[2], killer_team=ta, victim=b[2], victim_team=tb))
        r.kills = kills
        self._last_kills = kills


class HudThread:
    """Runs the HUD reader on the newest frame about once a second and calls back with reads."""

    def __init__(self, source, on_read, period: float = 1.0, player_name: str = "") -> None:
        self.player_name = player_name
        self.source = source
        self.on_read = on_read
        self.period = period
        self.enabled = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hud", daemon=True)
        self.last_ms = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            reader = HudReader(self.player_name)
        except Exception:
            log.exception("HUD reader unavailable (rapidocr_onnxruntime missing?)")
            return
        last_ts = 0.0
        while not self._stop.wait(self.period):
            if not self.enabled:
                continue
            latest = self.source.latest()
            if latest is None or latest[0] == last_ts:
                continue
            last_ts, img = latest
            started = time.time()
            try:
                read = reader.read(img)
            except Exception:
                log.exception("HUD read failed")
                continue
            self.last_ms = (time.time() - started) * 1000
            self.on_read(read)
