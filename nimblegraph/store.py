"""SQLite store (.nimblegraph/graph.db): a derived cache that can always be rebuilt."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "4"  # 4: file hashes ignore CRLF/LF (scanner.content_hash)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS parsed(path TEXT PRIMARY KEY, hash TEXT, engine TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, hash TEXT, mtime INTEGER, size INTEGER, loc INTEGER, tokens INTEGER,
  layer TEXT, is_entry INTEGER DEFAULT 0, pkg TEXT);
CREATE TABLE IF NOT EXISTS symbols(
  file TEXT, name TEXT, kind TEXT, line INTEGER, end_line INTEGER, exported INTEGER, parent TEXT,
  extends TEXT, implements TEXT, decorators TEXT, abstract INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS edges(src TEXT, dst TEXT, kind TEXT, names TEXT, via TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS externals(file TEXT, pkg TEXT, kind TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS unresolved(file TEXT, spec TEXT, line INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS routes(
  id INTEGER PRIMARY KEY, method TEXT, path TEXT, file TEXT, handler_file TEXT, handler TEXT, kind TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS route_mounts(route_id INTEGER, seq INTEGER, file TEXT, prefix TEXT);
CREATE TABLE IF NOT EXISTS route_middlewares(route_id INTEGER, seq INTEGER, file TEXT, symbol TEXT, scope TEXT);
CREATE TABLE IF NOT EXISTS symbol_refs(file TEXT, symbol TEXT, ref_file TEXT, ref_symbol TEXT);
CREATE TABLE IF NOT EXISTS inherits(
  file TEXT, symbol TEXT, kind TEXT, ref_file TEXT, ref_symbol TEXT, ref_pkg TEXT);
CREATE TABLE IF NOT EXISTS api_calls(file TEXT, symbol TEXT, method TEXT, url TEXT, route_id INTEGER, line INTEGER);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS symbol_refs_file ON symbol_refs(file, symbol);
"""

GRAPH_TABLES = ("files", "symbols", "edges", "externals", "unresolved", "routes", "route_mounts",
                "route_middlewares", "symbol_refs", "api_calls", "inherits")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        self.db.executescript("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);")
        version = self.get_meta("schema")
        if version is not None and str(version) != SCHEMA_VERSION:
            tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                self.db.execute(f"DROP TABLE IF EXISTS {t}")
        self.db.executescript(SCHEMA)
        self.set_meta("schema", SCHEMA_VERSION)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ meta
    def get_meta(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return row[0]

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    # ------------------------------------------------------------ parse cache
    def parsed_hashes(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT path, hash FROM parsed"))

    def load_parsed(self) -> dict[str, dict]:
        return {p: json.loads(j) for p, j in self.db.execute("SELECT path, json FROM parsed")}

    def save_parsed(self, records: dict[str, tuple[str, str, dict]]) -> None:
        """records: path -> (hash, engine, record)."""
        self.db.executemany(
            "INSERT OR REPLACE INTO parsed(path, hash, engine, json) VALUES(?, ?, ?, ?)",
            [(p, h, e, json.dumps(r, separators=(",", ":"))) for p, (h, e, r) in records.items()],
        )

    def drop_parsed(self, paths) -> None:
        self.db.executemany("DELETE FROM parsed WHERE path=?", [(p,) for p in paths])

    def clear_parsed(self) -> None:
        self.db.execute("DELETE FROM parsed")

    def file_stats(self) -> dict[str, tuple[int, int, str, int, int]]:
        return {r[0]: tuple(r[1:]) for r in self.db.execute("SELECT path, mtime, size, hash, loc, tokens FROM files")}

    # ------------------------------------------------------------ graph tables
    def replace_graph(self, tables: dict[str, list[tuple]]) -> None:
        for t in GRAPH_TABLES:
            self.db.execute(f"DELETE FROM {t}")
        for t, rows in tables.items():
            if not rows:
                continue
            ph = ",".join("?" * len(rows[0]))
            self.db.executemany(f"INSERT INTO {t} VALUES({ph})", rows)

    def commit(self) -> None:
        self.db.commit()

    def query(self, sql: str, params=()) -> list[tuple]:
        return self.db.execute(sql, params).fetchall()
