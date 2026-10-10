"""Persist the routing choices made in Settings alongside its connections."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from omote.providers.base import Modality
from omote.providers.registry import Route

log = logging.getLogger(__name__)


class RouteStore:
    def __init__(self, connections_path: Path):
        self.path = Path(connections_path).with_suffix('.routes.json')

    def load(self) -> dict[Modality, Route]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            return {Modality(kind): Route(**route) for kind, route in data.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            log.warning('Could not read saved provider routes; using configured defaults')
            return {}

    def save(self, routes: dict[Modality, Route]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        # Create private from the first byte; do not briefly expose settings.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump({kind.value: asdict(route) for kind, route in routes.items()}, out, indent=2)
        tmp.replace(self.path)

    def put(self, kind: Modality, route: Route | None) -> None:
        routes = self.load()
        if route is None:
            routes[kind] = Route(provider='')
        else:
            routes[kind] = route
        self.save(routes)
