"""Import and reference resolution for the non-JS languages.

- File imports (Python modules, Rust `mod`/`use`, C/C++ `#include`, PHP/Ruby `require`) resolve to files.
- Package/namespace imports (Go, Java/Kotlin, C#, PHP `use`, Swift, Ruby constants) resolve through a global
  declaration index `(family, scope, name) -> files`: the identifiers a file references become edges to the
  files that declare them, looking in the explicit imports, the file's own package/namespace and the imported
  ones. Each resolved name is also a local target, so symbol references and inheritance work as for JS."""

from __future__ import annotations

import posixpath
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from nimblegraph import lang as langs

FAMILY = {"java": "jvm", "kotlin": "jvm"}
# the C library and POSIX headers some projects include with quotes ("stdio.h")
C_SYSTEM_HEADERS = {
    "assert", "ctype", "errno", "fenv", "float", "inttypes", "iso646", "limits", "locale", "math", "setjmp", "signal",
    "stdalign", "stdarg", "stdatomic", "stdbool", "stddef", "stdint", "stdio", "stdlib", "stdnoreturn", "string",
    "tgmath", "threads", "time", "uchar", "wchar", "wctype", "unistd", "fcntl", "pthread", "dirent", "dlfcn", "poll",
    "sched", "semaphore", "strings", "termios", "windows", "winsock2", "malloc", "memory", "io", "direct", "process",
}
PY_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


@dataclass
class FileLinks:
    edges: dict[tuple[str, str], tuple[set[str], int]] = field(default_factory=dict)  # (dst, kind) -> (names, line)
    externals: dict[str, int] = field(default_factory=dict)  # pkg -> first line
    unresolved: list[tuple[str, int, str]] = field(default_factory=list)  # (spec, line, kind)
    locals: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (file, symbol)
    specs: dict[str, str] = field(default_factory=dict)  # import spec -> file

    def edge(self, dst: str, names, line: int, src: str, kind: str = "import") -> None:
        if dst == src:
            return
        cur = self.edges.get((dst, kind))
        if cur is None:
            self.edges[(dst, kind)] = (set(names), line)
        else:
            cur[0].update(names)
            self.edges[(dst, kind)] = (cur[0], min(cur[1], line) if line else cur[1])

    def ext(self, pkg: str, line: int) -> None:
        if pkg:
            self.externals[pkg] = min(self.externals.get(pkg, line), line)


def family(rec: dict) -> str:
    lg = rec.get("lang", "")
    return FAMILY.get(lg, lg)


class LangResolver:
    def __init__(self, root, files: set[str], parsed: dict[str, dict], manifests: dict[str, dict]):
        self.root = root
        self.files = files
        self.parsed = parsed
        self.manifests = manifests  # dir -> manifest.read() of the non-JS manifests
        self.decl: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        self.scopes: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.global_usings: dict[str, set[str]] = defaultdict(set)  # family -> namespaces (C# global using)
        for f in sorted(files):
            rec = parsed.get(f)
            if not rec or rec.get("lang") in (None, "", "javascript"):
                continue
            fam = family(rec)
            sc = self.scope_of(f, rec)
            self.scopes[(fam, sc)].append(f)
            for s in rec.get("symbols", []):
                self.decl[(fam, sc, s["name"])].append(f)
                if s.get("qualified") and s["qualified"] != s["name"]:  # Ruby Admin::UsersController
                    self.decl[(fam, sc, s["qualified"])].append(f)
            for imp in rec.get("imports", []):
                if imp.get("kind") == "global":
                    self.global_usings[fam].add(imp["spec"])
        # every namespace and its parents (using Acme.Users is valid when only Acme.Users.Services is declared)
        self.ns_prefixes: set[tuple[str, str]] = set()
        for fam, sc in self.scopes:
            parts = sc.split(".")
            for i in range(1, len(parts) + 1):
                self.ns_prefixes.add((fam, ".".join(parts[:i])))
        self.go_modules = sorted(((m.get("module") or "", d) for d, m in manifests.items() if m.get("module")),
                                 key=lambda x: -len(x[0]))
        self._py_index: dict[str, list[str]] | None = None
        self.psr4 = sorted(((ns.rstrip("\\"), dd) for m in manifests.values() for ns, dirs in (m.get("psr4") or {}).items()
                            for dd in dirs), key=lambda x: -len(x[0]))
        self._rust: dict | None = None
        self._c: dict | None = None

    # ------------------------------------------------------------ scopes
    def scope_of(self, f: str, rec: dict) -> str:
        lg = rec.get("lang")
        if lg == "go":
            return posixpath.dirname(f)
        if lg in ("java", "kotlin", "csharp", "php"):
            return rec.get("package") or ""
        if lg == "swift":
            parts = f.split("/")
            for i in range(len(parts) - 2, -1, -1):
                if parts[i] in ("Sources", "Tests") and i + 1 < len(parts) - 1:
                    return "/".join(parts[:i + 2])
            return ""
        return ""

    def has_scope(self, fam: str, sc: str) -> bool:
        return (fam, sc) in self.scopes

    # ------------------------------------------------------------ entry point
    def links(self, path: str, rec: dict) -> FileLinks:
        out = FileLinks()
        lg = rec.get("lang")
        fn = getattr(self, f"_links_{lg}", None)
        if fn is not None:
            fn(path, rec, out)
        return out

    def _refs(self, path: str, rec: dict, fam: str, visible: list[str], explicit: dict[str, list[str]],
              out: FileLinks, max_targets: int = 3) -> None:
        """Every referenced identifier declared in an explicit import or a visible scope becomes an edge."""
        own = {s["name"] for s in rec.get("symbols", [])}
        for r in rec.get("refs", []):
            if r in own:
                continue
            dsts = explicit.get(r)
            if not dsts:
                for sc in visible:
                    hit = self.decl.get((fam, sc, r))
                    if hit:
                        dsts = [f for f in hit if f != path]
                        if dsts:
                            break
            if not dsts:
                continue
            for d in dsts[:max_targets]:
                out.edge(d, [r], 0, path)
            out.locals.setdefault(r, (dsts[0], r))

    # ------------------------------------------------------------ JVM (Java, Kotlin)
    def _links_java(self, path: str, rec: dict, out: FileLinks) -> None:
        fam = family(rec)
        own = rec.get("package") or ""
        visible, explicit = [own], {}
        for imp in rec.get("imports", []):
            spec, line = imp["spec"], imp.get("line", 0)
            if spec.endswith(".*"):
                sc = spec[:-2]
                if self.has_scope(fam, sc):
                    visible.append(sc)
                else:  # static on-demand import of a class, or an external package
                    owner, _, cname = sc.rpartition(".")
                    hit = self.decl.get((fam, owner, cname))
                    if hit:
                        out.edge(hit[0], [cname], line, path)
                    else:
                        out.ext(_jvm_pkg(sc), line)
                continue
            scope, _, name = spec.rpartition(".")
            hit = self.decl.get((fam, scope, name))
            if not hit:  # nested class import a.b.Outer.Inner
                s2, _, outer = scope.rpartition(".")
                hit = self.decl.get((fam, s2, outer))
            if hit:
                locals_ = [n[1] for n in imp.get("names") or []] or [name]
                for n in locals_:
                    explicit[n] = hit
                    out.locals[n] = (hit[0], n if imp["kind"] == "static" else name)
                out.edge(hit[0], [n[0] for n in imp.get("names") or []] or [name], line, path)
                out.specs[spec] = hit[0]
            elif self.has_scope(fam, scope):  # a project package without that class
                out.unresolved.append((spec, line, imp["kind"]))
            else:
                out.ext(_jvm_pkg(spec), line)
        if own:
            visible += [own]
        self._refs(path, rec, fam, visible, explicit, out)

    _links_kotlin = _links_java

    # ------------------------------------------------------------ C#
    def _links_csharp(self, path: str, rec: dict, out: FileLinks) -> None:
        fam = "csharp"
        own = rec.get("package") or ""
        visible = []
        parts = own.split(".") if own else []
        for i in range(len(parts), 0, -1):
            visible.append(".".join(parts[:i]))
        visible.append("")
        explicit: dict[str, list[str]] = {}
        usings = [(imp["spec"], imp.get("line", 0), imp) for imp in rec.get("imports", [])]
        own_specs = {u[0] for u in usings}
        usings += [(ns, 0, None) for ns in sorted(self.global_usings.get(fam, ())) if ns not in own_specs]
        for spec, line, imp in usings:
            if imp is not None and imp.get("names"):  # using Alias = A.B.Type;
                scope, _, name = spec.rpartition(".")
                hit = self.decl.get((fam, scope, name))
                if hit:
                    for _imported, local in imp["names"]:
                        explicit[local] = hit
                        out.locals[local] = (hit[0], name)
                    out.edge(hit[0], [name], line, path)
                elif not self.has_scope(fam, scope):
                    out.ext(_dotted_pkg(spec), line)
                continue
            if (fam, spec) in self.ns_prefixes:
                visible.append(spec)
            elif imp is not None:
                out.ext(_dotted_pkg(spec), line)
        self._refs(path, rec, fam, visible, explicit, out)

    # ------------------------------------------------------------ Go
    def _go_dir(self, spec: str) -> str | None:
        for mod, d in self.go_modules:
            if spec == mod or spec.startswith(mod + "/"):
                rest = spec[len(mod):].lstrip("/")
                target = posixpath.join(d, rest) if d else rest
                return target if self.has_scope("go", target) else None
        return None

    def _links_go(self, path: str, rec: dict, out: FileLinks) -> None:
        own = posixpath.dirname(path)
        alias_dirs: dict[str, str] = {}
        imported_dirs: list[tuple[str, str, int]] = []
        for imp in rec.get("imports", []):
            spec, line = imp["spec"], imp.get("line", 0)
            d = self._go_dir(spec)
            local = (imp.get("names") or [["*", spec.rsplit("/", 1)[-1]]])[0][1]
            if d is not None:
                imported_dirs.append((d, local, line))
                if local == ".":
                    continue
                alias_dirs[local] = d
                out.specs[spec] = d
            elif any(spec == m or spec.startswith(m + "/") for m, _ in self.go_modules):
                out.unresolved.append((spec, line, "import"))
            else:
                out.ext(_go_pkg(spec), line)
        seen_dirs: set[str] = set()
        for r in rec.get("refs", []):
            if "." in r:
                alias, _, name = r.partition(".")
                d = alias_dirs.get(alias)
                if d is None:
                    continue
                hit = self.decl.get(("go", d, name))
                if hit:
                    out.edge(hit[0], [name], 0, path)
                    out.locals.setdefault(r, (hit[0], name))
                    seen_dirs.add(d)
            else:
                hit = self.decl.get(("go", own, r)) or next(
                    (self.decl.get(("go", d, r)) for d, local, _ in imported_dirs if local == "." and
                     self.decl.get(("go", d, r))), None)
                if hit:
                    dst = next((f for f in hit if f != path), None)
                    if dst:
                        out.edge(dst, [r], 0, path)
                        out.locals.setdefault(r, (dst, r))
        for d, local, line in imported_dirs:  # an import whose names we could not see still is a dependency
            if d not in seen_dirs:
                fs = [f for f in self.scopes.get(("go", d), []) if not f.endswith("_test.go")]
                if fs:
                    out.edge(fs[0], [], line, path)
        for key, (names, ln) in list(out.edges.items()):
            if not ln:
                first = next((line for d, _l, line in imported_dirs if key[0].startswith(d + "/")), 0)
                out.edges[key] = (names, first)

    # ------------------------------------------------------------ Python
    def _py_idx(self) -> dict[str, list[str]]:
        if self._py_index is None:
            idx: dict[str, list[str]] = defaultdict(list)
            for f in sorted(self.files):
                if not f.endswith((".py", ".pyi")):
                    continue
                parts = f.rsplit(".", 1)[0].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                for i in range(len(parts)):
                    idx[".".join(parts[i:])].append(f)
            self._py_index = idx
        return self._py_index

    def _py_file(self, base: str) -> str | None:
        for cand in (base + ".py", base + "/__init__.py", base + ".pyi", base + "/__init__.pyi"):
            if cand in self.files:
                return cand
        return None

    def _py_abs(self, dotted: str, importer: str) -> str | None:
        hits = self._py_idx().get(dotted)
        if not hits:
            return None
        if len(hits) == 1:
            return hits[0]
        # several `utils.py`: the one closest to the importer, then the shortest path
        return max(hits, key=lambda h: (_common_prefix(h, importer), -len(h)))

    def _links_python(self, path: str, rec: dict, out: FileLinks) -> None:
        pkg_dir = posixpath.dirname(path)
        for imp in rec.get("imports", []):
            spec, line, names = imp["spec"], imp.get("line", 0), imp.get("names") or []
            if spec.startswith("."):
                level = len(spec) - len(spec.lstrip("."))
                base = pkg_dir
                for _ in range(level - 1):
                    base = posixpath.dirname(base)
                rest = spec.lstrip(".")
                mod_base = posixpath.join(base, rest.replace(".", "/")) if rest else base
                target = self._py_file(mod_base) if mod_base else None
                self._py_names(path, spec, names, mod_base, target, line, out, relative=True)
                continue
            if imp["kind"] == "import":
                target = self._py_abs(spec, path)
                local = names[0][1] if names else spec.split(".")[0]
                if target:
                    out.edge(target, [], line, path)
                    out.locals[local] = (target, "*")
                    out.specs[spec] = target
                else:
                    out.ext(spec.split(".")[0], line)
                continue
            target = self._py_abs(spec, path)
            if target is None and not any(self._py_abs(f"{spec}.{n[0]}", path) for n in names if n[0] != "*"):
                out.ext(spec.split(".")[0], line)
                continue
            mod_base = target.rsplit(".", 1)[0].removesuffix("/__init__") if target else None
            self._py_names(path, spec, names, mod_base, target, line, out, relative=False)

    def _py_names(self, path, spec, names, mod_base, target, line, out: FileLinks, relative: bool) -> None:
        found = False
        for imported, local in names:
            sub = None
            if imported != "*":
                sub = self._py_file(posixpath.join(mod_base, imported)) if mod_base is not None else None
                if sub is None and not relative:
                    sub = self._py_abs(f"{spec}.{imported}", path)
            if sub:
                out.edge(sub, [], line, path)
                out.locals[local] = (sub, "*")
                found = True
            elif target:
                out.edge(target, [imported], line, path)
                if imported != "*":
                    out.locals[local] = (target, imported)
                found = True
        if not names and target:
            out.edge(target, [], line, path)
            found = True
        if target:
            out.specs[spec] = target
        if not found:
            if relative:
                out.unresolved.append((spec, line, "from"))
            else:
                out.ext(spec.split(".")[0], line)


def _links_php(self: LangResolver, path: str, rec: dict, out: FileLinks) -> None:
    fam, own = "php", rec.get("package") or ""
    explicit: dict[str, list[str]] = {}
    for imp in rec.get("imports", []):
        spec, line = imp["spec"], imp.get("line", 0)
        if imp["kind"] == "require":
            rel = spec.lstrip("/")
            cands = [posixpath.normpath(posixpath.join(posixpath.dirname(path), rel)), posixpath.normpath(rel)]
            hit = next((c for c in cands if c in self.files), None)
            if hit:
                out.edge(hit, [], line, path)
                out.specs[spec] = hit
            elif "vendor/" in spec:  # composer's autoloader
                out.ext("composer", line)
            elif spec.endswith(".php"):
                out.unresolved.append((spec, line, "require"))
            continue
        scope, _, name = spec.rpartition("\\")
        hit = self.decl.get((fam, scope, name)) or self._psr4_file(spec)
        if hit:
            for _imported, local in imp.get("names") or [[name, name]]:
                explicit[local] = hit
                out.locals[local] = (hit[0], name)
            out.edge(hit[0], [name], line, path)
            out.specs[spec] = hit[0]
        elif "\\" not in spec:  # use Exception; / use Closure; / facade aliases (Auth, Schema): global classes
            out.ext(spec, line)
        elif any(spec == ns or spec.startswith(ns + "\\") for ns, _d in self.psr4) or self.has_scope(fam, scope):
            out.unresolved.append((spec, line, "use"))
        else:
            out.ext(spec.split("\\")[0], line)
    for r in rec.get("refs", []):  # \App\Models\User or Models\User (relative to the own namespace)
        if "\\" not in r:
            continue
        scope, _, name = r.rpartition("\\")
        hit = self.decl.get((fam, scope, name)) or (self.decl.get((fam, f"{own}\\{scope}", name)) if own else None)
        if hit and hit[0] != path:
            out.edge(hit[0], [name], 0, path)
            out.locals.setdefault(r, (hit[0], name))
    self._refs(path, rec, fam, [own, ""], explicit, out)


def _psr4_file(self: LangResolver, spec: str) -> list[str] | None:
    for ns, d in self.psr4:
        if spec.startswith(ns + "\\"):
            f = posixpath.join(d, spec[len(ns) + 1:].replace("\\", "/") + ".php") if d not in ("", ".") \
                else spec[len(ns) + 1:].replace("\\", "/") + ".php"
            if f in self.files:
                return [f]
    return None


def _links_ruby(self: LangResolver, path: str, rec: dict, out: FileLinks) -> None:
    here = posixpath.dirname(path)
    for imp in rec.get("imports", []):
        spec, line = imp["spec"], imp.get("line", 0)
        base = spec if spec.endswith(".rb") else spec + ".rb"
        if imp["kind"] == "require_relative":
            cands = [posixpath.normpath(posixpath.join(here, base))]
        else:
            cands = [posixpath.normpath(base), f"lib/{base}", f"app/{base}", f"spec/{base}", f"test/{base}"]
            cands += [posixpath.join(d, "lib", base) for d in self.manifests if d]
        hit = next((c for c in cands if c in self.files), None)
        if hit:
            out.edge(hit, [], line, path)
            out.specs[spec] = hit
        elif imp["kind"] == "require_relative":
            out.unresolved.append((spec, line, "require_relative"))
        else:
            out.ext(spec.split("/")[0], line)
    # Rails autoloading: every constant referenced resolves by its (qualified) name
    self._refs(path, rec, "ruby", [""], {}, out)


def _rust_index(self: LangResolver) -> dict:
    """crate src dirs, module path -> file, and crate names (Cargo `name` with - -> _)."""
    if self._rust is None:
        crates: list[tuple[str, str]] = []  # (src dir, crate name)
        for d, m in self.manifests.items():
            if m.get("ecosystem") == "cargo":
                src = posixpath.join(d, "src") if d else "src"
                crates.append((src, (m.get("name") or posixpath.basename(d) or "crate").replace("-", "_")))
        if not crates:
            crates = [("src", "crate")] if any(f.startswith("src/") and f.endswith(".rs") for f in self.files) else [("", "crate")]
        crates.sort(key=lambda c: -len(c[0]))
        mods: dict[tuple[str, tuple], str] = {}
        for f in sorted(self.files):
            if not f.endswith(".rs"):
                continue
            src = next((s for s, _n in crates if s == "" or f.startswith(s + "/")), None)
            if src is None:
                continue
            segs = (f[len(src) + 1:] if src else f)[:-3].split("/")
            if segs[-1] == "mod":
                segs = segs[:-1]
            elif len(segs) == 1 and segs[0] in ("lib", "main"):
                segs = []
            mods.setdefault((src, tuple(segs)), f)
        self._rust = {"crates": crates, "mods": mods, "names": {n: s for s, n in crates}}
    return self._rust


def _rust_modpath(self: LangResolver, f: str) -> tuple[str, list[str], bool]:
    """(crate src, module path, is a mod root: lib.rs/main.rs/mod.rs)."""
    idx = _rust_index(self)
    src = next((s for s, _n in idx["crates"] if s == "" or f.startswith(s + "/")), "")
    segs = (f[len(src) + 1:] if src else f)[:-3].split("/")
    root = segs[-1] == "mod" or (len(segs) == 1 and segs[0] in ("lib", "main"))
    if segs[-1] == "mod":
        segs = segs[:-1]
    elif len(segs) == 1 and segs[0] in ("lib", "main"):
        segs = []
    return src, segs, root


def _rust_find(self: LangResolver, src: str, segs: list[str]) -> tuple[str | None, list[str]]:
    """Longest module prefix of segs that is a file, and the item names left over."""
    mods = _rust_index(self)["mods"]
    for i in range(len(segs), -1, -1):
        hit = mods.get((src, tuple(segs[:i])))
        if hit:
            return hit, segs[i:]
    return None, segs


def _rust_path(self: LangResolver, path: str, segs: list[str]) -> tuple[str | None, list[str], str | None]:
    """A use/ref path -> (module file, leftover items, external crate)."""
    src, here, _root = _rust_modpath(self, path)
    idx = _rust_index(self)
    if not segs:
        return None, [], None
    head = segs[0]
    if head == "crate":
        base, rest = [], segs[1:]
    elif head in ("self", "super"):
        base, rest = list(here), list(segs)
        while rest and rest[0] in ("self", "super"):
            if rest.pop(0) == "super" and base:
                base.pop()
    elif (src, (*here, head)) in idx["mods"]:  # a child module declared in this file
        base, rest = list(here), segs
    elif (src, (head,)) in idx["mods"] and not here:
        base, rest = [], segs
    elif head in idx["names"] and idx["names"][head] != src:  # another crate of the workspace
        src, base, rest = idx["names"][head], [], segs[1:]
    else:
        return None, [], head
    f, left = _rust_find(self, src, [*base, *rest])
    return f, left, None


def _links_rust(self: LangResolver, path: str, rec: dict, out: FileLinks) -> None:
    src, here, root = _rust_modpath(self, path)
    mods = _rust_index(self)["mods"]
    for imp in rec.get("imports", []):
        spec, line = imp["spec"], imp.get("line", 0)
        if imp["kind"] == "mod":
            child = mods.get((src, (*here, spec))) if root or not here else mods.get((src, (*here, spec)))
            if child:
                out.edge(child, [], line, path)
                out.locals[spec] = (child, "*")
            else:
                out.unresolved.append((spec, line, "mod"))
            continue
        segs = spec.split("::")
        f, left, ext = _rust_path(self, path, segs)
        if ext is not None:
            out.ext("std" if ext in ("std", "core", "alloc") else ext, line)
            continue
        if f is None:
            out.unresolved.append((spec, line, "use"))
            continue
        items = [x for x in left if x != "*"]
        out.edge(f, items[:1], line, path)
        out.specs[spec] = f
        for imported, local in imp.get("names") or []:
            if imported != "*":
                out.locals[local] = (f, items[0] if items else "*")
    for r in rec.get("refs", []):  # handlers::show, crate::db::Pool
        if "::" not in r:
            continue
        f, left, ext = _rust_path(self, path, r.split("::"))
        if f and f != path:
            out.edge(f, left[:1], 0, path)
            out.locals.setdefault(r, (f, left[0] if left else "*"))
    self._refs(path, rec, "rust", [], {}, out)


def _c_index(self: LangResolver) -> dict:
    """Include dirs (compile_commands.json -I, CMake include_directories, include/ src/) and headers by name."""
    if getattr(self, "_c", None) is None:
        import json
        import re
        from pathlib import Path
        dirs: list[str] = []
        root = self.root
        for cc in (root / "compile_commands.json", root / "build" / "compile_commands.json"):
            try:
                entries = json.loads(cc.read_text(encoding="utf-8", errors="replace")) if cc.is_file() else []
            except (OSError, ValueError):
                entries = []
            for e in entries[:2000]:
                cmd = e.get("command") or " ".join(e.get("arguments") or [])
                for m in re.finditer(r"(?:-I|-isystem\s*)\s*(\S+)", cmd):
                    p = m.group(1).strip('"')
                    try:
                        rel = posixpath.normpath(Path(p).resolve().relative_to(root.resolve()).as_posix())
                    except (ValueError, OSError):
                        continue
                    if rel not in dirs:
                        dirs.append(rel)
        for d, m in self.manifests.items():
            if m.get("ecosystem") != "cmake":
                continue
            try:
                t = (root / d / "CMakeLists.txt").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for blk in re.findall(r"include_directories\s*\(([^)]*)\)", t, re.I):
                for tok in blk.split():
                    if tok.upper() in ("PUBLIC", "PRIVATE", "INTERFACE", "SYSTEM", "BEFORE", "AFTER") or "$" in tok:
                        continue
                    rel = posixpath.normpath(posixpath.join(d, tok.strip('"'))) if d else posixpath.normpath(tok.strip('"'))
                    if rel not in dirs:
                        dirs.append(rel)
        for d in ("include", "src", "inc", ""):
            if d not in dirs:
                dirs.append(d)
        by_base: dict[str, list[str]] = defaultdict(list)
        for f in self.files:
            if langs.name_of(f) in ("c", "cpp"):
                by_base[posixpath.basename(f)].append(f)
        self._c = {"dirs": dirs, "by_base": by_base}
    return self._c


def _c_include(self: LangResolver, path: str, raw: str) -> str | None:
    """"x.h" next to the file first; then the project headers ending with the include path, preferring one in an
    include dir (-I, include_directories), then the one nearest to the includer."""
    idx = _c_index(self)
    here = posixpath.dirname(path)
    key = (here, raw)
    cache = idx.setdefault("hits", {})
    if key in cache:
        return cache[key]
    name = raw.strip('"<> ')
    hit = None
    if raw.startswith('"'):
        local = posixpath.normpath(posixpath.join(here, name))
        hit = local if local in self.files else None
    if hit is None:
        norm = posixpath.normpath(name) if ".." in name or "./" in name else name
        if norm.startswith("../"):  # "../src/world.hpp" that did not exist next to the file
            norm = norm.lstrip("./")
        pool = [f for f in idx["by_base"].get(posixpath.basename(norm), []) if f == norm or f.endswith("/" + norm)]
        if pool:
            dirs = idx.setdefault("dirset", set(idx["dirs"]))
            in_dirs = [f for f in pool if f[: len(f) - len(norm)].rstrip("/") in dirs]
            hit = max(in_dirs or pool, key=lambda f: (_common_prefix(f, path), -len(f)))
    cache[key] = hit
    return hit


def _c_includes(self: LangResolver, path: str) -> list[str]:
    cache = _c_index(self).setdefault("includes", {})
    if path not in cache:
        rec = self.parsed.get(path) or {}
        cache[path] = [h for h in (_c_include(self, path, imp["spec"]) for imp in rec.get("imports", [])) if h]
    return cache[path]


def _links_c(self: LangResolver, path: str, rec: dict, out: FileLinks) -> None:
    for imp in rec.get("imports", []):
        raw, line = imp["spec"], imp.get("line", 0)
        name = raw.strip('"<> ')
        hit = _c_include(self, path, raw)
        if hit:
            out.edge(hit, [], line, path)
            out.specs[raw] = hit
        elif raw.startswith('"') and name.rsplit(".", 1)[0] not in C_SYSTEM_HEADERS and not name.startswith("sys/"):
            out.unresolved.append((name, line, "include"))
        else:
            top = name.split("/", 1)[0]
            out.ext(top.rsplit(".", 1)[0] if "/" not in name else top, line)
    # a header leads to the source file that implements it (same stem, includes it): includer > x.h > x.c
    if path.endswith((".h", ".hh", ".hpp", ".hxx")):
        stem = posixpath.basename(path).rsplit(".", 1)[0]
        idx = _c_index(self)
        for ext in (".c", ".cc", ".cpp", ".cxx", ".c++"):
            for cand in idx["by_base"].get(stem + ext, []):
                if path in _c_includes(self, cand):
                    out.edge(cand, [], 0, path, "implements")  # not an import: no x.c <-> x.h cycle
    # names declared in the headers this file sees (two include levels) resolve as local targets
    seen: list[str] = []
    for h in _c_includes(self, path):
        for g in [h, *_c_includes(self, h)]:
            if g not in seen and g != path:
                seen.append(g)
    declared: dict[str, str] = {}
    for g in seen:
        for s in (self.parsed.get(g) or {}).get("symbols", []):
            declared.setdefault(s["name"], g)
    for r in rec.get("refs", []):
        name = r.rsplit("::", 1)[-1]
        if name in declared:
            out.locals.setdefault(r, (declared[name], name))


def _links_swift(self: LangResolver, path: str, rec: dict, out: FileLinks) -> None:
    own = self.scope_of(path, rec)
    modules = {posixpath.basename(sc): sc for fam, sc in self.scopes if fam == "swift" and sc}
    visible = [own]
    for imp in rec.get("imports", []):
        sc = modules.get(imp["spec"])
        if sc is not None and sc != own:
            visible.append(sc)
            fs = self.scopes.get(("swift", sc), [])
            if fs:
                out.specs[imp["spec"]] = fs[0]
        elif sc is None:
            out.ext(imp["spec"], imp.get("line", 0))
    self._refs(path, rec, "swift", visible, {}, out)


LangResolver._links_c = _links_c
LangResolver._links_cpp = _links_c
LangResolver._links_swift = _links_swift
LangResolver._links_php = _links_php
LangResolver._psr4_file = _psr4_file
LangResolver._links_ruby = _links_ruby
LangResolver._links_rust = _links_rust


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/"), b.split("/")):
        if x != y:
            break
        n += 1
    return n


def _jvm_pkg(spec: str) -> str:
    parts = spec.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else parts[0]


def _dotted_pkg(spec: str) -> str:
    parts = spec.split(".")
    return parts[0] if parts[0] == "System" else ".".join(parts[:2])


def _go_pkg(spec: str) -> str:
    parts = spec.split("/")
    if "." not in parts[0]:
        return spec  # standard library: net/http
    if parts[0] in ("github.com", "gitlab.com", "bitbucket.org") and len(parts) >= 3:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) if parts[0] == "gopkg.in" else "/".join(parts[:3])


def lang_of(rec: dict) -> str:
    return rec.get("lang") or langs.name_of(rec.get("path", ""))
