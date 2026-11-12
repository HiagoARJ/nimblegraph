"""Language registry: the files each language owns, the manifests that make a folder a package, the build
output folders skipped at a package root and the test-file pattern. JS/TS is parsed by the engines in
engine.py; every other language by a tree-sitter extractor in nimblegraph/lang/<module>.py."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    label: str
    exts: tuple[str, ...]
    module: str | None = None  # nimblegraph.lang.<module> with extract(); None = JS engines
    manifests: tuple[str, ...] = ()  # file names that make a folder a package of this language
    manifest_exts: tuple[str, ...] = ()  # e.g. .csproj
    build_dirs: tuple[str, ...] = ()  # output folders skipped at a package root (and only there)
    test: str = ""  # regex over the root-relative path (case-sensitive: FooTest.java, not Latest.java)
    symbols: bool = False  # imports name packages/namespaces: file edges come from resolved references


LANGUAGES: tuple[Language, ...] = (
    Language("javascript", "JS/TS", (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"), None,
             ("package.json",), (), ("dist", "build", "out", "coverage")),
    Language("python", "Python", (".py", ".pyi"), "python",
             ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"), (), ("build", "dist"),
             r"(?i:(^|/)(tests?|testing)/)|(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$"),
    Language("go", "Go", (".go",), "go", ("go.mod",), (), ("vendor",), r"_test\.go$", True),
    Language("java", "Java", (".java",), "java", ("pom.xml", "build.gradle", "build.gradle.kts"), (),
             ("target", "build", "out"), r"(^|/)src/(test|it)/|(^|/)\w*(Test|Tests|IT)\.java$", True),
    Language("kotlin", "Kotlin", (".kt",), "kotlin", ("build.gradle.kts", "build.gradle", "pom.xml"), (),
             ("target", "build", "out"), r"(^|/)src/(test|androidTest)/|(^|/)\w*(Test|Tests)\.kt$", True),
    Language("csharp", "C#", (".cs",), "csharp", (), (".csproj",), ("bin", "obj"),
             r"(^|/)[^/]*\.(Tests?|Specs?|UnitTests?|IntegrationTests?)/|(?i:(^|/)tests?/)|\w*Tests?\.cs$", True),
    Language("php", "PHP", (".php",), "php", ("composer.json",), (), ("vendor", "storage"), r"(?i:(^|/)tests?/)|\w*Test\.php$", True),
    Language("ruby", "Ruby", (".rb", ".rake", ".ru"), "ruby", ("Gemfile",), (".gemspec",), ("vendor", "tmp", "log"),
             r"(^|/)(spec|test)/|_(spec|test)\.rb$", True),
    Language("rust", "Rust", (".rs",), "rust", ("Cargo.toml",), (), ("target",), r"(^|/)(tests|benches)/"),
    Language("c", "C", (".c", ".h"), "cfamily", ("CMakeLists.txt", "meson.build"), (), ("build",),
             r"(^|/)tests?/|(^|/)test_[^/]*\.c$|_test\.c$"),
    Language("cpp", "C++", (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".ipp", ".tpp"), "cfamily",
             ("CMakeLists.txt", "meson.build"), (), ("build",), r"(^|/)tests?/|_test\.(cpp|cc|cxx)$|(^|/)test_[^/]*\.(cpp|cc)$"),
    Language("swift", "Swift", (".swift",), "swift", ("Package.swift",), (), (".build",), r"(?i:(^|/)tests/)|\w*Tests?\.swift$", True),
)

BY_NAME = {lg.name: lg for lg in LANGUAGES}
JS = BY_NAME["javascript"]
_EXT: dict[str, Language] = {e: lg for lg in LANGUAGES for e in lg.exts}
ALL_EXTS: tuple[str, ...] = tuple(_EXT)
# "x.service.ts" -> "x.service"; ".d.ts" too
RE_EXT = re.compile(r"(\.d)?(" + "|".join(re.escape(e) for e in sorted(ALL_EXTS, key=len, reverse=True)) + r")$",
                    re.I)
MANIFESTS: dict[str, tuple[Language, ...]] = {}
for _lg in LANGUAGES:
    for _m in _lg.manifests:
        MANIFESTS[_m] = (*MANIFESTS.get(_m, ()), _lg)
MANIFEST_EXTS = {e: lg for lg in LANGUAGES for e in lg.manifest_exts}
_TEST = {lg.name: re.compile(lg.test) for lg in LANGUAGES if lg.test}


def of(path: str) -> Language | None:
    return _EXT.get(posixpath.splitext(path)[1].lower())


def name_of(path: str) -> str:
    lg = of(path)
    return lg.name if lg else ""


def is_js(path: str) -> bool:
    return of(path) is JS


def strip_ext(name: str) -> str:
    return RE_EXT.sub("", name)


def is_test(path: str) -> bool:
    """Test files of the non-JS languages (JS keeps classify.RE_TEST)."""
    lg = of(path)
    rx = _TEST.get(lg.name) if lg else None
    return bool(rx and rx.search(path))


def manifest_langs(filename: str) -> tuple[Language, ...]:
    hit = MANIFESTS.get(filename)
    if hit:
        return hit
    lg = MANIFEST_EXTS.get(posixpath.splitext(filename)[1].lower())
    return (lg,) if lg else ()


def build_dirs() -> set[str]:
    return {d for lg in LANGUAGES for d in lg.build_dirs}


def build_dirs_at(filenames) -> set[str]:
    """Output folders to skip in a folder holding these files (a go.mod skips vendor/, a .csproj bin/ and obj/)."""
    out: set[str] = set()
    for fn in filenames:
        for lg in manifest_langs(fn):
            out.update(lg.build_dirs)
    return out
