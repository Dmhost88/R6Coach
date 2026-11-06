"""Local operator-card reader: who you picked, who your teammates picked, and the bans, in ~100 ms.

The cloud vision read takes 3-8 s, so the coach learned the player's lock late. This reads the
same facts off fixed boxes of the operator-select and ban screens with recognition-only OCR
(no text detection), the way hud.py reads the HUD.

Where the facts are (measured on 3440x1440, stored centre-relative in units of screen height
like hud.py, so other 21:9 and 16:9 sizes land in the same places):

operator_select (tabs LOCATIONS / OPERATORS / LOADOUT / READY under the top bar):
- READY tab (and LOADOUT with "show team loadout"): five player cards; operator name on each
  card at y 0.68-0.72, the player's gamer tag on a grey bar under it at y 0.75-0.78. The
  player's own card is the rightmost one (slot 5), confirmed by `player_name` when given.
  This is the only place teammates' picks are written as text, and the only read that
  separates the player's own card from the teammates' (the cloud read mixed them).
- OPERATORS tab: the detail panel heading (x 0.51-0.64, y 0.22-0.27) is the operator the
  player is looking at. It is grey when the operator cannot be taken (owned by a teammate, not
  unlocked). It is a hover, not a lock: the lock shows in the top bar.
- LOADOUT tab: heading at x 0.42-0.52, y 0.23-0.27 names the picked operator.
- Top bar, left of the timer: five team slots; slot 5 (next to the timer, grey-highlighted) is
  the player's own. A slot shows "?" until that player picks, then the operator's icon. So
  "slot 5 is not a question mark" means the player has locked something.

operator_ban:
- "BAN A DEFENDER / ATTACKER" grid: heading at x 0.44-0.51, y 0.62-0.68 is the ban the player
  is hovering (not confirmed) -> `ban_candidate`. Already-banned operators are only icons.
- Reveal splash: "MIRA (no-entry) ACE" in big type at y 0.73-0.81, left name right-aligned at
  x 0.39, right name left-aligned at x 0.61. "NO BAN" means none. -> `banned`.
- Overtime summary: two rows of up to three names (attackers y 0.46-0.51, defenders
  y 0.78-0.82) centred at x 0.32 / 0.50 / 0.67. -> `banned`.

Everything is the player's own menus. Nothing here looks at the 3D world or enemy positions.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .hud import _rel, _squash, crop

log = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent / "knowledge" / "data" / "operators.yaml"

# --------------------------------------------------------------------------- regions
TABS = _rel(0.140, 0.122, 0.410, 0.182)            # LOCATIONS  OPERATORS  LOADOUT  READY
TAB_LINE = _rel(0.140, 0.168, 0.410, 0.186)        # red progress line + dot under the active tab
TAB_CENTRES = (0.173, 0.249, 0.322, 0.386)         # frame-x of the four tab labels
TAB_NAMES = ("locations", "operators", "loadout", "ready")
BAN_TITLE = _rel(0.140, 0.148, 0.270, 0.190)       # BAN A DEFENDER / BAN AN ATTACKER
TEAM_BAR_L = _rel(0.350, 0.000, 0.450, 0.035)      # YOUR TEAM (ban and map phases)

PANEL_NAME = _rel(0.505, 0.210, 0.660, 0.280)      # OPERATORS tab heading
LOADOUT_NAME = _rel(0.412, 0.222, 0.530, 0.272)    # LOADOUT tab heading
LOADOUT_READY = _rel(0.212, 0.232, 0.258, 0.286)   # big READY button on the LOADOUT tab

CARD_X0, CARD_PITCH = 0.1527, 0.1407               # five cards on READY; frame-x of card 1, pitch
# Offsets within a card. The boxes cover both layouts: the READY tab (name y 0.683-0.722, tag
# y 0.755-0.782) and the final line-up shown once operators are locked, which has no tab row and
# sits ~0.01 lower (name y 0.691-0.732, tag y 0.765-0.795).
CARD_OP = (0.0255, 0.676, 0.118, 0.738)            # operator name
CARD_PLAYER = (0.003, 0.748, 0.105, 0.798)         # gamer tag on the grey bar
CARD_BAR = (0.010, 0.762, 0.120, 0.786)            # the grey bar itself (presence check)
TAB_LABELS = [_rel(cx - 0.035, 0.122, cx + 0.035, 0.170) for cx in TAB_CENTRES]

SLOT_X0, SLOT_PITCH, SLOT_W = 0.3480, 0.0186, 0.0148   # top-bar team slots (icon or "?")
SLOT_Y0, SLOT_Y1 = 0.010, 0.045

BAN_PICK_NAME = _rel(0.438, 0.618, 0.600, 0.684)   # hovered ban on the grid
BAN_SPLASH_L = _rel(0.200, 0.715, 0.398, 0.822)    # reveal: defender ban (blue side)
BAN_SPLASH_R = _rel(0.605, 0.715, 0.800, 0.822)    # reveal: attacker ban (red side)
BAN_SUMMARY_ROWS = ((0.452, 0.516), (0.768, 0.832))
BAN_SUMMARY_COLS = ((0.262, 0.378), (0.442, 0.558), (0.612, 0.728))

MIN_CONF = 0.5
BAN_CONF = 0.6      # ban names read at 0.82-0.99; art misread as letters stays below 0.45
UI_WORDS = {"READY", "RANDOM", "NOBAN", "NOVOTE", "LOCATIONS", "OPERATORS", "LOADOUT", "YOURTEAM",
            "ENEMYTEAM", "ROUND", "BANPHASE", "CURRENT", "NEXT", "REVEAL", "OVERTIME", "MATCHPOINT"}


def _card_region(i: int, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0 = CARD_X0 + CARD_PITCH * i
    return _rel(x0 + box[0], box[1], x0 + box[2], box[3])


def _slot_region(i: int) -> tuple[float, float, float, float]:
    x0 = SLOT_X0 + SLOT_PITCH * i
    return _rel(x0, SLOT_Y0, x0 + SLOT_W, SLOT_Y1)


CARD_OPS = [_card_region(i, CARD_OP) for i in range(5)]
CARD_PLAYERS = [_card_region(i, CARD_PLAYER) for i in range(5)]
CARD_BARS = [_card_region(i, CARD_BAR) for i in range(5)]
SLOTS = [_slot_region(i) for i in range(5)]
BAN_SUMMARY = [_rel(x0, y0, x1, y1) for (y0, y1) in BAN_SUMMARY_ROWS for (x0, x1) in BAN_SUMMARY_COLS]


# --------------------------------------------------------------------------- names
def _key(s: str) -> str:
    """OCR text -> comparable key: uppercase letters only, accents stripped, digit look-alikes
    fixed ('NØKK' / 'N0KK' -> 'NOKK')."""
    s = unicodedata.normalize("NFKD", s.replace("ø", "o").replace("Ø", "O"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper().replace("0", "O").replace("1", "I").replace("5", "S").replace("8", "B")
    return re.sub(r"[^A-Z]", "", s)


def load_operator_names(path: Path | str = DATA) -> dict[str, str]:
    """{alias: canonical name} from operators.yaml (names plus their aliases)."""
    import yaml

    with open(path, encoding="utf-8") as f:
        ops = (yaml.safe_load(f) or {}).get("operators", [])
    names: dict[str, str] = {}
    for op in ops:
        names[op["name"]] = op["name"]
        for a in op.get("aliases") or []:
            names[a] = op["name"]
    return names


class OperatorMatcher:
    """Fuzzy OCR-text -> canonical operator name. Rejects UI words and junk."""

    def __init__(self, names: dict[str, str] | list[str]) -> None:
        if not isinstance(names, dict):
            names = {n: n for n in names}
        self.index: dict[str, str] = {}
        for alias, canon in names.items():
            k = _key(alias)
            if k:
                self.index[k] = canon
        self.keys = list(self.index)

    def match(self, text: str) -> tuple[Optional[str], float]:
        """Returns (name, score). Tries the text as read, then with one stray character (an icon
        read as a letter) dropped from either end."""
        k = _key(text)
        if len(k) < 2 or k in UI_WORDS:
            return None, 0.0
        if k in self.index:
            return self.index[k], 1.0
        best, best_score = None, 0.0
        for cand in (k, k[1:], k[:-1], k[1:-1]):
            if len(cand) < 2:
                continue
            if cand in self.index:
                return self.index[cand], 0.95
            cutoff = 0.8 if len(cand) <= 4 else 0.75
            close = difflib.get_close_matches(cand, self.keys, n=1, cutoff=cutoff)
            if close:
                score = difflib.SequenceMatcher(None, cand, close[0]).ratio()
                if score > best_score:
                    best, best_score = self.index[close[0]], score
        return best, best_score


# --------------------------------------------------------------------------- result
@dataclass
class OpCards:
    my_operator: Optional[str] = None
    teammates: list[str] = field(default_factory=list)   # the other four players only
    banned: list[str] = field(default_factory=list)      # confirmed bans shown on screen
    screen: str = "unknown"                              # operator_select | operator_ban | unknown
    confidence: float = 0.0
    ms: float = 0.0
    # Extras for the watcher.
    tab: str = ""                     # active tab on operator_select: locations/operators/loadout/ready
    source: str = ""                  # where my_operator/teammates came from: cards | loadout | panel | ""
    locked: Optional[bool] = None     # top bar: the player's own slot shows an icon (picked), not "?"
    picked_slots: int = 0             # top bar: how many of the five team slots show an icon
    ban_candidate: Optional[str] = None   # ban grid: the operator the player is hovering, unconfirmed
    texts: dict[str, str] = field(default_factory=dict)  # raw OCR per box, for debugging

    @property
    def teammates_known(self) -> bool:
        """True only when teammates were read from the five cards (other tabs show icons only)."""
        return self.source == "cards"


# --------------------------------------------------------------------------- reader
class OperatorCardReader:
    """One instance per thread (RapidOCR sessions are not shared across threads).

    `ocr` may be an existing RapidOCR instance (e.g. the ScreenClassifier's) so only one model
    set is loaded. `player_name` (the config's player_name) confirms which card is the player's.
    """

    def __init__(self, names: dict[str, str] | list[str] | None = None, player_name: str = "",
                 ocr=None) -> None:
        if ocr is None:
            from rapidocr_onnxruntime import RapidOCR

            ocr = RapidOCR(det_limit_side_len=960, det_limit_type="max",
                           intra_op_num_threads=2, inter_op_num_threads=1)
        self.ocr = ocr
        self.matcher = OperatorMatcher(names if names is not None else load_operator_names())
        self.player_name = player_name.strip().lower()
        self.ocr_calls = 0
        self._own_confirmed_until = 0.0   # gamer tag on the rightmost card matched recently

    # ------------------------------------------------------------------ OCR helpers
    def _rec(self, img: np.ndarray, region) -> tuple[str, float]:
        """Recognition-only OCR of one fixed box (~20 ms). One crop per call on purpose: batching
        several crops through RapidOCR's recognizer was measured 5-10x slower per crop."""
        c = crop(img, region)
        if c.size == 0 or c.shape[0] < 4 or c.shape[1] < 4:
            return "", 0.0
        h = c.shape[0]
        if h > 96:   # recognition runs at 48 px line height; shrink tall boxes with area filtering
            c = cv2.resize(c, None, fx=64 / h, fy=64 / h, interpolation=cv2.INTER_AREA)
        self.ocr_calls += 1
        res, _ = self.ocr(np.ascontiguousarray(c), use_det=False, use_cls=False, use_rec=True)
        if not res:
            return "", 0.0
        return res[0][0].strip(), float(res[0][1])

    @staticmethod
    def _bright(img: np.ndarray, region, thr: int = 200) -> float:
        """Share of bright, unsaturated pixels (white UI text)."""
        c = crop(img, region)
        if c.size == 0:
            return 0.0
        c = c[::2, ::2]
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        return float(np.count_nonzero((hsv[..., 2] > thr) & (hsv[..., 1] < 60))) / (hsv.shape[0] * hsv.shape[1])

    @staticmethod
    def _grey_bar(img: np.ndarray, region) -> bool:
        """The light grey gamer-tag bar under a READY card."""
        c = crop(img, region)
        if c.size == 0:
            return False
        hsv = cv2.cvtColor(c[::2, ::2], cv2.COLOR_BGR2HSV)
        v, s = hsv[..., 2], hsv[..., 1]
        return float(np.count_nonzero((v > 150) & (s < 50))) / v.size > 0.5

    @staticmethod
    def _active_tab(img: np.ndarray) -> str:
        """A filled red dot sits on the progress line under the active tab (the line itself is
        thin, so the dot is where the red column count peaks)."""
        c = crop(img, TAB_LINE)
        if c.size == 0:
            return ""
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        red = (hsv[..., 1] > 120) & (hsv[..., 2] > 120) & ((hsv[..., 0] <= 8) | (hsv[..., 0] >= 170))
        cols = np.count_nonzero(red, axis=0)
        if cols.max() < 3:
            return ""
        peak = np.flatnonzero(cols >= cols.max() * 0.6)
        h, w = img.shape[:2]
        fx = (w / 2 + TAB_LINE[0] * h + float(peak.mean())) / w     # back to a frame fraction
        i = int(np.argmin([abs(fx - cx) for cx in TAB_CENTRES]))
        return TAB_NAMES[i] if abs(fx - TAB_CENTRES[i]) < 0.03 else ""

    @staticmethod
    def _slot_state(img: np.ndarray, i: int) -> str:
        """Top-bar team slot: 'empty' (no player), '?' (not picked yet) or 'icon' (picked).
        Only the upper part of the slot is used: the defuser bag badge sits bottom-right."""
        c = crop(img, SLOTS[i])
        if c.size == 0:
            return "empty"
        c = c[: max(1, int(c.shape[0] * 0.65))]
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        n = hsv.shape[0] * hsv.shape[1]
        white = float(np.count_nonzero((hsv[..., 2] > 180) & (hsv[..., 1] < 60))) / n
        sat = float(np.count_nonzero((hsv[..., 1] > 90) & (hsv[..., 2] > 80))) / n
        if white < 0.03 and sat < 0.03:
            return "empty"
        if white < 0.11 and sat < 0.05:
            return "?"
        return "icon"

    # ------------------------------------------------------------------ main entry point
    def read(self, img: np.ndarray, screen: Optional[str] = None) -> Optional[OpCards]:
        """Read one full BGR frame. `screen` may pass the watcher's local classification
        ("operator_select", "loadout", "operator_ban") to skip the title OCR; otherwise the
        tab row / ban title decide. Returns None for frames that are neither screen."""
        t0 = time.perf_counter()
        self.ocr_calls = 0
        r = OpCards()
        kind = self._kind(img, screen, r)
        if kind == "operator_select":
            self._read_select(img, r)
        elif kind == "operator_ban":
            self._read_ban(img, r)
        else:
            r.ms = (time.perf_counter() - t0) * 1000
            return None
        r.screen = kind
        r.ms = (time.perf_counter() - t0) * 1000
        return r

    def _kind(self, img: np.ndarray, screen: Optional[str], r: OpCards) -> str:
        s = str(getattr(screen, "value", screen) or "")
        if s in ("operator_select", "loadout"):
            return "operator_select"
        if s == "operator_ban":
            return "operator_ban"
        if s:
            return "unknown"
        # Cheapest first: the red dot under an active tab plus bright tab labels (no OCR).
        tabs_lit = self._bright(img, TABS) > 0.01
        if tabs_lit and self._active_tab(img) and sum(self._bright(img, b) > 0.03 for b in TAB_LABELS) >= 2:
            return "operator_select"
        if self._bright(img, BAN_TITLE) > 0.01:
            title, c = self._rec(img, BAN_TITLE)
            r.texts["ban_title"] = title
            t = _squash(title)
            if _fuzzy_in(t, "BANADEFENDER") or _fuzzy_in(t, "BANANATTACKER"):
                return "operator_ban"
        if tabs_lit:
            tabs, c = self._rec(img, TABS)
            r.texts["tabs"] = tabs
            t = _squash(tabs)
            n = sum(_fuzzy_in(t, k) for k in ("LOCATIONS", "OPERATORS", "LOADOUT", "READY"))
            if n >= 2:
                return "operator_select"
        if all(self._grey_bar(img, b) for b in CARD_BARS):
            return "operator_select"     # final line-up: five cards, no tab row
        if self._bright(img, TEAM_BAR_L, 170) > 0.03:
            strip, c = self._rec(img, TEAM_BAR_L)
            r.texts["team"] = strip
            if _fuzzy_in(_squash(strip), "YOURTEAM", 0.7):
                return "operator_ban"     # reveal splash or overtime summary (map ban reads empty)
        return "unknown"

    # ------------------------------------------------------------------ operator select
    def _read_select(self, img: np.ndarray, r: OpCards) -> None:
        r.tab = self._active_tab(img)
        # Top bar: each slot shows "?" until that player picks, then the operator icon.
        states = [self._slot_state(img, i) for i in range(5)]
        r.picked_slots = sum(s == "icon" for s in states)
        r.locked = states[4] == "icon"
        r.texts["slots"] = " ".join(states)

        # READY (and LOADOUT with the team view): five cards with names. The grey gamer-tag bar
        # under the rightmost card says the cards are up.
        if r.tab in ("ready", "loadout", "") and self._grey_bar(img, CARD_BARS[4]):
            r.tab = r.tab or "lineup"
            self._read_cards(img, r)
            if r.tab == "lineup" and r.my_operator:
                r.locked = True      # the line-up only appears once operators are locked
            return
        if r.tab == "loadout":
            text, conf = self._rec(img, LOADOUT_NAME)
            r.texts["loadout"] = text
            if conf >= MIN_CONF:
                name, score = self.matcher.match(text)
                if name:
                    r.my_operator, r.source, r.confidence = name, "loadout", min(conf, score)
            return
        if r.tab == "operators" and self._bright(img, PANEL_NAME, 140) > 0.005:
            # A white heading is the operator under the cursor (grey = not available to the
            # player, skipped by the brightness gate). It is a hover: the lock itself is only
            # certain when the player's own slot shows an icon.
            text, conf = self._rec(img, PANEL_NAME)
            r.texts["panel"] = text
            if conf >= MIN_CONF:
                name, score = self.matcher.match(text)
                if name:
                    r.my_operator, r.source = name, "panel"
                    r.confidence = min(conf, score) * (0.8 if r.locked else 0.5)

    def _read_cards(self, img: np.ndarray, r: OpCards) -> None:
        r.source = "cards"
        names: list[tuple[int, str, float]] = []
        for i in range(5):
            if self._bright(img, CARD_OPS[i], 170) < 0.005:
                continue      # empty card (blue silhouette, no name)
            text, conf = self._rec(img, CARD_OPS[i])
            r.texts[f"op{i}"] = text
            if conf < MIN_CONF:
                continue
            name, score = self.matcher.match(text)
            if name:
                names.append((i, name, min(conf, score)))
        # The player's own card is the rightmost one (every capture so far). With a player name
        # configured, confirm that from the gamer tag (one OCR call, remembered for two minutes)
        # and fall back to searching the other cards if it does not match.
        own = 4
        now = time.time()
        if self.player_name and now > self._own_confirmed_until:
            tag, conf = self._rec(img, CARD_PLAYERS[4])
            r.texts["player4"] = tag
            if tag and _same_tag(tag, self.player_name):
                self._own_confirmed_until = now + 120
            elif tag:
                for i in range(4):
                    tag, conf = self._rec(img, CARD_PLAYERS[i])
                    r.texts[f"player{i}"] = tag
                    if tag and _same_tag(tag, self.player_name):
                        own = i
                        break
        for i, name, score in names:
            if i == own:
                r.my_operator = name
                r.confidence = max(r.confidence, score)
            else:
                r.teammates.append(name)
        if r.my_operator is None and names:
            r.confidence = max(r.confidence, 0.6)   # teammates read, own card still empty

    # ------------------------------------------------------------------ operator ban
    def _read_ban(self, img: np.ndarray, r: OpCards) -> None:
        grid = "ban_title" in r.texts or self._bright(img, BAN_TITLE) > 0.01
        if grid:
            # The ban grid: the heading names the hovered ban, which is not confirmed yet.
            if self._bright(img, BAN_PICK_NAME, 140) > 0.005:
                text, conf = self._rec(img, BAN_PICK_NAME)
                r.texts["pick"] = text
                if conf >= MIN_CONF:
                    name, score = self.matcher.match(text)
                    if name:
                        r.ban_candidate = name
                        r.confidence = max(r.confidence, min(conf, score) * 0.9)
            return
        # Reveal splash ("MIRA (no-entry) ACE", "CASTLE (no-entry) NO BAN").
        # The names are huge, so the gate is strict: character art on the summary screen and the
        # grid's operator render only reach ~0.03 here.
        found = False
        for k, region in (("splash_l", BAN_SPLASH_L), ("splash_r", BAN_SPLASH_R)):
            if self._bright(img, region) < 0.05:
                continue
            text, conf = self._rec(img, region)
            r.texts[k] = text
            if conf >= BAN_CONF:
                found = found or _squash(text) in ("NOBAN", "NOBANS")
                self._add_ban(r, text, conf)
        if found or r.banned:
            return
        # Overtime summary: up to three attackers over up to three defenders.
        for i, region in enumerate(BAN_SUMMARY):
            if self._bright(img, region) < 0.01:
                continue
            text, conf = self._rec(img, region)
            r.texts[f"sum{i}"] = text
            if conf >= BAN_CONF:
                self._add_ban(r, text, conf)

    def _add_ban(self, r: OpCards, text: str, conf: float) -> None:
        name, score = self.matcher.match(text)
        if name and name not in r.banned:
            r.banned.append(name)
            r.confidence = max(r.confidence, min(conf, score))


# --------------------------------------------------------------------------- small helpers
def _same_tag(read: str, player: str) -> bool:
    a = re.sub(r"[^a-z0-9]", "", read.lower())
    b = re.sub(r"[^a-z0-9]", "", player)
    if not a or not b:
        return False
    # Tags are small type (18 px on a 1600-wide frame): "dmhost88" has been read as "dnmhosta".
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.7


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
