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


def test_context_is_side_specific_and_includes_notes(kb):
    kb.save_notes("bank", "I like CCTV anchor.")
    ctx = kb.context_for("Bank", "B Lockers / B CCTV Room", "defense")
    assert "Hold basement." in ctx
    assert "Take garage." not in ctx
    assert "I like CCTV anchor." in ctx


def test_unknown_map_context_says_so(kb):
    assert "No local data" in kb.context_for("Nowhere", None, None)
