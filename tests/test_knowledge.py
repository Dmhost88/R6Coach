def test_find_map_fuzzy(kb):
    assert kb.find_map("BANK")["id"] == "bank"
    assert kb.find_map("Bnak") is None or kb.find_map("Bnak")["id"] == "bank"
    assert kb.find_map("Oregon") is None


def test_find_site_tolerates_ocr_variants(kb):
    m = kb.find_map("Bank")
    assert kb.find_site(m, "B Lockers/B CCTV Room")["id"] == "b_lockers_cctv"
    assert kb.find_site(m, "2F Executive Lounge / 2F CEO Office")["id"] == "ceo_lounge"
    assert kb.find_site(m, "Something else entirely") is None


def test_operator_lookup_handles_accents(kb):
    assert kb.operator("Jager")["name"] == "Jäger"
    assert kb.operator("thermite")["name"] == "Thermite"


def test_real_data_loads_with_aliases():
    from pathlib import Path
    from r6coach.knowledge import Knowledge
    real = Knowledge(Path(__file__).parent / "_notes_unused")
    assert len(real.maps) == 15 and len(real.operators) >= 78
    assert real.operator("Snake") and real.operator("Snake")["name"] == "Solid Snake"
    assert real.operator("Tubarão")["name"] == "Tubarao"
    dossier = real.map_dossier("Oregon")
    assert "Laundry" in dossier and "MAP DOSSIER: Oregon" in dossier
    assert "Thatcher" in real.operators_dossier()


def test_rooms_seen_feed_the_dossier(tmp_path, kb):
    from r6coach.profile import Profile
    p = Profile(tmp_path / "p.sqlite")
    for _ in range(3):
        p.room_seen("Bank", "B CCTV Room")
    p.room_seen("Bank", "one tice")          # single read: treated as an OCR slip
    assert p.rooms_seen("bank") == ["B CCTV Room"]
    assert "B CCTV Room" in kb.map_dossier("Bank", p.rooms_seen("Bank"))


def test_context_is_side_specific_and_includes_notes(kb):
    kb.save_notes("bank", "I like CCTV anchor.")
    ctx = kb.context_for("Bank", "B Lockers / B CCTV Room", "defense")
    assert "Hold basement." in ctx
    assert "Take garage." not in ctx
    assert "I like CCTV anchor." in ctx


def test_unknown_map_context_says_so(kb):
    assert "No local data" in kb.context_for("Nowhere", None, None)
