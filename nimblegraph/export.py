"""`nimblegraph export --format mermaid|dot|json [target]`: the graph (or the chain of a target) for humans and tools."""

from __future__ import annotations

import json
import re

from nimblegraph import query
from nimblegraph.classify import FLOW_ORDER
from nimblegraph.query import Graph

LAYER_COLORS = {
    "entry": "#ffd166", "page": "#4fc1ff", "layout": "#9cdcfe", "route": "#569cd6", "component": "#c586c0",
    "hook": "#dcdcaa", "store": "#ce9178", "api-client": "#4ec9b0", "api-route": "#ff8a65", "middleware": "#d7ba7d",
    "controller": "#f48771", "service": "#b5cea8", "repository": "#7fb069", "model": "#e06c75", "util": "#9d9d9d",
    "type": "#6a9955", "config": "#858585", "test": "#6b6b6b", "module": "#a0a0a0",
}


def select(g: Graph, target: str | None, depth: int, notes=None) -> tuple[set[str], list[tuple[str, str, str]]]:
    """Files and (src, dst, kind) edges to export; without a target, the whole graph minus tests/config."""
    if target:
        files = query.chain_files(g, target, depth, notes)
        if not files:
            hit = g.find_files(target)
            files = set(hit)
    else:
        files = {p for p in g.files if g.layer(p) not in ("test", "config")}
    edges = []
    seen = set()
    for src in sorted(files):
        for e in g.out.get(src, []):
            if e.dst in files and e.kind != "type" and (src, e.dst) not in seen:
                seen.add((src, e.dst))
                edges.append((src, e.dst, e.kind))
    return files, edges


GENERIC_NAMES = {"page", "route", "index", "layout", "loading", "error", "main", "app", "utils", "types", "api"}


def short(path: str) -> str:
    """File name, with its folder when the name alone says nothing (Next's page.tsx, index.ts...)."""
    parts = path.split("/")
    if len(parts) > 1 and parts[-1].split(".")[0] in GENERIC_NAMES:
        return "/".join(parts[-2:])
    return parts[-1]


def _id(path: str, ids: dict[str, str]) -> str:
    if path not in ids:
        ids[path] = f"n{len(ids)}"
    return ids[path]


def to_mermaid(g: Graph, files: set[str], edges, notes=None) -> str:
    ids: dict[str, str] = {}
    lines = ["flowchart LR"]
    by_layer: dict[str, list[str]] = {}
    for f in sorted(files, key=lambda f: (FLOW_ORDER.get(g.layer(f), 50), f)):
        by_layer.setdefault(g.layer(f), []).append(f)
    for layer, fs in by_layer.items():
        lines.append(f"  subgraph {layer.replace('-', '_')}[{layer}]")
        for f in fs:
            title = (notes.files.get(f) or {}).get("title") if notes else None
            label = short(f) + (f"<br/><i>{title}</i>" if title else "")
            label = re.sub(r'["\[\]]', "'", label)
            lines.append(f'    {_id(f, ids)}["{label}"]')
        lines.append("  end")
    for s, d, kind in edges:
        arrow = "-.->" if kind in ("dynamic", "calls_api") else "-->"
        lab = "|API|" if kind == "calls_api" else ("|renders|" if kind == "renders" else "")
        lines.append(f"  {_id(s, ids)} {arrow}{lab} {_id(d, ids)}")
    for layer in by_layer:
        color = LAYER_COLORS.get(layer, "#a0a0a0")
        members = ",".join(_id(f, ids) for f in by_layer[layer])
        lines.append(f"  classDef {layer.replace('-', '_')}C fill:{color},stroke:#333,color:#111")
        lines.append(f"  class {members} {layer.replace('-', '_')}C")
    return "\n".join(lines) + "\n"


def to_dot(g: Graph, files: set[str], edges, notes=None) -> str:
    lines = ["digraph nimblegraph {", "  rankdir=LR;", '  node [shape=box, style="rounded,filled", fontname="Segoe UI", fontsize=10];']
    for f in sorted(files):
        title = (notes.files.get(f) or {}).get("title") if notes else None
        label = short(f) + (f"\\n{title}" if title else "")
        lines.append(f'  "{f}" [label="{label}", fillcolor="{LAYER_COLORS.get(g.layer(f), "#a0a0a0")}", tooltip="{f}"];')
    for s, d, kind in edges:
        style = ' [style=dashed, color="#ff9800", label="API"]' if kind == "calls_api" else (
            " [style=dashed]" if kind == "dynamic" else "")
        lines.append(f'  "{s}" -> "{d}"{style};')
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_json(g: Graph, files: set[str], edges, notes=None) -> str:
    nodes = [{"id": f, "layer": g.layer(f), "loc": g.files[f].loc, "tokens": g.files[f].tokens,
              "note": (notes.files.get(f) if notes else None)} for f in sorted(files)]
    links = [{"source": s, "target": d, "kind": k} for s, d, k in edges]
    routes = [{"method": r.method, "path": r.path, "file": r.file, "handler_file": r.handler_file, "handler": r.handler,
               "kind": r.kind} for r in g.routes.values() if r.target in files]
    return json.dumps({"nodes": nodes, "links": links, "routes": routes}, ensure_ascii=False, indent=1) + "\n"


FORMATS = {"mermaid": to_mermaid, "dot": to_dot, "json": to_json}
