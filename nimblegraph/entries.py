"""Entry points: package.json main/module/bin, conventional main/index/server/app files, the <script src>
of index.html (Vite), framework entries (Next pages/routes, server.listen, Nest main) and config.toml."""

from __future__ import annotations

import posixpath
import re

from nimblegraph.resolve import SOURCE_EXTS, Resolver

CONVENTIONAL = ("src/main", "src/index", "src/server", "src/app", "main", "index", "server", "app")
RE_SCRIPT = re.compile(r"""<script[^>]*\bsrc=["']([^"']+)["']""", re.I)


def find(root, files: set[str], packages: list[str], resolver: Resolver, framework_entries: set[str],
         extra: list[str]) -> set[str]:
    out = set(framework_entries)
    for d in sorted(set(packages) | {""}):
        pj = resolver.package_json(d) or {}
        targets = [pj.get(k) for k in ("main", "module", "source")]
        b = pj.get("bin")
        targets += list(b.values()) if isinstance(b, dict) else [b]
        for t in targets:
            if isinstance(t, str):
                rel = posixpath.normpath(posixpath.join(d, t)) if d else posixpath.normpath(t)
                hit = resolver.try_file(rel) or resolver.map_build_to_src(rel)
                if hit:
                    out.add(hit)
        for c in CONVENTIONAL:
            base = f"{d}/{c}" if d else c
            for e in SOURCE_EXTS:
                if base + e in files:
                    out.add(base + e)
                    break
        html = root / d / "index.html" if d else root / "index.html"
        if html.is_file():
            try:
                text = html.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for src in RE_SCRIPT.findall(text):
                if src.startswith(("http:", "https:", "//")):
                    continue
                rel = src.lstrip("/")
                rel = f"{d}/{rel}" if d else rel
                hit = resolver.try_file(posixpath.normpath(rel))
                if hit:
                    out.add(hit)
    for e in extra:
        if e in files:
            out.add(e)
    return out
