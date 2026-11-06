"""Evaluate the local screen classifier on captured frames, and replay a capture through the watcher.

  python tools/screens_eval.py label  [--labels FILE] [--out CSV]
      Classify every frame under data/calibration/*/auto. With --labels (lines "session/frame label",
      label = a Screen value or "in_round"), print accuracy per class and the confusions.
  python tools/screens_eval.py replay --labels FILE [--session 2026-11-06_005400]
      Replay one capture through the real Watcher/ReadGate with simulated time (2 samples/s, each
      frame shown until the next one's timestamp), once with vision.local_screens off and once on.
      The fake cloud answers with the hand label; the local HUD reader runs for real. Prints the
      number of cloud reads before and after.

No API calls are made.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from r6coach.models import IN_ROUND, Screen, ScreenRead  # noqa: E402

CAL = ROOT / "data" / "calibration"


def load(p: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def frames(session: str | None = None) -> list[tuple[str, Path]]:
    out = []
    for d in sorted(CAL.iterdir()):
        if (d / "auto").is_dir() and (session is None or d.name == session):
            out += [(f"{d.name}/{p.stem}", p) for p in sorted((d / "auto").glob("*.jpg"))]
    return out


def read_labels(path: Path) -> dict[str, str]:
    labels = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if line:
            fid, lab = line.split()[:2]
            labels[fid] = lab
    return labels


def coarse(screen: str, in_round: bool) -> str:
    """Prep and action can't be told apart from one frame (the HUD tracks it), so both count as in_round."""
    return "in_round" if screen in ("prep_phase", "action_phase") else screen


# ---------------------------------------------------------------------------- label
def cmd_label(args) -> None:
    from r6coach.screens import ScreenClassifier

    clf = ScreenClassifier()
    labels = read_labels(Path(args.labels)) if args.labels else {}
    rows = []
    for fid, p in frames(args.session):
        img = load(p)
        r = clf.read(img)
        pred = coarse(r.screen.value, r.in_round)
        rows.append({"id": fid, "pred": pred, "screen": r.screen.value, "conf": round(r.confidence, 2),
                     "title": r.title, "key": r.key, "card": int(r.card), "map": r.map or "", "mode": r.mode or "", "ms": round(r.ms, 1),
                     "ocr_calls": r.ocr_calls, "label": labels.get(fid, "")})
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    ms = [r["ms"] for r in rows]
    print(f"{len(rows)} frames  ms/frame: mean {statistics.mean(ms):.1f}  median {statistics.median(ms):.1f}  "
          f"p95 {sorted(ms)[int(len(ms) * 0.95)]:.1f}  max {max(ms):.1f}  "
          f"OCR calls/frame {statistics.mean(r['ocr_calls'] for r in rows):.2f}")
    print("predicted:", dict(Counter(r["pred"] for r in rows).most_common()))
    conf = [r for r in rows if r["conf"] >= 0.75]
    print(f"confident: {len(conf)}/{len(rows)}")
    maps = Counter(r["map"] for r in rows if r["map"])
    print("maps read:", dict(maps))
    if not labels:
        return
    # Accuracy per true class, over confident predictions (what the watcher acts on) and overall.
    per = defaultdict(lambda: [0, 0, 0, 0])   # n, correct, confident n, confident correct
    confusions = Counter()
    for r in rows:
        lab = r["label"]
        if not lab:
            continue
        ok = r["pred"] == lab
        c = per[lab]
        c[0] += 1
        c[1] += ok
        if r["conf"] >= 0.75:
            c[2] += 1
            c[3] += ok
            if not ok:
                confusions[(lab, r["pred"])] += 1
    print(f"\n{'class':16} {'n':>4} {'acc':>6} {'conf n':>7} {'conf acc':>9}")
    for lab, (n, ok, cn, cok) in sorted(per.items(), key=lambda kv: -kv[1][0]):
        print(f"{lab:16} {n:4d} {ok / n:6.1%} {cn:7d} {(cok / cn if cn else 0):9.1%}")
    tot = [sum(v[i] for v in per.values()) for i in range(4)]
    print(f"{'ALL':16} {tot[0]:4d} {tot[1] / tot[0]:6.1%} {tot[2]:7d} {tot[3] / max(tot[2], 1):9.1%}")
    # Precision of confident predictions per predicted class (a wrong confident "skip" costs detail).
    print("\nconfident predictions: precision per predicted class")
    pp = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["label"] and r["conf"] >= 0.75:
            pp[r["pred"]][0] += 1
            pp[r["pred"]][1] += r["pred"] == r["label"]
    for k, (n, ok) in sorted(pp.items(), key=lambda kv: -kv[1][0]):
        print(f"  {k:16} {ok}/{n} = {ok / n:.1%}")
    if confusions:
        print("\nconfident mistakes (true -> predicted):")
        for (a, b), n in confusions.most_common():
            print(f"  {a} -> {b}: {n}")
            for r in rows:
                if r["label"] == a and r["pred"] == b and r["conf"] >= 0.75:
                    print(f"      {r['id']}  title={r['title']!r}")


# ---------------------------------------------------------------------------- replay
class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t

    def __getattr__(self, name):   # sleep, perf_counter, ... from the real module
        return getattr(time, name)


class _Source:
    status = "replay"

    def __init__(self) -> None:
        self.cur = None

    def latest(self):
        return self.cur


class _NoSave:
    def maybe_save(self, *a) -> None:
        pass


class _FakeClaude:
    """Answers each read with the hand label of the frame on screen."""

    def __init__(self, watcher_ref, labels) -> None:
        self.w = watcher_ref
        self.labels = labels
        self.fid = None
        self.calls: list[tuple[float, str, str]] = []

    def read_screen(self, jpeg, model, effort, hint="", player_name=""):
        lab = self.labels.get(self.fid, "other")
        if lab == "in_round":
            st = self.w[0].state.screen
            lab = st.value if st in IN_ROUND else "action_phase"
        if lab not in Screen.__members__:
            lab = "other"
        self.calls.append((self.w[0]._clock.t, self.fid, lab))
        return ScreenRead(screen=Screen(lab), confidence=0.9,
                          map="Chalet" if lab in ("operator_ban", "operator_select") else None)


class _CachedClassifier:
    """The classifier is deterministic per frame: classify each frame once and reuse it."""

    def __init__(self) -> None:
        from r6coach.screens import ScreenClassifier

        self.clf = ScreenClassifier()
        self.cache: dict[int, object] = {}

    def read(self, img):
        k = id(img)
        if k not in self.cache:
            self.cache[k] = self.clf.read(img)
        return self.cache[k]


def run_replay(session: str, labels: dict, local: bool, hud_cache: dict, clf, imgs: list) -> _FakeClaude:
    import r6coach.hud as hud_mod
    import r6coach.watcher as wmod
    from r6coach.bus import Bus
    from r6coach.config import Config

    clock = _Clock()
    wmod.time = clock
    hud_mod.time = clock
    cfg = Config()
    cfg.vision.local_screens = local
    src = _Source()
    ref = [None]
    claude = _FakeClaude(ref, labels)
    w = wmod.Watcher(cfg, Bus(), src, claude, _NoSave(), screens=clf if local else None)
    w._clock = clock
    ref[0] = w
    screens_seen = Counter()
    t0 = imgs[0][0]
    end = imgs[-1][0] + 4.0
    i = 0
    clock.t = t0
    last_hud = -1.0
    while clock.t < end:
        while i + 1 < len(imgs) and imgs[i + 1][0] <= clock.t:
            i += 1
        ts, fid, img = imgs[i]
        src.cur = (ts, img)
        claude.fid = fid
        if clock.t - last_hud >= 1.0:             # HUD thread: once a second
            last_hud = clock.t
            key = (fid, local)
            if fid not in hud_cache:
                hud_cache[fid] = hud_cache["_reader"].read(img)
            w.apply_hud(hud_cache[fid])
        w._tick()
        screens_seen[w.state.screen.value] += 1
        clock.t += 0.5
    wmod.time = time
    hud_mod.time = time
    return claude


def cmd_replay(args) -> None:
    from r6coach.hud import HudReader

    labels = read_labels(Path(args.labels))
    imgs = []
    for fid, p in frames(args.session):
        hh, mm, ss = int(p.stem[0:2]), int(p.stem[2:4]), int(p.stem[4:6])
        imgs.append((hh * 3600 + mm * 60 + ss, fid, load(p)))
    # Same-second names: spread them out so time keeps moving forward.
    for k in range(1, len(imgs)):
        if imgs[k][0] <= imgs[k - 1][0]:
            imgs[k] = (imgs[k - 1][0] + 0.5, imgs[k][1], imgs[k][2])
    span = imgs[-1][0] - imgs[0][0]
    print(f"{args.session}: {len(imgs)} frames over {span / 60:.1f} min")
    hud_cache: dict = {"_reader": HudReader()}
    clf = _CachedClassifier()
    res = {}
    for local in (False, True):
        c = run_replay(args.session, labels, local, hud_cache, clf, imgs)
        res[local] = c
        by = Counter(lab for _, _, lab in c.calls)
        print(f"\nlocal_screens={local}: {len(c.calls)} cloud reads  by cloud-label: {dict(by.most_common())}")
        if args.verbose:
            for t, fid, lab in c.calls:
                print(f"   {t - imgs[0][0]:7.1f}s  {fid}  {lab}")
    a, b = len(res[False].calls), len(res[True].calls)
    print(f"\ncloud reads: {a} -> {b}  ({(a - b) / a:.0%} fewer), "
          f"~${a * 0.012:.2f} -> ~${b * 0.012:.2f} at $0.012/read")
    # Did every detail screen still get at least one read?
    detail = ("map_ban", "operator_ban", "operator_select", "scoreboard")
    runs_missed = []
    seq = [(fid, labels.get(fid, "")) for _, fid, _ in imgs]
    read_fids = {fid for _, fid, _ in res[True].calls}
    k = 0
    while k < len(seq):
        lab = seq[k][1]
        j = k
        while j + 1 < len(seq) and seq[j + 1][1] == lab:
            j += 1
        if lab in detail and not any(seq[m][0] in read_fids for m in range(k, j + 1)):
            runs_missed.append((lab, seq[k][0], j - k + 1))
        k = j + 1
    print("detail-screen runs with no cloud read (local on):", runs_missed or "none")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["label", "replay"])
    ap.add_argument("--labels")
    ap.add_argument("--out")
    ap.add_argument("--session")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.cmd == "replay":
        args.session = args.session or "2026-11-06_005400"
        if not args.labels:
            ap.error("replay needs --labels")
        cmd_replay(args)
    else:
        cmd_label(args)


if __name__ == "__main__":
    main()
