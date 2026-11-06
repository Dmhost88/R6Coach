"""Debug helper: run one cloud screen read on a saved frame and time it.

    .venv/Scripts/python tools/try_vision.py <image> [model]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from r6coach.bus import Bus  # noqa: E402
from r6coach.capture import downscale, encode_jpeg  # noqa: E402
from r6coach.config import load_config  # noqa: E402
from r6coach.llm import Claude  # noqa: E402

cfg = load_config()
img = cv2.imdecode(np.fromfile(sys.argv[1], np.uint8), cv2.IMREAD_COLOR)
jpeg = encode_jpeg(downscale(img, cfg.capture.upload_width))
model = sys.argv[2] if len(sys.argv) > 2 else cfg.vision.model
bus = Bus()
bus.subscribe("error", lambda ev: print("ERROR", ev.data))
bus.subscribe("llm.usage", lambda ev: print("usage", ev.data))
claude = Claude(bus)
t = time.time()
read = claude.read_screen(jpeg, model, cfg.vision.effort, player_name=cfg.player_name)
print(f"{time.time() - t:.1f}s")
print(read.model_dump_json(indent=1, exclude_defaults=True) if read else read)
