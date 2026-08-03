from minebot.places import PlaceMemory


def test_remember_and_get(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    places.remember("home", 10.0, 64.0, -5.0)

    place = places.get("home")

    assert place is not None
    assert (place.x, place.y, place.z) == (10.0, 64.0, -5.0)


def test_get_unknown_place_returns_none(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    assert places.get("nowhere") is None


def test_names_lists_all_remembered_places(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    places.remember("home", 0.0, 0.0, 0.0)
    places.remember("base", 1.0, 1.0, 1.0)

    assert set(places.names()) == {"home", "base"}


def test_places_persist_across_instances(tmp_path):
    path = tmp_path / "places.json"
    places = PlaceMemory(path)
    places.remember("home", 10.0, 64.0, -5.0)

    reloaded = PlaceMemory(path)

    place = reloaded.get("home")
    assert place is not None
    assert (place.x, place.y, place.z) == (10.0, 64.0, -5.0)


def test_missing_file_starts_empty(tmp_path):
    places = PlaceMemory(tmp_path / "does_not_exist.json")
    assert places.names() == []


def test_remember_overwrites_an_existing_name(tmp_path):
    places = PlaceMemory(tmp_path / "places.json")
    places.remember("home", 0.0, 0.0, 0.0)
    places.remember("home", 10.0, 64.0, -5.0)

    place = places.get("home")
    assert (place.x, place.y, place.z) == (10.0, 64.0, -5.0)
