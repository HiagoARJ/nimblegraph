"""`nimblegraph setup`: latest Node LTS (via fnm), TypeScript for the Node engine, and a `nimblegraph.cmd` shim on PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from nimblegraph import engine

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent / "ui"


def _say(msg: str) -> None:
    print(msg, flush=True)


def ensure_lts() -> engine.NodeRuntime | None:
    lts = engine.latest_lts(refresh=True)
    major = engine.major_of(lts["version"])
    _say(f"latest Node LTS: {lts['version']} {lts.get('codename', '')}".rstrip())
    installed = engine.fnm_installed()
    if not any(engine.major_of(v) == major for v in installed):  # noqa: SLF001
        fnm = engine.find_fnm()
        if fnm:
            _say(f"installing Node {lts['version']} with fnm ...")
            r = subprocess.run([fnm, "install", "--lts"], check=False)
            if r.returncode != 0:
                _say("  fnm install failed; continuing with what is available")
        else:
            _say("  fnm not found: install Node LTS manually or put `node` on PATH")
    rt = engine.find_node(refresh=True)
    if rt:
        _say(f"node engine runtime: {rt.describe()}")
    else:
        _say("no usable Node found; the Python parser will be used")
    return rt


def _npm_cmd(rt: engine.NodeRuntime) -> list[str] | None:
    win = os.name == "nt"
    if rt.via == "fnm exec":
        return [rt.cmd[0], "exec", f"--using={rt.major}", "--", "npm.cmd" if win else "npm"]
    node = Path(rt.cmd[0])
    for cand in (node.parent / ("npm.cmd" if win else "npm"), node.parent / "bin" / "npm"):
        if cand.is_file():
            return [str(cand)]
    found = shutil.which("npm")
    return [found] if found else None


def install_typescript(rt: engine.NodeRuntime) -> bool:
    npm = _npm_cmd(rt)
    if not npm:
        _say("npm not found next to node; skipping typescript install")
        return False
    _say(f"installing typescript in {engine.NODE_DIR} ...")
    env = dict(os.environ)
    env["PATH"] = str(Path(rt.cmd[0]).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run([*npm, "install", "--no-audit", "--no-fund"], cwd=engine.NODE_DIR, env=env, check=False)
    ok = r.returncode == 0 and engine.tool_typescript() is not None
    _say("  typescript ready" if ok else "  npm install failed")
    return ok


def _npm_env(rt: engine.NodeRuntime) -> dict:
    env = dict(os.environ)
    env["PATH"] = str(Path(rt.cmd[0]).parent) + os.pathsep + env.get("PATH", "")
    return env


def build_ui(rt: engine.NodeRuntime | None = None) -> bool:
    """npm install + vite build of nimblegraph/ui (React UI) with the Node LTS runtime."""
    rt = rt or engine.find_node()
    if rt is None:
        _say("ui build: no Node available")
        return False
    npm = _npm_cmd(rt)
    if not npm or not (UI_DIR / "package.json").is_file():
        _say("ui build: npm or ui/package.json not found")
        return False
    env = _npm_env(rt)
    if not (UI_DIR / "node_modules").is_dir():
        _say(f"installing UI dependencies in {UI_DIR} ...")
        if subprocess.run([*npm, "install", "--no-audit", "--no-fund"], cwd=UI_DIR, env=env, check=False).returncode:
            _say("  npm install failed")
            return False
    _say("building the UI ...")
    ok = subprocess.run([*npm, "run", "build"], cwd=UI_DIR, env=env, check=False).returncode == 0
    _say("  ui ready" if ok else "  ui build failed")
    return ok


def start_ui_dev(api_port: int) -> subprocess.Popen | None:
    rt = engine.find_node()
    npm = _npm_cmd(rt) if rt else None
    if not npm:
        _say("ui --dev needs Node/npm")
        return None
    env = _npm_env(rt)
    env["NIMBLEGRAPH_API"] = f"http://127.0.0.1:{api_port}"
    return subprocess.Popen([*npm, "run", "dev", "--", "--port", "5173", "--strictPort"], cwd=UI_DIR, env=env)


SKILL_SRC = Path(__file__).resolve().parent / "skill" / "SKILL.md"


def install_skill() -> Path | None:
    """The /nimblegraph skill for Claude Code: ~/.claude/skills/nimblegraph/SKILL.md."""
    if not SKILL_SRC.is_file():
        return None
    target = Path.home() / ".claude" / "skills" / "nimblegraph" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SKILL_SRC.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    _say(f"Claude Code skill: {target}")
    return target


def shim_dir() -> Path | None:
    """A user-writable directory on PATH where `nimblegraph.cmd` can live."""
    home = Path.home().resolve()
    preferred = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Python" / "bin"
    entries = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    norm = {str(p.resolve()).lower() for p in entries if p.exists()}
    if preferred.is_dir() and str(preferred.resolve()).lower() in norm:
        return preferred
    for p in entries:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp.is_dir() and str(rp).lower().startswith(str(home).lower()) and os.access(rp, os.W_OK) \
                and "windowsapps" not in str(rp).lower():
            return rp
    return None


def install_shim() -> Path | None:
    d = shim_dir()
    if d is None:
        _say("no user-writable folder on PATH found; add the Python Scripts folder to PATH to use `nimblegraph`")
        return None
    if os.name == "nt":
        target = d / "nimblegraph.cmd"
        target.write_text(
            "@echo off\n"
            "setlocal\n"
            f'set "PYTHONPATH={REPO_ROOT};%PYTHONPATH%"\n'
            f'"{sys.executable}" -m nimblegraph %*\n',
            encoding="utf-8",
            newline="\r\n",
        )
        # Git Bash (used by Claude Code on Windows) does not try `.cmd`: give it an extensionless script too
        sh = d / "nimblegraph"
        py = Path(sys.executable).as_posix()
        sh.write_text(
            "#!/bin/sh\n"
            f'PYTHONPATH="{REPO_ROOT.as_posix()};$PYTHONPATH" MSYS_NO_PATHCONV=1 exec "{py}" -m nimblegraph "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        _say(f"command shim: {sh} (Git Bash)")
    else:
        target = d / "nimblegraph"
        target.write_text(f'#!/bin/sh\nPYTHONPATH="{REPO_ROOT}:$PYTHONPATH" exec "{sys.executable}" -m nimblegraph "$@"\n')
        target.chmod(0o755)
    _say(f"command shim: {target}")
    return target


def prefetch_grammars() -> None:
    """Downloads the tree-sitter grammars of the non-JS languages now, so the first build works offline."""
    from nimblegraph import lang as langs
    try:
        import tree_sitter_language_pack as tslp
    except Exception as e:  # noqa: BLE001 - missing wheel or a native library blocked by the OS
        _say(f"tree-sitter not available ({type(e).__name__}); other languages use the regex fallback")
        return
    names = sorted({lg.name for lg in langs.LANGUAGES if lg.module})
    try:
        tslp.download(names)
        _say(f"tree-sitter grammars ready: {', '.join(names)}")
    except Exception as e:  # noqa: BLE001 - offline: they download on first use
        _say(f"tree-sitter grammars not downloaded ({e}); they download on first use")


def run(skip_shim: bool = False) -> int:
    prefetch_grammars()
    rt = ensure_lts()
    if rt:
        if engine.tool_typescript() is None or "--force" in sys.argv:
            install_typescript(rt)
        else:
            _say(f"typescript already installed in {engine.NODE_DIR / 'node_modules'}")
        if (UI_DIR / "src").is_dir():
            build_ui(rt)
        else:  # installed from a wheel/git: ui/dist ships prebuilt
            _say("using the prebuilt UI")
    if not skip_shim:
        install_shim()
    install_skill()
    return 0
