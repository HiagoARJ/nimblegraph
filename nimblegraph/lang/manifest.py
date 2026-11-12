"""Package manifests of the non-JS ecosystems: package name and dependency names (with versions when cheap),
read with regexes/tomllib so nothing has to be installed."""

from __future__ import annotations

import posixpath
import re
import tomllib
from pathlib import Path


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _toml(p: Path) -> dict:
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


RE_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([<>=!~^][^;#]*)?", re.M)


def _pep508(items) -> dict[str, str]:
    out = {}
    for it in items or []:
        m = RE_REQ.match(str(it))
        if m:
            out[m.group(1).lower().replace("_", "-")] = (m.group(2) or "").strip()
    return out


def read(root: Path, d: str, names: list[str]) -> dict:
    """{'name', 'ecosystem', 'deps': {name: version}, 'module'?: go module path / php psr-4 map}."""
    base = root / d if d else root
    out: dict = {"name": "", "ecosystem": "", "deps": {}}
    for fn in sorted(names):
        p = base / fn
        if fn == "go.mod":
            t = _read(p)
            m = re.search(r"^module\s+(\S+)", t, re.M)
            out.update(ecosystem="go", name=m.group(1) if m else out["name"])
            out["module"] = m.group(1) if m else ""
            for mm in re.finditer(r"^\s*(?:require\s+)?([\w.-]+\.[\w./-]+)\s+(v[\w.+-]+)", t, re.M):
                out["deps"][mm.group(1)] = mm.group(2)
        elif fn == "pyproject.toml":
            data = _toml(p)
            proj = data.get("project") or {}
            poetry = (data.get("tool") or {}).get("poetry") or {}
            out.update(ecosystem="python", name=proj.get("name") or poetry.get("name") or out["name"])
            out["deps"].update(_pep508(proj.get("dependencies")))
            for k, v in (poetry.get("dependencies") or {}).items():
                if k.lower() != "python":
                    out["deps"][k.lower()] = str(v) if isinstance(v, str) else ""
        elif fn in ("requirements.txt", "Pipfile", "setup.py", "setup.cfg"):
            out["ecosystem"] = out["ecosystem"] or "python"
            t = _read(p)
            if fn == "requirements.txt":
                out["deps"].update(_pep508(ln for ln in t.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-"))))
            elif fn == "Pipfile":
                sec = re.search(r"^\[packages\]([\s\S]*?)(^\[|\Z)", t, re.M)
                for mm in re.finditer(r'^\s*"?([\w.-]+)"?\s*=', sec.group(1) if sec else "", re.M):
                    out["deps"][mm.group(1).lower()] = ""
            else:
                m = re.search(r"""name\s*=\s*['"]?([\w.-]+)""", t)
                out["name"] = out["name"] or (m.group(1) if m else "")
                for mm in re.finditer(r"""['"]([A-Za-z][\w.-]*)\s*(?:[<>=~!][^'"]*)?['"]""",
                                      (re.search(r"install_requires\s*=\s*\[([^\]]*)\]", t) or re.search(r"()", "")).group(1)):
                    out["deps"][mm.group(1).lower()] = ""
        elif fn == "pom.xml":
            t = _read(p)
            t_nodeps = re.sub(r"<(dependencies|parent|build|dependencyManagement)>[\s\S]*?</\1>", "", t)
            m = re.search(r"<artifactId>([^<]+)</artifactId>", t_nodeps)
            out.update(ecosystem="maven", name=m.group(1).strip() if m else out["name"])
            for dep in re.finditer(r"<dependency>([\s\S]*?)</dependency>", t):
                g = re.search(r"<groupId>([^<]+)</groupId>", dep.group(1))
                a = re.search(r"<artifactId>([^<]+)</artifactId>", dep.group(1))
                v = re.search(r"<version>([^<]+)</version>", dep.group(1))
                if a:
                    out["deps"][f"{g.group(1).strip() + ':' if g else ''}{a.group(1).strip()}"] = v.group(1).strip() if v else ""
            par = re.search(r"<parent>([\s\S]*?)</parent>", t)
            if par:
                a = re.search(r"<artifactId>([^<]+)</artifactId>", par.group(1))
                g = re.search(r"<groupId>([^<]+)</groupId>", par.group(1))
                if a:
                    out["deps"][f"{g.group(1).strip() + ':' if g else ''}{a.group(1).strip()}"] = ""
        elif fn in ("build.gradle", "build.gradle.kts"):
            t = _read(p)
            out["ecosystem"] = out["ecosystem"] or "gradle"
            for mm in re.finditer(r"""(?:implementation|api|compileOnly|runtimeOnly|testImplementation|kapt|ksp)\s*\(?\s*['"]([\w.-]+):([\w.-]+)(?::([\w.+-]+))?['"]""", t):
                out["deps"][f"{mm.group(1)}:{mm.group(2)}"] = mm.group(3) or ""
            for mm in re.finditer(r"""id\s*\(?\s*['"]([\w.-]+)['"]""", t):
                out["deps"][mm.group(1)] = ""
            out["name"] = out["name"] or posixpath.basename(d)
        elif fn.endswith(".csproj"):
            t = _read(p)
            out.update(ecosystem="nuget", name=out["name"] or fn[:-7])
            sdk = re.search(r'<Project\s+Sdk="([^"]+)"', t)
            if sdk:
                out["deps"][sdk.group(1)] = ""
            for mm in re.finditer(r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]*)")?', t):
                out["deps"][mm.group(1)] = mm.group(2) or ""
            for mm in re.finditer(r'<ProjectReference\s+Include="([^"]+)"', t):
                out.setdefault("project_refs", []).append(mm.group(1).replace("\\", "/"))
        elif fn == "composer.json":
            import json
            try:
                data = json.loads(_read(p) or "{}")
            except ValueError:
                data = {}
            out.update(ecosystem="composer", name=data.get("name") or out["name"])
            for key in ("require", "require-dev"):
                for k, v in (data.get(key) or {}).items():
                    out["deps"][k] = str(v)
            psr4 = {}
            for key in ("autoload", "autoload-dev"):
                for ns, dirs in ((data.get(key) or {}).get("psr-4") or {}).items():
                    for dd in dirs if isinstance(dirs, list) else [dirs]:
                        psr4.setdefault(ns, []).append(posixpath.normpath(posixpath.join(d, dd)) if d else posixpath.normpath(dd))
            out["psr4"] = psr4
        elif fn == "Gemfile" or fn.endswith(".gemspec"):
            t = _read(p)
            out["ecosystem"] = "rubygems"
            for mm in re.finditer(r"""^\s*(?:gem|\w+\.add_(?:runtime_|development_)?dependency)\s*\(?\s*['"]([\w.-]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""", t, re.M):
                out["deps"][mm.group(1)] = mm.group(2) or ""
            if fn.endswith(".gemspec"):
                out["name"] = fn[:-8]
        elif fn == "Cargo.toml":
            data = _toml(p)
            out.update(ecosystem="cargo", name=(data.get("package") or {}).get("name") or out["name"])
            for key in ("dependencies", "dev-dependencies", "build-dependencies"):
                for k, v in (data.get(key) or {}).items():
                    out["deps"][k] = v if isinstance(v, str) else str((v or {}).get("version", "")) if isinstance(v, dict) else ""
            ws = (data.get("workspace") or {}).get("dependencies") or {}
            for k, v in ws.items():
                out["deps"].setdefault(k, v if isinstance(v, str) else "")
        elif fn == "Package.swift":
            t = _read(p)
            m = re.search(r"""name:\s*"([^"]+)\"""", t)
            out.update(ecosystem="swiftpm", name=m.group(1) if m else out["name"])
            for mm in re.finditer(r"""\.package\(\s*(?:name:\s*"[^"]*",\s*)?url:\s*"([^"]+)\"""", t):
                out["deps"][mm.group(1).rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]] = ""
        elif fn in ("CMakeLists.txt", "meson.build"):
            t = _read(p)
            m = re.search(r"project\s*\(\s*['\"]?([\w.-]+)", t, re.I)
            out.update(ecosystem=out["ecosystem"] or "cmake", name=out["name"] or (m.group(1) if m else ""))
            for mm in re.finditer(r"find_package\s*\(\s*([\w.-]+)", t, re.I):
                out["deps"][mm.group(1)] = ""
    if not out["name"]:
        out["name"] = posixpath.basename(d) if d else root.name
    return out
