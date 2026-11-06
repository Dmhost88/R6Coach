"""Download Ubisoft's official map blueprints into data/maps/<map_id>/ (git-ignored, personal use).

Each map page on ubisoft.com links a "download blueprints (.zip)". Only image files are unpacked,
by base name, so nothing in a zip can write outside its map's folder. Re-running skips maps that
are already unpacked. Usage: python -I tools/fetch_blueprints.py [map_id ...]
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://ubistatic-a.ubisoft.com/0106/gamesites/rainbow6/blueprints/"
# From each map's page on ubisoft.com/en-us/game/rainbow-six/siege/game-info/maps/<page> (2026-11-06).
ZIPS = {
    "bank": "r6-maps-bank-blueprints.zip",
    "border": "r6-maps-border-blueprints.zip",
    "calypso_casino": "r6-maps-calypso-casino-blueprints.zip",
    "chalet": "r6-maps-chalet-blueprints.zip",
    "clubhouse": "r6-maps-clubhouse-blueprints.zip",
    "coastline": "r6-maps-coastline-blueprints.zip",
    "consulate": "r6-maps-consulate-blueprints_may23.zip",
    "fortress": "r6-maps-fortress-blueprints.zip",
    "kafe_dostoyevsky": "r6-maps-kafe-blueprints.zip",
    "kanal": "r6-maps-kanal-blueprints.zip",
    "lair": "r6-maps-lair-blueprints.zip",
    "nighthaven_labs": "r6-maps-nighthavenlabs-blueprints.zip",
    "oregon": "r6-maps-oregon-blueprints.zip",
    "outback": "r6-maps-outback-blueprints.zip",
    "villa": "r6-maps-villa-blueprints.zip",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_ZIP = 80 * 1024 * 1024
MAX_FILE = 60 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent / "data" / "maps"


def fetch(map_id: str) -> None:
    out = ROOT / map_id
    if (out / "source.json").exists():
        print(f"{map_id}: already here")
        return
    url = BASE + ZIPS[map_id]
    req = urllib.request.Request(url, headers={"User-Agent": "R6Coach blueprint fetch"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(MAX_ZIP + 1)
        modified = r.headers.get("Last-Modified")
    if len(data) > MAX_ZIP:
        raise RuntimeError(f"{map_id}: zip larger than {MAX_ZIP} bytes")
    out.mkdir(parents=True, exist_ok=True)
    files = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = Path(info.filename.replace("\\", "/")).name
            if info.is_dir() or Path(name).suffix.lower() not in IMAGE_EXT or name.startswith("."):
                continue
            if info.file_size > MAX_FILE:
                print(f"  skip {name}: too large")
                continue
            (out / name).write_bytes(z.read(info))
            files.append(name)
    (out / "source.json").write_text(json.dumps({
        "url": url, "last_modified": modified, "fetched": time.strftime("%Y-%m-%d"),
        "zip_bytes": len(data), "files": sorted(files),
        "rights": "Ubisoft official blueprints; personal local use only, not redistributed.",
    }, indent=2), encoding="utf-8")
    print(f"{map_id}: {len(files)} images ({len(data) // 1024} KB)")


if __name__ == "__main__":
    for m in sys.argv[1:] or list(ZIPS):
        try:
            fetch(m)
        except Exception as e:  # keep going with the other maps
            print(f"{m}: FAILED {e}")
