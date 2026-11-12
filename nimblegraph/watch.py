"""Live updates for `nimblegraph ui`: every second an incremental build (cheap when nothing changed) and a check of
notes.json; changes are pushed to the browser as SSE events."""

from __future__ import annotations

import threading
import time

from nimblegraph import build


class Watcher(threading.Thread):
    def __init__(self, app, interval: float = 1.0):
        super().__init__(daemon=True, name="nimblegraph-watch")
        self.app = app
        self.interval = interval
        self.stop_event = threading.Event()
        self._notes_mtime = self._mtime()

    def _mtime(self) -> int:
        try:
            return self.app.cfg.notes_path.stat().st_mtime_ns
        except OSError:
            return 0

    def tick(self) -> None:
        r = build.run(self.app.cfg, engine_pref=self.app.engine_pref)
        if r.dirty:
            self.app.reload()
            self.app.publish("graph", {"added": r.added, "removed": r.removed, "changed": r.changed,
                                       "routes_added": r.routes_added, "routes_removed": r.routes_removed,
                                       "at": time.time()})
        m = self._mtime()
        if m != self._notes_mtime:
            self._notes_mtime = m
            self.app.reload()
            self.app.publish("notes", {"at": time.time()})

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - a broken file must not kill live mode
                self.app.publish("error", {"message": str(e)[:300]})

    def stop(self) -> None:
        self.stop_event.set()
