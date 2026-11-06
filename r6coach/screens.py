"""Local screen-type recognition: tells menus, ban/pick screens and live rounds apart on your PC.

The cloud vision read (~$0.012 each) was also being used just to learn *which* screen is up.
This classifier answers that locally from a few cheap signals, so the watcher only pays for a
cloud read where the details matter (bans, picks, the first prep read, the scoreboard).

Signals, cheapest first:
- the in-round top bar (blue block left of the timer, red block right of it) and the timer;
- recognition-only OCR (~10-20 ms each) of a few fixed title boxes: the operator-select tab row,
  the "BAN A DEFENDER" title, the main-menu nav row, the centre title ("MAP BAN"/"MAP SELECTION"),
  the pause menu's RESUME, the spectator card ("WATCHING"/"KILLED BY") and the match-end tabs;
- the top-right mode/map label ("RANKED / CHALET"), read for the map name.
Boxes are only read when a pixel check says text could be there, and the checks stop at the first
confident answer, so a typical frame costs 2-4 OCR calls.

Regions were measured on 3440x1440 (21:9) and stored centre-relative in units of screen height,
like the HUD reader. Other aspect ratios are unverified.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .hud import TIMER, _rel, _squash, _team_colour, crop, parse_timer
from .models import Screen

# --------------------------------------------------------------------------- regions
# (frame fractions measured on 3440x1440, converted to centre-relative height units)
TABS = _rel(0.140, 0.122, 0.410, 0.182)          # LOCATIONS  OPERATORS  LOADOUT  READY
BAN_TITLE = _rel(0.140, 0.148, 0.270, 0.190)     # BAN A DEFENDER / BAN AN ATTACKER
NAV = _rel(0.140, 0.000, 0.540, 0.040)           # PLAY OPERATORS BATTLE PASS LOCKER CAREER ESPORTS SHOP
TITLE = _rel(0.400, 0.105, 0.600, 0.175)         # MAP BAN / MAP SELECTION
RESUME = _rel(0.730, 0.085, 0.810, 0.135)        # pause menu
SPECTATE = _rel(0.150, 0.785, 0.240, 0.825)      # WATCHING / KILLED BY (spectator card)
END_TABS = _rel(0.370, 0.045, 0.620, 0.095)      # TEAM LINEUP  RANKED  MATCH RECAP
MODE = _rel(0.800, 0.004, 0.870, 0.036)          # BOMB / RANKED / UNRANKED (in a match)
MAP = _rel(0.800, 0.030, 0.870, 0.068)           # HOUSE / CHALET (in a match)
END_MODE = _rel(0.800, 0.030, 0.870, 0.062)      # same label, lower, on the match-end screen
END_MAP = _rel(0.800, 0.058, 0.870, 0.092)
UNDER_TIMER = _rel(0.460, 0.058, 0.540, 0.095)   # ROUND n / MATCH POINT
TEAM_BAR_L = _rel(0.350, 0.000, 0.450, 0.035)    # YOUR TEAM (ban and map phases)
SCORE_YOUR = _rel(0.180, 0.380, 0.290, 0.440)    # Tab scoreboard: big "YOUR TEAM"
SB_ATK = _rel(0.435, 0.115, 0.475, 0.160)        # Tab scoreboard column header "ATK"
CARD_ROUND = _rel(0.400, 0.240, 0.600, 0.340)    # ROUND n title card (bans) / ROLE SWAP
CARD_BAN = _rel(0.380, 0.440, 0.620, 0.540)      # BAN PHASE / ROLE SWAP card; BOMB on the match intro
ROUND_BANNER = _rel(0.390, 0.350, 0.610, 0.430)  # WON ROUND n / LOST ROUND n (no top bar then)
END_CARD = _rel(0.360, 0.660, 0.640, 0.790)      # VICTORY / DEFEAT / YOUR TEAM WINS / MVP

MIN_CONF = 0.6

MAPS = ["Bank", "Border", "Chalet", "Clubhouse", "Coastline", "Consulate", "Emerald Plains", "Favela",
        "Fortress", "Hereford Base", "House", "Kafe Dostoyevsky", "Kanal", "Lair", "Nighthaven Labs",
        "Oregon", "Outback", "Presidential Plane", "Skyscraper", "Stadium Bravo", "Theme Park", "Tower",
        "Villa", "Yacht", "Calypso Casino"]
_MAP_KEYS = {_squash(m): m for m in MAPS}
_MAP_KEYS.update({"KAFE": "Kafe Dostoyevsky", "PLANE": "Presidential Plane", "STADIUM": "Stadium Bravo",
                  "HEREFORD": "Hereford Base", "NIGHTHAVEN": "Nighthaven Labs", "EMERALD": "Emerald Plains",
                  "THEMEPARK": "Theme Park", "CASINO": "Calypso Casino"})


def match_map(text: str) -> Optional[str]:
    """Fuzzy map-name lookup for an OCR'd label ("CHALET", "CHAIET", "KAFE DOSTOYEVSKY")."""
    t = _squash(text)
    if len(t) < 4:
        return None
    if t in _MAP_KEYS:
        return _MAP_KEYS[t]
    best = difflib.get_close_matches(t, list(_MAP_KEYS), n=1, cutoff=0.75)
    return _MAP_KEYS[best[0]] if best else None


def _has(text: str, *keys: str) -> bool:
    return any(k in text for k in keys)


def _fuzzy_in(text: str, key: str, cutoff: float = 0.8) -> bool:
    """True when `key` appears in `text`, allowing an OCR slip or two ("BANADEFENOER")."""
    if key in text:
        return True
    n = len(key)
    if len(text) < n - 1:
        return False
    sm = difflib.SequenceMatcher(None, "", key, autojunk=False)
    for i in range(0, max(1, len(text) - n + 2)):
        sm.set_seq1(text[i:i + n])
        if sm.ratio() >= cutoff:
            return True
    return False


@dataclass
class LocalScreen:
    screen: Screen
    confidence: float
    title: str = ""                   # the text that decided it
    map: Optional[str] = None
    mode: Optional[str] = None        # top-right mode label: "RANKED", "BOMB", ...
    timer: Optional[int] = None
    in_round: bool = False            # top bar with timer: prep or action phase
    # Cloud-read hints for the watcher. card: a title card / waiting screen of a detail screen type
    # with nothing to read on it. key: a sub-screen whose details only need one read per round
    # ("BANADEFENDER", "BANANATTACKER", "result", "MAPBAN", "MAPSELECTION", "scoreboard").
    card: bool = False
    key: str = ""
    ms: float = 0.0
    ocr_calls: int = 0

    @property
    def confident(self) -> bool:
        return self.confidence >= 0.75


class ScreenClassifier:
    """One instance per thread (RapidOCR sessions are not shared)."""

    def __init__(self, ocr=None) -> None:
        if ocr is None:
            from rapidocr_onnxruntime import RapidOCR

            ocr = RapidOCR(det_limit_side_len=960, det_limit_type="max",
                           intra_op_num_threads=2, inter_op_num_threads=1)
        self.ocr = ocr
        self._calls = 0

    # ------------------------------------------------------------------ helpers
    def _rec(self, img: np.ndarray, region) -> tuple[str, float]:
        c = crop(img, region)
        if c.size == 0:
            return "", 0.0
        h = c.shape[0]
        # Recognition works on a 48 px line height; big title boxes are scaled down first.
        if h > 96:
            c = cv2.resize(c, None, fx=64 / h, fy=64 / h, interpolation=cv2.INTER_AREA)
        self._calls += 1
        res, _ = self.ocr(c, use_det=False, use_cls=False, use_rec=True)
        if not res:
            return "", 0.0
        return res[0][0].strip(), float(res[0][1])

    def _text(self, img: np.ndarray, region, min_conf: float = 0.5) -> str:
        t, c = self._rec(img, region)
        return _squash(t) if c >= min_conf else ""

    @staticmethod
    def _bright_text(img: np.ndarray, region, thr: int = 200) -> float:
        """Share of bright, unsaturated pixels: white UI text."""
        c = crop(img, region)
        if c.size == 0:
            return 0.0
        c = c[::2, ::2]
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        return float(np.count_nonzero((hsv[..., 2] > thr) & (hsv[..., 1] < 60))) / (hsv.shape[0] * hsv.shape[1])

    @staticmethod
    def _top_bar(img: np.ndarray) -> bool:
        left = crop(img, _rel(0.40, 0.005, 0.45, 0.06))
        right = crop(img, _rel(0.55, 0.005, 0.60, 0.06))
        return _team_colour(left) == "us" and _team_colour(right) == "them"

    def _map_label(self, img: np.ndarray, end: bool = False) -> tuple[Optional[str], Optional[str]]:
        mode_t, mc = self._rec(img, END_MODE if end else MODE)
        map_t, pc = self._rec(img, END_MAP if end else MAP)
        mode = _squash(mode_t) if mc >= MIN_CONF and mode_t else None
        return (match_map(map_t) if pc >= 0.5 else None), mode

    # ------------------------------------------------------------------ main entry points
    def classify(self, img: np.ndarray) -> tuple[Screen, float, str]:
        r = self.read(img)
        return r.screen, r.confidence, r.title

    def read(self, img: np.ndarray, want_map: bool = True) -> LocalScreen:
        t0 = time.perf_counter()
        self._calls = 0
        r = self._decide(img, want_map)
        r.ms = (time.perf_counter() - t0) * 1000
        r.ocr_calls = self._calls
        return r

    def _decide(self, img: np.ndarray, want_map: bool) -> LocalScreen:
        tt, tc = self._rec(img, TIMER)
        timer = parse_timer(tt) if tc >= MIN_CONF else None
        bar = self._top_bar(img)

        if timer is not None or bar:
            return self._with_bar(img, timer, tt, want_map)
        return self._without_bar(img, want_map)

    def _with_bar(self, img: np.ndarray, timer: Optional[int], timer_text: str, want_map: bool) -> LocalScreen:
        """The match top bar (or at least its timer) is up: ban/pick screens, a live round, pause."""
        # Ban phases (map and operator) show "YOUR TEAM" / "ENEMY TEAM" in the bar instead of icons.
        if self._bright_text(img, TEAM_BAR_L, 170) > 0.03:
            strip = self._text(img, TEAM_BAR_L, 0.6)
            if strip.startswith("YOU") or _fuzzy_in(strip, "YOURTEAM", 0.7):
                title = self._text(img, TITLE)
                for key in ("MAPSELECTION", "MAPBAN"):
                    if _fuzzy_in(title, key):
                        return LocalScreen(Screen.map_ban, 0.95, title, timer=timer, key=key)
                r = self._with_map(img, LocalScreen(Screen.operator_ban, 0.9, strip, timer=timer), True)
                if r.map is None and r.mode is None:
                    # No map label yet: still the map vote ("START OF MAP BAN PHASE", "MAP SELECTED").
                    r.screen, r.confidence, r.card = Screen.map_ban, 0.8, True
                    return r
                ban = self._text(img, BAN_TITLE) if self._bright_text(img, BAN_TITLE) > 0.01 else ""
                for key in ("BANADEFENDER", "BANANATTACKER"):
                    if _fuzzy_in(ban, key):
                        r.title, r.key = ban, key
                        return r
                if self._flat_body(img):
                    r.card = True          # waiting for the ban phase to start: nothing to read yet
                else:
                    r.key = "result"       # ban result splash: "MIRA (ban) ACE"
                return r
        if self._bright_text(img, TABS) > 0.01:
            tabs = self._text(img, TABS)
            n = sum(_fuzzy_in(tabs, k) for k in ("LOCATIONS", "OPERATORS", "LOADOUT", "READY"))
            if n >= 2:
                return self._with_map(img, LocalScreen(Screen.operator_select, 0.95, tabs, timer=timer), want_map)
        if self._bright_text(img, RESUME) > 0.005:
            res = self._text(img, RESUME)
            if _fuzzy_in(res, "RESUME"):
                return LocalScreen(Screen.other, 0.9, res, timer=timer)
        if self._bright_text(img, SPECTATE) > 0.005:
            spec = self._text(img, SPECTATE)
            if _fuzzy_in(spec, "WATCHING") or _fuzzy_in(spec, "KILLEDBY"):
                return LocalScreen(Screen.spectating, 0.9, spec, timer=timer, in_round=True)
        if self._scoreboard(img):
            return LocalScreen(Screen.scoreboard, 0.9, "ATK DEF", timer=timer, key="scoreboard")
        if timer is not None:
            phase = Screen.prep_phase if timer <= 45 else Screen.action_phase
            return LocalScreen(phase, 0.85, timer_text, timer=timer, in_round=True)
        # Bar without a timer: defuser planted (in round), or a ban result splash.
        under = self._text(img, UNDER_TIMER)
        if _fuzzy_in(under, "ROUND", 0.75) or _fuzzy_in(under, "MATCHPOINT") or _fuzzy_in(under, "OVERTIME"):
            return LocalScreen(Screen.action_phase, 0.75, under, in_round=True)
        return LocalScreen(Screen.action_phase, 0.5, "top bar", in_round=True)

    def _without_bar(self, img: np.ndarray, want_map: bool) -> LocalScreen:
        """Menus, title cards, spectator view, match end, loading."""
        nav = self._text(img, NAV)
        if _fuzzy_in(nav, "FINDINGMATCH") or _fuzzy_in(nav, "MATCHSTARTED") or _fuzzy_in(nav, "MATCHFOUND"):
            return LocalScreen(Screen.matchmaking, 0.9, nav)
        if _fuzzy_in(nav, "OPERATORS", 0.75) and (_has(nav, "PLAY", "PIAY") or _fuzzy_in(nav, "BATTLEPASS", 0.7)):
            return LocalScreen(Screen.main_menu, 0.9, nav)
        if _fuzzy_in(nav, "CUSTOMGAME", 0.75):   # custom-game lobby banner above the nav row
            return LocalScreen(Screen.main_menu, 0.85, nav)
        if self._bright_text(img, RESUME) > 0.005:
            res = self._text(img, RESUME)
            if _fuzzy_in(res, "RESUME"):
                return LocalScreen(Screen.other, 0.9, res)
        if self._bright_text(img, SPECTATE) > 0.005:
            spec = self._text(img, SPECTATE)
            if _fuzzy_in(spec, "WATCHING") or _fuzzy_in(spec, "KILLEDBY"):
                return LocalScreen(Screen.spectating, 0.9, spec)
        if self._blank(img):
            return LocalScreen(Screen.loading, 0.85, "blank")
        if self._bright_text(img, ROUND_BANNER) > 0.005:
            banner = self._text(img, ROUND_BANNER)
            if _fuzzy_in(banner, "WONROUND") or _fuzzy_in(banner, "LOSTROUND"):
                # The HUD reader also sees this banner and moves the state to round_end.
                return LocalScreen(Screen.round_end, 0.9, banner)
        if self._bright_text(img, CARD_BAN) > 0.005:
            card = self._text(img, CARD_BAN)
            # Full-screen title cards: the HUD reads ROLE SWAP itself, the rest carry no details.
            if _fuzzy_in(card, "BANPHASE") or _fuzzy_in(card, "ROLESWAP") or _fuzzy_in(card, "BANSCLEARED"):
                return LocalScreen(Screen.operator_ban, 0.9, card, card=True)
            if _fuzzy_in(card, "MAPBANPHASE", 0.75):
                return LocalScreen(Screen.map_ban, 0.9, card, card=True)
            if card in ("BOMB", "SECUREAREA", "HOSTAGE"):   # "YOUR TEAM / ENEMY TEAM / BOMB" intro
                return LocalScreen(Screen.loading, 0.8, card)
        rnd = self._text(img, CARD_ROUND)
        if re.fullmatch(r"ROUN[DO0]\w{0,3}(MATCHPOINT|OVERTIME)?", rnd):   # ROUND n title card
            return LocalScreen(Screen.operator_ban, 0.8, rnd, card=True)
        end = self._text(img, END_TABS)
        if _fuzzy_in(end, "TEAMLINEUP") or _fuzzy_in(end, "MATCHRECAP"):
            return self._with_map(img, LocalScreen(Screen.match_end, 0.95, end), want_map, end=True)
        card = self._text(img, END_CARD)    # (blue "YOUR TEAM WINS" fails a white-text gate)
        if _has(card, "VICTORY", "DEFEAT", "WINS", "MVP", "DRAW"):
            return LocalScreen(Screen.match_end, 0.85, card)
        if self._scoreboard(img):
            return LocalScreen(Screen.scoreboard, 0.9, "ATK DEF", key="scoreboard")
        title = self._text(img, TITLE)
        if title in ("RANKED", "UNRANKED", "QUICKMATCH", "STANDARD"):   # "RANKED  YOUR TEAM VS ENEMY TEAM"
            return LocalScreen(Screen.loading, 0.8, title)
        return LocalScreen(Screen.other, 0.3, "")

    def _scoreboard(self, img: np.ndarray) -> bool:
        if self._bright_text(img, SB_ATK) < 0.005:
            return False
        return self._text(img, SB_ATK, 0.8) == "ATK"

    @staticmethod
    def _flat_body(img: np.ndarray) -> bool:
        """Below the top bar there is only a dark backdrop (the wait before a ban turn)."""
        body = crop(img, _rel(0.10, 0.15, 0.90, 0.95))
        small = cv2.resize(body, (128, 64), interpolation=cv2.INTER_AREA)
        v = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 2].astype(np.float32)
        return float(v.std()) < 20

    @staticmethod
    def _blank(img: np.ndarray) -> bool:
        """Loading screens: flat, or the blue-grey loading backdrop, with no UI."""
        small = cv2.resize(img, (172, 72), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        v = hsv[..., 2].astype(np.float32)
        mv, sv, ms = float(v.mean()), float(v.std()), float(hsv[..., 1].mean())
        return (sv < 12 and mv < 110) or (75 < mv < 95 and sv < 22 and 65 < ms < 90)

    def _with_map(self, img: np.ndarray, r: LocalScreen, want_map: bool, end: bool = False) -> LocalScreen:
        if want_map:
            r.map, r.mode = self._map_label(img, end)
        return r
