"""Named location memory -- !save <name> saves the *caller's* current
position under a name, later commands (!goto <name>, and eventually
!look/!info per PENDING.md) resolve that name back to coordinates.
Persisted to a JSON file so saved places survive a backend restart --
there's no other persistence layer in this project yet.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("minebot.places")

DEFAULT_PATH = Path("places.json")


@dataclass
class Place:
    x: float
    y: float
    z: float


class PlaceMemory:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = path
        self._places: dict[str, Place] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("couldn't load places from %s: %s", self._path, e)
            return
        self._places = {name: Place(**coords) for name, coords in raw.items()}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps({name: asdict(place) for name, place in self._places.items()}, indent=2))
        except OSError as e:
            log.warning("couldn't save places to %s: %s", self._path, e)

    def remember(self, name: str, x: float, y: float, z: float) -> None:
        self._places[name] = Place(x=x, y=y, z=z)
        self._save()

    def get(self, name: str) -> Place | None:
        return self._places.get(name)

    def names(self) -> list[str]:
        return list(self._places.keys())
