"""Cross-file string constants for the route detectors. The extractors already resolve what a file knows
(its locals, fields and module constants) and leave `{=name}` holes for the rest; this pass fills them from the
file's imports (`from app.config import PREFIX`), class constants of other files (`ApiRoutes.Users`,
`settings.API_V1_STR` -> class Settings) and, for Spring, `${key:default}` from application.properties/yml.
Only path-like positions are touched (first argument, prefix/path/value/template options), so handler names are
never replaced."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

RE_HOLE = re.compile(r"\{=([^{}]+)\}")
RE_PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
PATH_PROPS = ("prefix", "url_prefix", "path", "value", "template", "Template", "url", "uri")


class Constants:
    def __init__(self, ctx):
        self.ctx = ctx
        self.exact: dict[str, set[str]] = defaultdict(set)
        self.last: dict[str, set[str]] = defaultdict(set)
        for rec in ctx.parsed.values():
            for k, v in (rec.get("strings") or {}).items():
                if "{=" in v:
                    continue
                self.exact[k].add(v)
                self.last[k.rsplit(".", 1)[-1]].add(v)
        self._props: dict[str, str] | None = None

    def const(self, f: str, name: str) -> str | None:
        rec = self.ctx.rec(f)
        own = (rec.get("strings") or {}).get(name)
        if own is not None and "{=" not in own:
            return own
        root, _, rest = name.partition(".")
        hit = self.ctx.resolve_local(f, root)
        if hit:
            g, sym = hit
            strs = self.ctx.rec(g).get("strings") or {}
            for key in ((rest, f"{sym}.{rest}") if rest else (sym,)):
                v = strs.get(key)
                if v is not None and "{=" not in v:
                    return v
        for table, key in ((self.exact, name), (self.exact, ".".join(name.split(".")[-2:])),
                           (self.last, name.rsplit(".", 1)[-1])):
            vals = table.get(key)
            if vals and len(vals) == 1:
                return next(iter(vals))
        return None

    def fill(self, f: str, s: str) -> str:
        for _ in range(3):
            new = RE_HOLE.sub(lambda m: self.const(f, m.group(1)) or m.group(0), s)
            if new == s:
                break
            s = new
        if "${" in s and self.ctx.rec(f).get("lang") in ("java", "kotlin"):
            props = self.properties()
            s = RE_PLACEHOLDER.sub(lambda m: props.get(m.group(1).strip(), m.group(2) if m.group(2) is not None
                                                       else m.group(0)), s)
        return s

    def properties(self) -> dict[str, str]:
        """Spring application.properties / application.yml (flattened keys), first one found wins per key."""
        if self._props is None:
            props: dict[str, str] = {}
            root = Path(self.ctx.root)
            for p in sorted(root.glob("**/src/main/resources/application*.*"))[:20]:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if p.suffix == ".properties":
                    for ln in text.splitlines():
                        m = re.match(r"\s*([\w.\-\[\]]+)\s*[=:]\s*(.*?)\s*$", ln)
                        if m and not ln.lstrip().startswith(("#", "!")):
                            props.setdefault(m.group(1), m.group(2))
                elif p.suffix in (".yml", ".yaml"):
                    stack: list[tuple[int, str]] = []
                    for ln in text.splitlines():
                        m = re.match(r"^(\s*)([\w.\-]+)\s*:\s*(.*?)\s*$", ln)
                        if not m or ln.lstrip().startswith("#"):
                            continue
                        ind = len(m.group(1))
                        while stack and stack[-1][0] >= ind:
                            stack.pop()
                        key = ".".join([k for _i, k in stack] + [m.group(2)])
                        if m.group(3):
                            props.setdefault(key, m.group(3).strip("'\""))
                        else:
                            stack.append((ind, m.group(2)))
            self._props = props
        return self._props


def _fill_arg(c: Constants, f: str, a: dict | None) -> dict | None:
    if not a:
        return a
    t = a.get("t")
    if t in ("str", "tpl") and ("{=" in a["v"] or "${" in a["v"]):
        v = c.fill(f, a["v"])
        return {**a, "t": "tpl" if "{=" in v or ":param" in v else "str", "v": v}
    if t in ("id", "member"):
        v = c.const(f, a["v"])
        return {"t": "str", "v": c.fill(f, v), "via": a["v"]} if v is not None else a
    if t == "arr":
        return {**a, "items": [_fill_arg(c, f, i) for i in a["items"]]}
    if t == "obj":
        return {**a, "props": {k: _fill_arg(c, f, v) if k in PATH_PROPS else v for k, v in a["props"].items()}}
    return a


def _fill_args(c: Constants, f: str, args: list) -> list:
    if not args:
        return args
    out = list(args)
    out[0] = _fill_arg(c, f, out[0])  # the path (or a format/method string)
    for i, a in enumerate(out[1:], start=1):
        if a and a.get("t") == "obj":
            out[i] = _fill_arg(c, f, a)
        elif a and a.get("t") in ("str", "tpl"):
            out[i] = _fill_arg(c, f, a)
    return out


def resolve(ctx) -> None:
    """Fills the path/prefix arguments of every record in place (once per build)."""
    c = Constants(ctx)
    for f, rec in ctx.parsed.items():
        for call in rec.get("calls", []):
            call["args"] = _fill_args(c, f, call.get("args") or [])
        for a in rec.get("assigns", []):
            a["args"] = _fill_args(c, f, a.get("args") or [])
        for s in rec.get("symbols", []):
            for owner in (s, *(s.get("methods") or [])):
                for d in owner.get("decorators") or []:
                    d["args"] = _fill_args(c, f, d.get("args") or [])
