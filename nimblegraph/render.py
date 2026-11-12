"""Compact, token-budgeted text output: indented lines (no JSON), path aliases ($c=src/components)
created only when they pay for their legend, and a cost footer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nimblegraph import tokens as tk

MAX_ALIASES = 6


@dataclass
class Out:
    title: str  # e.g. "deps src/app.ts"
    lines: list[str] = field(default_factory=list)
    paths: set[str] = field(default_factory=set)  # every file path printed (for aliases + "reading all" cost)
    read_cost: int = 0  # tokens to read every file referenced in full
    hint: str | None = None  # next command to suggest when the answer is cut
    stats: str = ""  # extra header segment, e.g. "14 files"

    def add(self, line: str = "") -> None:
        self.lines.append(line)

    def path(self, p: str) -> str:
        self.paths.add(p)
        return p


def choose_aliases(text: str, paths: set[str]) -> list[tuple[str, str]]:
    """Greedy: pick directory prefixes whose replacement saves more characters than the legend costs."""
    candidates: set[str] = set()
    for p in paths:
        parts = p.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            pre = "/".join(parts[:i]) + "/"
            if len(pre) >= 6:
                candidates.add(pre)
    chosen: list[tuple[str, str]] = []
    used_names: set[str] = set()
    for _ in range(MAX_ALIASES):
        best = None
        for pre in candidates:
            n = len(re.findall(r"(?<![\w$./-])" + re.escape(pre), text))
            if n < 2:
                continue
            alias = _alias_name(pre, used_names)
            saving = n * (len(pre) - len(alias) - 1) - (len(alias) + len(pre) + 2)
            if saving > 8 and (best is None or saving > best[0]):
                best = (saving, pre, alias)
        if not best:
            break
        _, pre, alias = best
        used_names.add(alias)
        chosen.append((alias, pre))
        text = re.sub(r"(?<![\w$./-])" + re.escape(pre), alias + "/", text)
        candidates.discard(pre)
    return chosen


def _alias_name(prefix: str, used: set[str]) -> str:
    last = prefix.rstrip("/").split("/")[-1]
    letters = re.sub(r"[^a-z0-9]", "", last.lower()) or "p"
    for cand in (letters[0], letters[:2], letters[:3]):
        if f"${cand}" not in used:
            return f"${cand}"
    i = 2
    while f"${letters[0]}{i}" in used:
        i += 1
    return f"${letters[0]}{i}"


def apply_aliases(lines: list[str], aliases: list[tuple[str, str]]) -> list[str]:
    out = []
    for line in lines:
        for alias, pre in aliases:
            line = re.sub(r"(?<![\w$./-])" + re.escape(pre), alias + "/", line)
        out.append(line)
    return out


DETAIL_MAX = 3


def progressive(make, budget: int, levels=(3, 2, 1, 0)) -> str:
    """Builds the answer at decreasing detail until it fits the budget:
    3 full, 2 without symbol lists, 1 notes reduced to titles, 0 deep levels replaced by counts;
    if it still does not fit, lines are cut and the next command is suggested."""
    o = None
    for d in levels:
        o = make(d)
        text = finish(o, budget, detail=d, cut=False)
        if tk.estimate(text) <= budget:
            return text
    return finish(o, budget, detail=levels[-1])


def finish(o: Out, budget: int, detail: int | None = None, cut: bool = True) -> str:
    body = "\n".join(o.lines)
    aliases = choose_aliases(body, o.paths)
    lines = apply_aliases(o.lines, aliases)
    legend = " ".join(f"{a}={p.rstrip('/')}" for a, p in aliases)
    head_extra = [legend] if legend else []

    def assemble(ls: list[str], cut: int) -> str:
        cost = tk.estimate("\n".join(head_extra + ls)) + 25
        header = f"@{o.title}" + (f" | {o.stats}" if o.stats else "") + f" | ~{tk.fmt(cost)} tok"
        foot = f"[~{tk.fmt(cost)} tok | reading all: ~{tk.fmt(o.read_cost)} tok"
        foot += f" | detail {detail}/{DETAIL_MAX}" if detail is not None else ""
        foot += f" | cut {cut} lines" if cut else ""
        foot += "]"
        parts = [header, *head_extra, *ls]
        if cut and o.hint:
            parts.append(f"> {o.hint}")
        parts.append(foot)
        return "\n".join(parts)

    text = assemble(lines, 0)
    if tk.estimate(text) <= budget or not cut:
        return text
    # over budget: cut from the end, keeping section headers ordered
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tk.estimate(assemble(lines[:mid], len(lines) - mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return assemble(lines[:lo], len(lines) - lo)
