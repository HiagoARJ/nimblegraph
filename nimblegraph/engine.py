"""Picks the extraction engine: Node (latest LTS via fnm, then PATH) with the TypeScript
compiler API, falling back to the pure-Python parser."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from nimblegraph.config import user_cache_dir

NODE_DIR = Path(__file__).parent / "node"
EXTRACT = NODE_DIR / "extract.mjs"
DEFAULT_LTS_MAJOR = 24
MIN_MAJOR = 20
LTS_TTL = 7 * 24 * 3600
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class NodeRuntime:
    cmd: list[str]
    version: str  # "v24.21.0"
    via: str  # "fnm" | "fnm exec" | "PATH"

    @property
    def major(self) -> int:
        return major_of(self.version)

    def describe(self) -> str:
        return f"node {self.version} via {self.via}"


@dataclass
class ExtractResult:
    records: dict[str, dict] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)  # files the engine could not parse
    header: dict = field(default_factory=dict)
    warning: str | None = None


def major_of(version: str) -> int:
    m = re.match(r"v?(\d+)", version or "")
    return int(m.group(1)) if m else 0


def _run(cmd: list[str], timeout: float = 15, **kw) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace", creationflags=_NO_WINDOW, **kw)
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------ cache (~/.nimblegraph/node.json)

def _cache_file() -> Path:
    return user_cache_dir() / "node.json"


def _load_cache() -> dict:
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    try:
        _cache_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


# ------------------------------------------------------------------ fnm

def find_fnm() -> str | None:
    exe = shutil.which("fnm")
    if exe:
        return exe
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.is_dir():
            for d in sorted(pkgs.glob("Schniz.fnm_*"), reverse=True):
                if (d / "fnm.exe").is_file():
                    return str(d / "fnm.exe")
    for p in (Path.home() / ".local" / "share" / "fnm" / "fnm", Path.home() / ".fnm" / "fnm"):
        if p.is_file():
            return str(p)
    return None


def fnm_dir() -> Path | None:
    candidates = []
    if os.environ.get("FNM_DIR"):
        candidates.append(Path(os.environ["FNM_DIR"]))
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "fnm")
    candidates += [Path.home() / ".local" / "share" / "fnm", Path.home() / ".fnm"]
    for c in candidates:
        if (c / "node-versions").is_dir():
            return c
    return None


def fnm_installed() -> dict[str, Path]:
    """version ('v24.21.0') -> node executable, for every Node installed by fnm."""
    base = fnm_dir()
    out: dict[str, Path] = {}
    if not base:
        return out
    for d in (base / "node-versions").iterdir():
        inst = d / "installation"
        for exe in (inst / "node.exe", inst / "bin" / "node"):
            if exe.is_file():
                out[d.name] = exe
                break
    return out


def _vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def latest_lts(refresh: bool = False, offline: bool = False) -> dict:
    """{'version': 'v24.21.0', 'codename': 'Krypton'}; cached for 7 days."""
    cache = _load_cache()
    lts = cache.get("lts")
    fresh = lts and time.time() - lts.get("checked", 0) < LTS_TTL
    if lts and (fresh or offline) and not refresh:
        return lts
    fnm = None if offline else find_fnm()
    if fnm:
        r = _run([fnm, "list-remote", "--lts"], timeout=20)
        if r and r.returncode == 0:
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("v")]
            if lines:
                m = re.match(r"(v[\d.]+)\s*(?:\((.+)\))?", lines[-1])
                if m:
                    lts = {"version": m.group(1), "codename": m.group(2) or "", "checked": time.time()}
                    cache["lts"] = lts
                    _save_cache(cache)
                    return lts
    return lts or {"version": f"v{DEFAULT_LTS_MAJOR}", "codename": "", "checked": 0}


# ------------------------------------------------------------------ node detection

def _node_version(cmd: list[str]) -> str | None:
    r = _run([*cmd, "--version"], timeout=15)
    if r and r.returncode == 0 and r.stdout.strip().startswith("v"):
        return r.stdout.strip()
    return None


def _candidates(lts_major: int) -> list[tuple[list[str], str]]:
    out: list[tuple[list[str], str]] = []
    installed = fnm_installed()
    same = sorted((v for v in installed if major_of(v) == lts_major), key=_vkey, reverse=True)
    for v in same:
        out.append(([str(installed[v])], "fnm"))
    fnm = find_fnm()
    if fnm:
        out.append(([fnm, "exec", f"--using={lts_major}", "--", "node"], "fnm exec"))
    path_node = shutil.which("node")
    if path_node:
        out.append(([path_node], "PATH"))
    newer = sorted((v for v in installed if major_of(v) >= MIN_MAJOR and major_of(v) != lts_major), key=_vkey, reverse=True)
    for v in newer:
        out.append(([str(installed[v])], "fnm"))
    return out


def find_node(refresh: bool = False) -> NodeRuntime | None:
    lts_major = major_of(latest_lts(offline=True)["version"]) or DEFAULT_LTS_MAJOR
    cache = _load_cache()
    cached = cache.get("node")
    if cached and not refresh:
        cmd = cached["cmd"]
        if Path(cmd[0]).is_file() and major_of(cached["version"]) >= lts_major:
            return NodeRuntime(cmd, cached["version"], cached["via"])
    best: NodeRuntime | None = None
    for cmd, via in _candidates(lts_major):
        v = _node_version(cmd)
        if not v or major_of(v) < MIN_MAJOR:
            continue
        rt = NodeRuntime(cmd, v, via)
        if rt.major >= lts_major:
            best = rt
            break
        if best is None:
            best = rt
    if best:
        cache["node"] = {"cmd": best.cmd, "version": best.version, "via": best.via}
        _save_cache(cache)
    return best


def tool_typescript() -> Path | None:
    p = NODE_DIR / "node_modules" / "typescript" / "package.json"
    return p if p.is_file() else None


def has_typescript(root: Path) -> bool:
    if tool_typescript():
        return True
    for d in (root, *root.parents):
        p = d / "node_modules" / "typescript" / "package.json"
        if p.is_file():
            try:
                return int(json.loads(p.read_text(encoding="utf-8"))["version"].split(".")[0]) >= 5
            except (OSError, ValueError, KeyError):
                return False
    return False


# ------------------------------------------------------------------ engines

class NodeEngine:
    name = "node"

    def __init__(self, runtime: NodeRuntime):
        self.runtime = runtime

    def describe(self) -> str:
        return self.runtime.describe()

    def extract(self, root: Path, files: list[str]) -> ExtractResult:
        res = ExtractResult()
        if not files:
            return res
        payload = json.dumps({"root": str(root), "files": files})
        timeout = 60 + 0.2 * len(files)
        try:
            proc = subprocess.run(
                [*self.runtime.cmd, str(EXTRACT)], input=payload, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW,
            )
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err, code = "timeout", -1
        except OSError as e:
            out, err, code = "", str(e), -1
        for i, line in enumerate(out.splitlines()):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if i == 0 and "engine" in obj:
                res.header = obj["engine"]
                continue
            if "path" not in obj:
                continue
            if "error" in obj:
                res.failed.append(obj["path"])
            else:
                res.records[obj["path"]] = obj
        missing = [f for f in files if f not in res.records and f not in res.failed]
        res.failed.extend(missing)
        if code != 0 or missing:
            msg = (err or "").strip().splitlines()[-1:] or [f"exit {code}"]
            res.warning = f"node engine: {len(missing)} files not parsed ({msg[0][:200]})"
        return res


class PythonEngine:
    name = "python"

    def describe(self) -> str:
        from nimblegraph.parse import ts as pyparse
        return f"python ({pyparse.BACKEND})"

    def extract(self, root: Path, files: list[str]) -> ExtractResult:
        from nimblegraph.parse import ts as pyparse
        res = ExtractResult(header={"python": sys.version.split()[0], "backend": pyparse.BACKEND})
        for rel in files:
            try:
                res.records[rel] = pyparse.extract_path(root, rel)
            except Exception as e:  # noqa: BLE001 - any parser bug must not break the build
                from nimblegraph.parse import fallback
                try:
                    res.records[rel] = fallback.extract_path(root, rel)
                except Exception:  # noqa: BLE001
                    res.failed.append(rel)
                res.warning = f"python parser fell back to regex on {rel}: {e}"
        return res


def select(root: Path, prefer: str = "auto") -> tuple[NodeEngine | PythonEngine, str | None]:
    """Returns (engine, warning)."""
    if prefer == "python":
        return PythonEngine(), None
    rt = find_node()
    if rt is None:
        warn = "no usable Node found (PATH or fnm); using the Python parser"
        return PythonEngine(), warn
    if not has_typescript(root):
        return PythonEngine(), "typescript not installed for the Node engine (run `nimblegraph setup`); using the Python parser"
    warn = None
    lts = latest_lts(offline=True)
    if rt.major < major_of(lts["version"]):
        warn = f"using {rt.describe()}, not the latest LTS ({lts['version']}); run `nimblegraph setup`"
    return NodeEngine(rt), warn
