"""Import resolution for the Python engine, and post-pass for what the Node engine left unresolved:
tsconfig/jsconfig paths (extends, references), monorepo workspace packages, package.json `imports`,
vite aliases, config.toml aliases, `.js` -> `.ts` ESM imports, and dist/ -> src/ mapping."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
ASSET_EXTS = {
    ".css", ".scss", ".sass", ".less", ".styl", ".pcss", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".wav", ".webm", ".ogg", ".json",
    ".md", ".mdx", ".txt", ".html", ".wasm", ".graphql", ".gql", ".yaml", ".yml", ".csv", ".pdf", ".glsl", ".vue",
    ".svelte", ".astro",
}
JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
BUILD_DIRS = ("dist/", "build/", "lib/", "out/", "esm/", "cjs/")
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram",
    "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib", "test",
}

RE_JSON_COMMENT = re.compile(r'("(?:[^"\\]|\\.)*")|//[^\n]*|/\*[\s\S]*?\*/')
RE_TRAILING_COMMA = re.compile(r'("(?:[^"\\]|\\.)*")|,(\s*[}\]])')


@dataclass(frozen=True)
class Resolution:
    kind: str  # file | ext | asset | unresolved
    target: str | None = None  # root-relative path (file) or package name (ext)


def read_jsonc(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    text = RE_JSON_COMMENT.sub(lambda m: m.group(1) or "", text)
    text = RE_TRAILING_COMMA.sub(lambda m: m.group(1) or m.group(2), text)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def package_name(spec: str) -> str:
    if spec.startswith("node:"):
        return spec
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0]


def is_asset(spec: str) -> bool:
    if "?" in spec or spec.startswith("data:"):
        return True
    ext = posixpath.splitext(spec.split("?")[0])[1].lower()
    return ext in ASSET_EXTS


class TsConfig:
    """Merged compilerOptions relevant to resolution: baseUrl and paths (absolute posix dirs)."""

    def __init__(self, base_url: str | None, paths: dict[str, list[str]], paths_base: str | None, refs: list[str],
                 include_dirs: list[str]):
        self.base_url = base_url  # root-relative dir or None
        self.paths = paths
        self.paths_base = paths_base  # root-relative dir paths are relative to
        self.refs = refs  # root-relative referenced config files
        self.include_dirs = include_dirs  # root-relative static prefixes of "include"


class Resolver:
    def __init__(self, root: Path, files: set[str], packages: list[str] | None = None, aliases: dict | None = None):
        self.root = root
        self.files = files
        self.dirs: set[str] = set()
        for f in files:
            d = posixpath.dirname(f)
            while d and d not in self.dirs:
                self.dirs.add(d)
                d = posixpath.dirname(d)
        self.top_dirs = {f.split("/", 1)[0] for f in files if "/" in f}
        self.aliases = dict(aliases or {})
        self._cfg_for_dir: dict[str, str | None] = {}
        self._configs: dict[str, TsConfig | None] = {}
        self._pkg_json: dict[str, dict | None] = {}
        self.workspaces: dict[str, str] = {}  # package name -> dir
        for d in packages if packages is not None else self._find_packages():
            pj = self.package_json(d)
            if pj and isinstance(pj.get("name"), str):
                self.workspaces.setdefault(pj["name"], d)
        self.vite_aliases = self._vite_aliases()
        self._cache: dict[tuple[str, str], Resolution] = {}

    # ------------------------------------------------------------ small helpers
    def _abs(self, rel: str) -> Path:
        return self.root / rel if rel else self.root

    def _find_packages(self) -> list[str]:
        return sorted({posixpath.dirname(f) for f in self.files} | {""})

    def package_json(self, d: str) -> dict | None:
        if d not in self._pkg_json:
            p = self._abs(d) / "package.json"
            self._pkg_json[d] = read_jsonc(p) if p.is_file() else None
        return self._pkg_json[d]

    def try_file(self, base: str) -> str | None:
        """base is root-relative without guarantees about extension; returns an indexed file or None."""
        base = posixpath.normpath(base) if base else base
        if base.startswith("../") or base == "..":
            return None
        if base in self.files:
            return base
        stem, ext = posixpath.splitext(base)
        if ext in JS_TO_TS:
            for alt in JS_TO_TS[ext]:
                if stem + alt in self.files:
                    return stem + alt
        if base.endswith(".d.ts"):
            stem = base[:-5]
            for e in SOURCE_EXTS:
                if stem + e in self.files:
                    return stem + e
        for e in SOURCE_EXTS:
            if base + e in self.files:
                return base + e
        if base in self.dirs or base == "":
            pj = self.package_json(base) if base else None
            if pj and base:
                hit = self._package_entry(base, pj)
                if hit:
                    return hit
            for e in SOURCE_EXTS:
                cand = f"{base}/index{e}" if base else f"index{e}"
                if cand in self.files:
                    return cand
        return None

    def map_build_to_src(self, rel: str) -> str | None:
        """packages/x/dist/index.js -> packages/x/src/index.ts (also .d.ts)."""
        for bd in BUILD_DIRS:
            idx = rel.find(bd) if rel.startswith(bd) else rel.find("/" + bd)
            if idx < 0:
                continue
            start = idx if rel.startswith(bd) and idx == 0 else idx + 1
            head, tail = rel[:start], rel[start + len(bd):]
            tail = re.sub(r"\.d\.(m|c)?ts$", "", tail)
            tail = re.sub(r"\.(m|c)?jsx?$", "", tail)
            for cand in (f"{head}src/{tail}", f"{head}{tail}"):
                hit = self.try_file(cand)
                if hit:
                    return hit
        return None

    def normalize(self, rel: str) -> str | None:
        """Maps a path produced by the Node engine to an indexed file."""
        if rel in self.files:
            return rel
        return self.try_file(rel) or self.map_build_to_src(rel)

    # ------------------------------------------------------------ package.json entries/exports
    @staticmethod
    def _pick_condition(v) -> str | None:
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            for x in v:
                r = Resolver._pick_condition(x)
                if r:
                    return r
            return None
        if isinstance(v, dict):
            for key in ("source", "development", "import", "module", "browser", "node", "require", "default", "types"):
                if key in v:
                    r = Resolver._pick_condition(v[key])
                    if r:
                        return r
            for val in v.values():
                r = Resolver._pick_condition(val)
                if r:
                    return r
        return None

    def _target(self, pkg_dir: str, target: str) -> str | None:
        rel = posixpath.normpath(posixpath.join(pkg_dir, target)) if pkg_dir else posixpath.normpath(target)
        return self.try_file(rel) or self.map_build_to_src(rel)

    def _package_entry(self, pkg_dir: str, pj: dict, sub: str = ".") -> str | None:
        exports = pj.get("exports")
        if exports is not None:
            if isinstance(exports, (str, list)) or (isinstance(exports, dict) and not any(k.startswith(".") for k in exports)):
                exports = {".": exports}
            if isinstance(exports, dict):
                if sub in exports:
                    t = self._pick_condition(exports[sub])
                    if t:
                        hit = self._target(pkg_dir, t)
                        if hit:
                            return hit
                for pat, val in exports.items():
                    if "*" in pat:
                        pre, post = pat.split("*", 1)
                        if sub.startswith(pre) and sub.endswith(post) and len(sub) >= len(pre) + len(post):
                            star = sub[len(pre):len(sub) - len(post)] if post else sub[len(pre):]
                            t = self._pick_condition(val)
                            if t:
                                hit = self._target(pkg_dir, t.replace("*", star))
                                if hit:
                                    return hit
        if sub == ".":
            for key in ("source", "module", "main", "types", "typings"):
                if isinstance(pj.get(key), str):
                    hit = self._target(pkg_dir, pj[key])
                    if hit:
                        return hit
            for cand in ("src/index", "index", "src/main", "lib/index"):
                hit = self.try_file(posixpath.join(pkg_dir, cand) if pkg_dir else cand)
                if hit:
                    return hit
            return None
        sub_path = sub[2:]
        base = posixpath.join(pkg_dir, sub_path) if pkg_dir else sub_path
        return self.try_file(base) or self.try_file(posixpath.join(pkg_dir, "src", sub_path)) or self.map_build_to_src(base)

    # ------------------------------------------------------------ tsconfig
    def _config_file_for(self, d: str) -> str | None:
        if d in self._cfg_for_dir:
            return self._cfg_for_dir[d]
        found = None
        for name in ("tsconfig.json", "jsconfig.json"):
            cand = f"{d}/{name}" if d else name
            if (self.root / cand).is_file():
                found = cand
                break
        if found is None and d:
            found = self._config_file_for(posixpath.dirname(d))
        self._cfg_for_dir[d] = found
        return found

    def _load_config(self, rel: str, depth: int = 0) -> TsConfig | None:
        if rel in self._configs:
            return self._configs[rel]
        self._configs[rel] = None  # cycle guard
        data = read_jsonc(self.root / rel)
        if data is None:
            return None
        cdir = posixpath.dirname(rel)
        base_url = paths_base = None
        paths: dict[str, list[str]] = {}
        ext = data.get("extends")
        for parent_spec in ([ext] if isinstance(ext, str) else ext if isinstance(ext, list) else []):
            parent = self._extends_path(cdir, parent_spec)
            if parent and depth < 8:
                pc = self._load_config(parent, depth + 1)
                if pc:
                    base_url = pc.base_url or base_url
                    if pc.paths:
                        paths, paths_base = pc.paths, pc.paths_base
        opts = data.get("compilerOptions") or {}
        if isinstance(opts.get("baseUrl"), str):
            base_url = posixpath.normpath(posixpath.join(cdir, opts["baseUrl"])) if cdir else posixpath.normpath(opts["baseUrl"])
            base_url = "" if base_url == "." else base_url
        if isinstance(opts.get("paths"), dict):
            paths = {k: [v] if isinstance(v, str) else list(v) for k, v in opts["paths"].items()}
            paths_base = cdir
        if base_url is not None:  # TS: paths are relative to baseUrl when there is one
            paths_base = base_url
        refs = []
        for r in data.get("references") or []:
            if isinstance(r, dict) and isinstance(r.get("path"), str):
                p = posixpath.normpath(posixpath.join(cdir, r["path"])) if cdir else posixpath.normpath(r["path"])
                if not p.endswith(".json"):
                    p = f"{p}/tsconfig.json"
                refs.append(p)
        include_dirs = []
        for inc in data.get("include") or []:
            if isinstance(inc, str):
                static = re.split(r"[*?{]", inc)[0].rstrip("/")
                static = posixpath.normpath(posixpath.join(cdir, static)) if cdir else posixpath.normpath(static or ".")
                include_dirs.append("" if static == "." else static)
        cfg = TsConfig(base_url, paths, paths_base, refs, include_dirs)
        self._configs[rel] = cfg
        return cfg

    def _extends_path(self, cdir: str, spec: str) -> str | None:
        if spec.startswith("."):
            p = posixpath.normpath(posixpath.join(cdir, spec)) if cdir else posixpath.normpath(spec)
            for cand in (p, p + ".json", f"{p}/tsconfig.json"):
                if (self.root / cand).is_file():
                    return cand
            return None
        # package config (e.g. @tsconfig/node20/tsconfig.json) or workspace package
        name = package_name(spec)
        sub = spec[len(name) + 1:]
        if name in self.workspaces:
            d = self.workspaces[name]
            for cand in (posixpath.join(d, sub or "tsconfig.json"), posixpath.join(d, (sub or "tsconfig") + ".json")):
                if (self.root / cand).is_file():
                    return cand
        d = cdir
        while True:
            nm = posixpath.join(d, "node_modules", spec) if d else posixpath.join("node_modules", spec)
            for cand in (nm, nm + ".json", f"{nm}/tsconfig.json"):
                if (self.root / cand).is_file():
                    return cand
            if not d:
                return None
            d = posixpath.dirname(d)

    def config_for(self, file: str) -> TsConfig | None:
        cf = self._config_file_for(posixpath.dirname(file))
        if not cf:
            return None
        cfg = self._load_config(cf)
        if cfg is None or cfg.paths or not cfg.refs:
            return cfg
        refs = [c for c in (self._load_config(r) for r in cfg.refs if (self.root / r).is_file()) if c]
        for r in refs:
            if any(file == d or file.startswith(d + "/") or d == "" for d in r.include_dirs):
                if r.paths or r.base_url is not None:
                    return r
        return next((r for r in refs if r.paths), cfg)

    def _apply_paths(self, cfg: TsConfig, spec: str) -> str | None:
        best = None
        best_len = -1
        for pat, targets in cfg.paths.items():
            if "*" in pat:
                pre, post = pat.split("*", 1)
                if spec.startswith(pre) and spec.endswith(post) and len(spec) >= len(pre) + len(post) and len(pre) > best_len:
                    best, best_len = (targets, spec[len(pre):len(spec) - len(post)] if post else spec[len(pre):]), len(pre)
            elif pat == spec and len(pat) > best_len:
                best, best_len = (targets, ""), len(pat)
        if not best:
            return None
        targets, star = best
        base = cfg.paths_base or ""
        for t in targets:
            cand = posixpath.join(base, t.replace("*", star)) if base else t.replace("*", star)
            hit = self.try_file(cand) or self.map_build_to_src(posixpath.normpath(cand))
            if hit:
                return hit
        return None

    # ------------------------------------------------------------ vite / package imports
    def _vite_aliases(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for f in sorted(self.files):
            name = posixpath.basename(f)
            if not re.match(r"vite(st)?\.config\.(m|c)?[jt]s$", name):
                continue
            try:
                text = (self.root / f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cdir = posixpath.dirname(f)
            m = re.search(r"alias\s*:\s*([\[{])", text)
            if not m:
                continue
            block = text[m.end():m.end() + 3000]
            # object form: '@': path.resolve(__dirname, './src')
            for km in re.finditer(r"""['"]?([@~#$\w./-]+)['"]?\s*:\s*((?:[^,\n}()]|\([^)]*\))+)""", block):
                key, val = km.group(1), km.group(2)
                if key in ("find", "replacement"):
                    continue
                strs = re.findall(r"""['"]([^'"]+)['"]""", val)
                if strs:
                    out.append((key, self._alias_target(cdir, strs[-1])))
            for fm in re.finditer(r"""find\s*:\s*['"]([^'"]+)['"]\s*,\s*replacement\s*:\s*([^}]+)""", block):
                strs = re.findall(r"""['"]([^'"]+)['"]""", fm.group(2))
                if strs:
                    out.append((fm.group(1), self._alias_target(cdir, strs[-1])))
        return out

    @staticmethod
    def _alias_target(cdir: str, target: str) -> str:
        target = target.lstrip("/") if not target.startswith("./") else target
        t = posixpath.normpath(posixpath.join(cdir, target)) if cdir else posixpath.normpath(target)
        return "" if t == "." else t

    def _alias_hit(self, aliases, spec: str) -> str | None:
        for key, target in sorted(aliases, key=lambda kv: -len(kv[0])):
            if spec == key or spec.startswith(key.rstrip("/") + "/"):
                rest = spec[len(key):].lstrip("/")
                cand = posixpath.join(target, rest) if rest else target
                hit = self.try_file(cand)
                if hit:
                    return hit
        return None

    def _package_imports(self, file: str, spec: str) -> str | None:
        d = posixpath.dirname(file)
        while True:
            pj = self.package_json(d)
            if pj and isinstance(pj.get("imports"), dict):
                imports = pj["imports"]
                for pat, val in imports.items():
                    star = None
                    if "*" in pat:
                        pre, post = pat.split("*", 1)
                        if spec.startswith(pre) and spec.endswith(post):
                            star = spec[len(pre):len(spec) - len(post)] if post else spec[len(pre):]
                    elif pat == spec:
                        star = ""
                    if star is None:
                        continue
                    t = self._pick_condition(val)
                    if t and t.startswith("."):
                        hit = self._target(d, t.replace("*", star))
                        if hit:
                            return hit
                return None
            if not d:
                return None
            d = posixpath.dirname(d)

    # ------------------------------------------------------------ main entry
    def resolve(self, spec: str, file: str) -> Resolution:
        key = (posixpath.dirname(file), spec)
        hit = self._cache.get(key)
        if hit is None:
            hit = self._resolve(spec, file)
            self._cache[key] = hit
        return hit

    def _resolve(self, spec: str, file: str) -> Resolution:
        if not spec:
            return Resolution("unresolved")
        if spec.startswith((".", "/")):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(file), spec)) if spec.startswith(".") else spec.lstrip("/")
            hit = self.try_file(base) or self.map_build_to_src(base)
            if hit:
                return Resolution("file", hit)
            return Resolution("asset") if is_asset(spec) else Resolution("unresolved")
        clean = spec.split("?")[0]
        hit = self._alias_hit(self.aliases.items(), clean)
        if hit:
            return Resolution("file", hit)
        cfg = self.config_for(file)
        if cfg and cfg.paths:
            hit = self._apply_paths(cfg, clean)
            if hit:
                return Resolution("file", hit)
        if spec.startswith("#"):
            hit = self._package_imports(file, clean)
            if hit:
                return Resolution("file", hit)
        hit = self._alias_hit(self.vite_aliases, clean)
        if hit:
            return Resolution("file", hit)
        name = package_name(clean)
        if name in self.workspaces:
            d = self.workspaces[name]
            sub = "." + clean[len(name):] if clean != name else "."
            pj = self.package_json(d) or {}
            hit = self._package_entry(d, pj, sub)
            if hit:
                return Resolution("file", hit)
        if cfg and cfg.base_url is not None:
            hit = self.try_file(posixpath.join(cfg.base_url, clean) if cfg.base_url else clean)
            if hit:
                return Resolution("file", hit)
        if is_asset(spec):
            return Resolution("asset")
        if spec.startswith(("@/", "~", "#", "$")) or (name in self.top_dirs and name not in NODE_BUILTINS):
            return Resolution("unresolved")
        bare = name[5:] if name.startswith("node:") else name
        return Resolution("ext", bare if bare in NODE_BUILTINS else name)
