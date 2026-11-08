from r6coach.places import same_room, site_with_room, snap_room, snap_site

ROOMS = ["2F Office", "2F Library Hallway", "2F Mechanical", "1F Piano Room", "EXT Balcony", "2F Balcony",
         "1F Office Supplies"]
SITES = ["2F Laundry / 2F Piano Room", "2F Party Room / 2F Office", "1F Green Bedroom / 1F Red Bedroom"]


def test_snap_room_fixes_ocr_typos():
    assert snap_room("2F Libary Hallway", ROOMS) == "2F Library Hallway"
    assert snap_room("2F Office8", ROOMS) == "2F Office"
    assert snap_room("1F Office Suplies", ROOMS) == "1F Office Supplies"
    assert snap_room("2F Meeting Room", ROOMS) is None         # a room the data lacks: not a guess


def test_snap_room_keeps_floors_apart():
    assert snap_room("EXT Balcony", ROOMS) == "EXT Balcony"
    assert snap_room("1F Office", ROOMS) is None               # no 1F Office on this map
    assert snap_room("Piano Room", ROOMS) == "1F Piano Room"   # no prefix read: any floor


def test_same_room_ignores_ocr_wobble():
    assert same_room("2F Office", "2F Office8")
    assert same_room("EXT Motel Balcony", "EXT Mote Balcony")
    assert not same_room("2F Office", "1F Office")
    assert not same_room("2F Office", "2F Laundry")


def test_snap_site_upgrades_one_room_to_the_pair():
    assert snap_site("2F Laundry", SITES) == "2F Laundry / 2F Piano Room"
    assert snap_site("2F Party Room / 2F Ofice", SITES) == "2F Party Room / 2F Office"
    assert snap_site("B Garage", SITES) is None


def test_site_with_room_from_the_defender_spawn():
    assert site_with_room("1F Red Bedroom", SITES) == "1F Green Bedroom / 1F Red Bedroom"
    assert site_with_room("2F Office", SITES) == "2F Party Room / 2F Office"
    assert site_with_room("1F Office", SITES) is None           # floor must agree
    assert site_with_room("EXT Garden", SITES) is None
