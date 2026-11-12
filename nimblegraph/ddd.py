"""DDD reading: bounded contexts with their building blocks, the context map, and the ubiquitous language
(domain terms per context, synonyms, `.nimblegraph/glossary.toml`)."""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field

from nimblegraph import arch as arch_mod
from nimblegraph import lang as langs
from nimblegraph.config import Config
from nimblegraph.query import Graph, fold, tokens

GLOSSARY_FILE = "glossary.toml"
ROLE_SUFFIX = re.compile(r"(Aggregate|AggregateRoot|Entity|ValueObject|Vo|VO|DomainEvent|Event|Repository|Repo|"
                         r"DomainService|Service|CommandHandler|QueryHandler|EventHandler|Handler|Command|Query|"
                         r"UseCase|Usecase|Dto|DTO|Factory|Mapper|Port|Adapter|Controller|Policy|Specification|Spec)$")
TERM_ROLES = {"aggregate", "entity", "value-object"}  # the nouns of the model; verbs (commands, use cases) are not terms
# common synonym families (en / pt-BR); the glossary decides which one is the official term
SYNONYMS = [
    {"customer", "client", "cliente", "consumer", "buyer", "comprador"},
    {"order", "pedido", "purchase", "compra", "encomenda"},
    {"product", "produto", "item", "article", "artigo", "sku"},
    {"user", "usuario", "member", "membro"},
    {"invoice", "fatura", "bill", "nota", "notafiscal"},
    {"payment", "pagamento", "charge", "cobranca"},
    {"employee", "funcionario", "colaborador", "staff", "worker"},
    {"company", "empresa", "organization", "organizacao", "org", "business"},
    {"address", "endereco", "location", "localizacao"},
    {"price", "preco", "cost", "custo"},
    {"cart", "basket", "carrinho", "sacola"},
    {"supplier", "fornecedor", "vendor", "provider"},
    {"shipment", "delivery", "entrega", "envio", "remessa"},
    {"account", "conta"},
    {"category", "categoria", "group", "grupo"},
    {"schedule", "agenda", "agendamento", "appointment", "booking", "reserva"},
]


def term_of(name: str) -> str | None:
    base = ROLE_SUFFIX.sub("", name)
    base = re.sub(r"^(I|Abstract|Base)(?=[A-Z])", "", base)
    return base if len(base) >= 3 and base[0].isupper() else None


@dataclass
class Term:
    name: str
    contexts: set[str] = field(default_factory=set)
    symbols: list[tuple[str, str, str]] = field(default_factory=list)  # (file, symbol, role)


def domain_terms(g: Graph, a: arch_mod.Arch) -> dict[str, Term]:
    out: dict[str, Term] = {}
    cands = dict(a.sym_roles)
    for f, syms in g.symbols.items():  # plain classes of the domain ring are model nouns too (OrderLine)
        if a.ring.get(f) == "domain":
            for s in syms:
                if s["kind"] == "class" and not s["parent"] and not s.get("abstract") and (f, s["name"]) not in cands:
                    cands[(f, s["name"])] = {"entity"}
    for (f, sym), roles in sorted(cands.items()):
        hit = roles & TERM_ROLES
        if not hit or g.layer(f) == "test":
            continue
        t = term_of(sym)
        if not t:
            continue
        term = out.setdefault(t, Term(t))
        term.contexts.add(a.context.get(f) or "-")
        term.symbols.append((f, sym, sorted(hit)[0]))
    return out


def synonym_groups(terms: dict[str, Term]) -> list[list[str]]:
    """Domain terms that are probably the same concept under different words."""
    by_word = defaultdict(list)
    for t in terms:
        for w in tokens(t):
            by_word[w].append(t)
    out = []
    for fam in SYNONYMS:
        present = sorted({t for w in fam for t in by_word.get(w, [])
                          if len(tokens(t)) == 1 or fold(t) in fam})
        if len({fold(x) for x in present}) >= 2:
            out.append(present)
    return out


def load_glossary(cfg: Config) -> dict:
    p = arch_mod.governance_path(cfg, GLOSSARY_FILE)
    if not p.is_file():
        return {}
    with p.open("rb") as f:
        return tomllib.load(f).get("terms", {}) or {}


def glossary_hits(cfg: Config, g: Graph) -> list[tuple[str, int, str, str, str]]:
    """(file, line, official term, synonym used, message) wherever a forbidden synonym names a symbol, a file or
    a route."""
    terms = load_glossary(cfg)
    banned: dict[str, str] = {}
    for name, spec in terms.items():
        for s in (spec or {}).get("synonyms", []) if isinstance(spec, dict) else []:
            banned[fold(str(s))] = name
    if not banned:
        return []
    out = []
    for f, syms in g.symbols.items():
        if g.layer(f) in ("test", "config"):
            continue
        for s in syms:
            if not s["exported"] or s["parent"]:
                continue
            for w in tokens(s["name"]):
                if w in banned:
                    out.append((f, s["line"], banned[w], s["name"],
                                f"'{s['name']}' uses '{w}': the glossary term is '{banned[w]}'"))
                    break
        stem = langs.strip_ext(f.rsplit("/", 1)[-1])
        for w in tokens(stem):
            if w in banned and not any(x[0] == f for x in out):
                out.append((f, 0, banned[w], stem, f"file name uses '{w}': the glossary term is '{banned[w]}'"))
                break
    for r in g.routes.values():
        if r.kind != "api":
            continue
        for w in tokens(r.path):
            if w in banned:
                out.append((r.file, r.line, banned[w], f"{r.method} {r.path}",
                            f"route uses '{w}': the glossary term is '{banned[w]}'"))
                break
    return out


def glossary_toml(terms: dict[str, Term], groups: list[list[str]]) -> str:
    lines = ["# Ubiquitous language (versioned). `nimblegraph glossary` compares it with the code;",
             "# synonyms listed here are flagged by `nimblegraph check` (naming/glossary).", ""]
    if groups:
        lines.append("# Possible synonyms found in the code (pick one term, list the others as synonyms):")
        lines += [f"#   {', '.join(gr)}" for gr in groups]
        lines.append("")
    for name, t in sorted(terms.items()):
        ctx = sorted(c for c in t.contexts if c != "-")
        lines += [f"[terms.{name}]", f'context = "{ctx[0] if len(ctx) == 1 else ",".join(ctx)}"',
                  "synonyms = []", 'description = ""', ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------- per-context summary

@dataclass
class ContextInfo:
    name: str
    root: str
    how: str
    files: list[str]
    blocks: dict[str, list[tuple[str, str]]]  # role -> [(file, symbol)]
    routes: list[str]


def context_info(g: Graph, a: arch_mod.Arch, name: str) -> ContextInfo:
    files = a.files_of(name)
    blocks: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (f, sym), roles in sorted(a.sym_roles.items()):
        if a.context.get(f) != name:
            continue
        for role in roles:
            blocks[role].append((f, sym))
    routes = sorted({r.label for r in g.routes.values() if r.kind == "api" and a.context.get(r.target) == name})
    return ContextInfo(name, a.context_root.get(name, ""), a.context_how.get(name, ""), files, blocks, routes)
