"""Hand-labelled frames from the 2026-11-06 capture sessions (3440x1440).

Each case: (session folder, frame, expected fields). Only listed fields are checked.
Frames live under data/calibration/ (not in git); tests skip when they're absent.
"""

S1 = "2026-11-06_003854"   # House (attack), Coastline custom (defense)
S2 = "2026-11-06_005400"   # Ranked on Chalet, won 4-1

CASES = [
    (S1, "004027_0020", {"timer": 143, "score_us": 0, "score_them": 0, "room": "EXT Front Street", "health": 125}),
    # OCR reads this callout as "Biliards Room", so only the tail is checked.
    (S1, "004834_0142", {"timer": 43, "reinforcements": 10, "room~": "ards Room", "health": 110, "event": "prep"}),
    (S1, "004111_0034", {"event": "bombs_found", "site~": "Master Bedroom"}),
    (S1, "004149_0043", {"event": "planted"}),
    (S2, "005740_0045", {"event": "operators_locked"}),
    (S2, "010209_0117", {"timer": 161, "score_us": 1, "alive_us": 5, "alive_them": 2}),
    (S2, "010536_0169", {"event": "bombs_found", "site~": "Wine Cellar"}),
    (S2, "010633_0187", {"alive_us": 5, "alive_them": 3, "kill": ("Mate_A", "us", "Enemy_C", "them")}),
    (S2, "010714_0199", {"alive_us": 2, "alive_them": 3, "event": "defuser_dropped",
                         "kill": ("Enemy_A", "them", "Mate_C", "us")}),
    (S2, "010803_0214", {"timer": 64, "room": "B Fireplace Stairs", "alive_us": 1, "alive_them": 1,
                         "kill": ("Mate_B", "us", "Enemy_A", "them")}),
    (S2, "010359_0152", {"event": "won_round", "kill": ("dmhost88", "us", "Enemy_D", "them")}),
    (S2, "010835_0224", {"event": "lost_round"}),
    (S2, "010845_0227", {"event": "role_swap"}),
    (S2, "011459_0302", {"event": "enemies_found_bombs"}),
    (S2, "011623_0328", {"alive_us": 3, "alive_them": 2}),
    (S2, "011725_0338", {"event": "won_round"}),
]
