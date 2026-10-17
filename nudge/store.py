"""Persistence: watch definitions in YAML, runtime state in JSON.

Definitions (watches.yaml) are human-editable and only rewritten when a watch
is added, changed, or removed. State (.nudge-state.json) changes on every check.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from nudge.models import Watch, new_id


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Store:
    def __init__(self, watches_path: Path | str, state_path: Path | str):
        self.watches_path = Path(watches_path)
        self.state_path = Path(state_path)
        self.watches: list[Watch] = []
        self.state: dict[str, Any] = {"watches": {}}
        self.dirty = False

    @classmethod
    def load(cls, watches_path: Path | str, state_path: Path | str) -> "Store":
        store = cls(watches_path, state_path)
        if store.watches_path.is_file():
            raw = yaml.safe_load(store.watches_path.read_text()) or {}
            store.watches = [Watch.from_dict(item) for item in raw.get("watches") or []]
        if store.state_path.is_file():
            try:
                store.state = json.loads(store.state_path.read_text())
            except json.JSONDecodeError:
                store.state = {}
        store.state.setdefault("watches", {})
        return store

    def save(self) -> None:
        if self.dirty or not self.watches_path.exists():
            body = yaml.safe_dump(
                {"watches": [w.to_dict() for w in self.watches]},
                sort_keys=False,
                allow_unicode=True,
            )
            _atomic_write(self.watches_path, body)
            self.dirty = False
        known = {w.id for w in self.watches}
        self.state["watches"] = {k: v for k, v in self.state["watches"].items() if k in known}
        _atomic_write(self.state_path, json.dumps(self.state, indent=2, sort_keys=True))

    # -- watches -----------------------------------------------------------

    def add(self, watch: Watch) -> Watch:
        while any(w.id == watch.id for w in self.watches):
            watch.id = new_id()
        self.watches.append(watch)
        self.dirty = True
        return watch

    def find(self, key: str) -> Watch:
        """Look up by id, unique id prefix, or case-insensitive name."""
        key = key.strip()
        for w in self.watches:
            if w.id == key:
                return w
        matches = [w for w in self.watches if w.id.startswith(key)] or [
            w for w in self.watches if w.name.lower() == key.lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"No watch matching {key!r}")
        raise KeyError(f"{key!r} matches several watches: {', '.join(w.id for w in matches)}")

    def remove(self, key: str) -> Watch:
        watch = self.find(key)
        self.watches.remove(watch)
        self.state["watches"].pop(watch.id, None)
        self.dirty = True
        return watch

    def set_enabled(self, key: str, enabled: bool) -> Watch:
        watch = self.find(key)
        if watch.enabled != enabled:
            watch.enabled = enabled
            self.dirty = True
        return watch

    # -- state -------------------------------------------------------------

    def state_for(self, watch: Watch) -> dict[str, Any]:
        return self.state["watches"].setdefault(watch.id, {})

    @property
    def telegram_offset(self) -> int:
        return int(self.state.get("telegram_offset", 0))

    @telegram_offset.setter
    def telegram_offset(self, value: int) -> None:
        self.state["telegram_offset"] = value
