"""Notes written by a local model (Hugging Face GGUF served by Ollama, LM Studio or llama-server): no Claude, no cloud.

The server is reached over HTTP with the standard library only. Ollama is detected (/api/version) and used through
its native /api/chat with a JSON schema; any other server is used through the OpenAI-compatible
/v1/chat/completions. Each file gets its graph context (layer, routes, exports, imports, importers, API calls) plus
its code; the answer is forced into the note shape and fitted to the token limits.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from nimblegraph import tokens as tk
from nimblegraph.notes import CHAIN_LIMITS, FILE_LIMITS, MAX_TAGS, NoteError, Notes, file_hash
from nimblegraph.query import Graph

DEFAULTS = {
    "url": "http://127.0.0.1:11434",
    "model": "hf.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q3_K_XL",
    "context": 8192,  # tokens the server keeps for prompt + answer
    "max_file_tokens": 3000,  # code sent per file (the head of big files)
    "temperature": 0.2,
    "timeout": 180,  # per call; a stuck server fails this file and the run moves on
}

FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "resp": {"type": "string"},
        "scope": {"type": "string"},
        "flow": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_TAGS},
    },
    "required": ["title", "summary", "resp", "scope", "flow", "tags"],
}
CHAIN_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "flow": {"type": "string"}},
    "required": ["title", "summary", "flow"],
}

FILE_SYSTEM = """You write the index notes ("fichas") of a codebase. For the file given, answer ONLY a JSON object:
- title: what the file is, at most 6 words (e.g. "Controller de usuários")
- summary: what it does, at most 22 words, concrete (entities, rules, side effects)
- resp: what is and what is NOT its responsibility, at most 16 words
- scope: the feature/domain, 1 or 2 words, lowercase (e.g. "user", "billing")
- flow: its main steps joined by " > ", at most 20 words (e.g. "req > valida > service.create > 201")
- tags: up to 4 short lowercase tags (e.g. "crud", "auth-required", "orm")
Write title, summary, resp and flow in {language}. Be specific to THIS file; never repeat the file name as the title;
do not invent what is not in the code."""

CHAIN_SYSTEM = """You describe a whole feature of a codebase (a chain of files from the UI to the database).
Answer ONLY a JSON object:
- title: the feature, at most 6 words
- summary: what the feature does end to end, at most 35 words
- flow: the steps in order joined by " > ", at most 45 words
  (e.g. "página lista > cliente HTTP > GET /api/users > controller valida > service > repositório")
Write in {language}. Use only what the notes and the flow below say."""


class LocalAIError(RuntimeError):
    pass


def settings(cfg) -> dict:
    out = dict(DEFAULTS)
    out.update(cfg.settings.get("local_ai", {}) or {})
    return out


# ---------------------------------------------------------------------- HTTP client

def _request(url: str, payload: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LocalAIError(f"{e.code} from {url}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise LocalAIError(f"cannot reach {url}: {e}") from e


@dataclass
class Client:
    url: str
    model: str
    context: int = 8192
    temperature: float = 0.2
    timeout: float = 600
    kind: str = ""  # "ollama" | "openai"
    stats: dict = field(default_factory=lambda: {"calls": 0, "seconds": 0.0, "out_tokens": 0})

    def detect(self) -> str:
        base = self.url.rstrip("/")
        try:
            _request(f"{base}/api/version", timeout=3)
            self.kind = "ollama"
        except LocalAIError:
            try:
                _request(f"{base}/v1/models", timeout=3)
                self.kind = "openai"
            except LocalAIError as e:
                raise LocalAIError(f"no local AI server at {self.url} (start Ollama / LM Studio / llama-server)") from e
        return self.kind

    def models(self) -> list[str]:
        base = self.url.rstrip("/")
        if (self.kind or self.detect()) == "ollama":
            return [m["name"] for m in _request(f"{base}/api/tags", timeout=5).get("models", [])]
        return [m["id"] for m in _request(f"{base}/v1/models", timeout=5).get("data", [])]

    def has_model(self) -> bool:
        want = self.model.lower()
        return any(m.lower() == want or m.lower() == f"{want}:latest" for m in self.models())

    def pull(self, progress=None) -> None:
        """Downloads the model into Ollama (the GGUF comes straight from Hugging Face for hf.co/... names)."""
        if (self.kind or self.detect()) != "ollama":
            raise LocalAIError("pull is only available with Ollama; load the model in your server instead")
        req = urllib.request.Request(f"{self.url.rstrip('/')}/api/pull", method="POST",
                                     data=json.dumps({"model": self.model, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=None) as r:
            for raw in r:
                ev = json.loads(raw.decode("utf-8") or "{}")
                if ev.get("error"):
                    raise LocalAIError(ev["error"])
                if progress:
                    progress(ev)

    def chat_json(self, system: str, user: str, schema: dict, max_tokens: int = 400) -> dict:
        base = self.url.rstrip("/")
        t0 = time.time()
        if (self.kind or self.detect()) == "ollama":
            body = {
                "model": self.model, "stream": False, "format": schema, "keep_alive": "30m",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {"temperature": self.temperature, "num_ctx": self.context, "num_predict": max_tokens},
            }
            res = _request(f"{base}/api/chat", body, timeout=self.timeout)
            text = (res.get("message") or {}).get("content", "")
            self.stats["out_tokens"] += int(res.get("eval_count") or 0)
        else:
            body = {
                "model": self.model, "temperature": self.temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "note", "schema": schema}},
            }
            res = _request(f"{base}/v1/chat/completions", body, timeout=self.timeout)
            text = ((res.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            self.stats["out_tokens"] += int((res.get("usage") or {}).get("completion_tokens") or 0)
        self.stats["calls"] += 1
        self.stats["seconds"] += time.time() - t0
        return parse_json(text)


def client_for(cfg) -> Client:
    s = settings(cfg)
    return Client(url=s["url"], model=s["model"], context=int(s["context"]), temperature=float(s["temperature"]),
                  timeout=float(s["timeout"]))


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group())
            except ValueError:
                pass
    raise LocalAIError(f"the model did not answer JSON: {text[:200]!r}")


# ---------------------------------------------------------------------- fitting to the note limits

def fit_text(text: str, limit_tokens: int) -> str:
    """Shortens to the token limit at a clause or word boundary."""
    text = " ".join(str(text or "").split()).strip()
    max_chars = int(limit_tokens * tk.CHARS_PER_TOKEN) - 1
    if tk.estimate(text) <= limit_tokens:
        return text
    cut = text[:max_chars]
    for sep in (". ", "; ", ", ", " > ", " "):
        i = cut.rfind(sep)
        if i > max_chars * 0.5:
            return cut[:i].rstrip(" ,;:>")
    return cut.rstrip()


def fit_tags(tags) -> list[str]:
    out = []
    for t in tags or []:
        t = re.sub(r"[^a-z0-9à-ú#+._-]+", "-", str(t).lower()).strip("-")
        if t and t not in out and len(t) <= 24:
            out.append(t)
    return out[:4]


def fit_note(raw: dict, limits: dict) -> dict:
    out = {k: fit_text(raw.get(k, ""), lim) for k, lim in limits.items() if raw.get(k)}
    if "scope" in out:
        out["scope"] = out["scope"].lower()
    if "tags" in raw and limits is FILE_LIMITS:
        out["tags"] = fit_tags(raw["tags"])
    return out


# ---------------------------------------------------------------------- prompts

def _short(paths, n: int = 8) -> str:
    paths = list(paths)
    s = ", ".join(paths[:n])
    return s + (f" (+{len(paths) - n})" if len(paths) > n else "")


def file_context(g: Graph, notes: Notes, root: Path, path: str, max_file_tokens: int) -> str:
    f = g.files[path]
    lines = [f"File: {path}", f"Layer: {f.layer or 'module'}" + (f" | package: {f.pkg}" if f.pkg else "")]
    routes = g.routes_of_file(path)
    if routes:
        lines.append("Routes: " + ", ".join(r.label + (f" -> {r.handler}" if r.handler else "") for r in routes[:12]))
    exported = [f"{s['name']} ({s['kind']})" for s in g.symbols.get(path, []) if s["exported"] and not s["parent"]]
    if exported:
        lines.append("Exports: " + ", ".join(exported[:15]))
    deps = sorted({e.dst for e in g.out.get(path, []) if e.kind in ("import", "dynamic", "type")})
    if deps:
        lines.append("Imports: " + _short(deps))
    known = [f"{d} = {notes.files[d]['title']}" for d in deps if d in notes.files][:6]
    if known:
        lines.append("Notes of what it imports: " + "; ".join(known))
    users = sorted(g.importers(path))
    if users:
        lines.append(f"Used by {len(users)} files: " + _short(users, 5))
    calls = g.calls_from(path)
    if calls:
        lines.append("HTTP calls: " + ", ".join(
            f"{c.method} {c.url}" + (f" -> {g.routes[c.route_id].target}" if c.route_id else "") for c in calls[:8]))
    if g.externals.get(path):
        lines.append("Packages: " + ", ".join(sorted(g.externals[path])[:10]))
    try:
        code = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        code = ""
    max_chars = int(max_file_tokens * tk.CHARS_PER_TOKEN)
    if len(code) > max_chars:
        code = code[:max_chars] + "\n/* ... file truncated ... */"
    lines.append("--- code ---")
    lines.append(code)
    return "\n".join(lines)


def chain_context(g: Graph, notes: Notes, key: str, flow: list[dict]) -> str:
    lines = [f"Feature: {key}", "Flow (indent = uses):"]
    for step in flow[:80]:
        if step.get("ref"):
            continue
        n = notes.files.get(step["path"]) or {}
        label = f"~> {step['label']} " if step.get("label") else ""
        title = f" — {n['title']}: {n.get('summary', '')}" if n else ""
        lines.append(f"{'  ' * min(step['depth'], 8)}{label}{step['path']} [{step.get('layer', '')}]{title}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- describing

@dataclass
class Result:
    path: str
    ok: bool
    seconds: float
    title: str = ""
    error: str = ""


def describe_file(client: Client, g: Graph, notes: Notes, root: Path, path: str, language: str,
                  max_file_tokens: int) -> Result:
    t0 = time.time()
    try:
        raw = client.chat_json(FILE_SYSTEM.format(language=language),
                               file_context(g, notes, root, path, max_file_tokens), FILE_SCHEMA)
        fields = fit_note(raw, FILE_LIMITS)
        if not fields.get("title"):
            raise LocalAIError("empty title")
        note = notes.set_file(path, fields, file_hash(root / path), by=f"local:{client.model}")
        return Result(path, True, time.time() - t0, note["title"])
    except (LocalAIError, NoteError) as e:
        return Result(path, False, time.time() - t0, error=str(e)[:200])


def describe_chain(client: Client, g: Graph, notes: Notes, key: str, flow: list[dict], files: dict[str, str],
                   language: str) -> Result:
    t0 = time.time()
    try:
        raw = client.chat_json(CHAIN_SYSTEM.format(language=language), chain_context(g, notes, key, flow), CHAIN_SCHEMA)
        note = notes.set_chain(key, fit_note(raw, CHAIN_LIMITS), files, by=f"local:{client.model}")
        return Result(key, True, time.time() - t0, note["title"])
    except (LocalAIError, NoteError) as e:
        return Result(key, False, time.time() - t0, error=str(e)[:200])


def eta(done: int, total: int, elapsed: float) -> str:
    if not done:
        return "?"
    left = (total - done) * elapsed / done
    h, rem = divmod(int(left), 3600)
    return f"{h}h{rem // 60:02d}m" if h else f"{rem // 60}m{rem % 60:02d}s"


# ---------------------------------------------------------------------- batches

def select_files(g: Graph, notes: Notes, files=None, force: bool = False) -> list[str]:
    """Files to describe, most important first; up-to-date notes are skipped unless force."""
    from nimblegraph.update import IMPORTANCE, todo_items
    if not force:
        return todo_items(g, notes, files)
    from nimblegraph.notes import SKIP_LAYERS
    pool = [p for p in (files if files is not None else g.files) if g.layer(p) not in SKIP_LAYERS]
    return sorted(pool, key=lambda p: (IMPORTANCE.get(g.layer(p), 3), -len(g.importers(p)), p))


def chain_keys_from_scopes(notes: Notes, min_files: int = 3) -> list[str]:
    counts: dict[str, int] = {}
    for key, n in notes.files.items():
        s = str(n.get("scope") or "").strip().lower()
        if s and "#" not in key:
            counts[s] = counts.get(s, 0) + 1
    return sorted(k for k, c in counts.items() if c >= min_files)


def run_batch(cfg, g: Graph, notes: Notes, paths: list[str], client: Client, language: str, on_event=None,
              stop=None, chains: list[str] | None = None, save_every: int = 5) -> dict:
    """Describes `paths` (then the `chains`), saving notes.json every few files so a stopped run resumes."""
    from nimblegraph import api
    s = settings(cfg)
    t0 = time.time()
    done = ok = 0
    total = len(paths) + len(chains or [])
    try:
        for p in paths:
            if stop is not None and stop.is_set():
                break
            r = describe_file(client, g, notes, cfg.root, p, language, int(s["max_file_tokens"]))
            done += 1
            ok += r.ok
            if on_event:
                on_event({"kind": "file", "i": done, "total": total, "path": p, "ok": r.ok, "title": r.title,
                          "error": r.error, "seconds": round(r.seconds, 1), "eta": eta(done, total, time.time() - t0)})
            if done % save_every == 0:
                notes.save()
        notes.save()
        for key in chains or []:
            if stop is not None and stop.is_set():
                break
            ch = api.chain_payload(g, notes, key)
            files = {f: g.files[f].hash for f in ch["files"] if f in g.files and g.layer(f) not in ("test", "config")}
            if not files:  # reported, so the counter always reaches the total
                done += 1
                if on_event:
                    on_event({"kind": "chain", "i": done, "total": total, "path": key, "ok": True, "skipped": True,
                              "title": "skipped: no files for this term", "error": "", "seconds": 0.0,
                              "eta": eta(done, total, time.time() - t0)})
                continue
            r = describe_chain(client, g, notes, key, ch["flow"], files, language)
            done += 1
            ok += r.ok
            if on_event:
                on_event({"kind": "chain", "i": done, "total": total, "path": key, "ok": r.ok, "title": r.title,
                          "error": r.error, "seconds": round(r.seconds, 1), "eta": eta(done, total, time.time() - t0)})
            notes.save()
    finally:
        notes.save()
    return {"done": done, "ok": ok, "total": total, "seconds": round(time.time() - t0, 1),
            "out_tokens": client.stats["out_tokens"]}
