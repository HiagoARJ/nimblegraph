"""HTTP calls made by the non-JS code, linked to the routes of the project: clients (HttpClient.GetFromJsonAsync,
requests/httpx, RestTemplate, http.Get, Guzzle/Http::, reqwest, Faraday...) and tests (FastAPI TestClient, Django
test client, MockMvc `perform(get(...))`, httptest.NewRequest, Rails request specs, Laravel `getJson`, ASP.NET
WebApplicationFactory clients). URLs come from the extractor's string evaluation (`$"{baseUrl}items/{id}"` ->
api/catalog/items/:param); only calls that match a route are kept, so `config.get("a/b")` adds nothing."""

from __future__ import annotations

import re

from nimblegraph.frameworks import ApiCall, Context, arg_str
from nimblegraph.frameworks.api_calls import match, normalize_url

VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}
JAVA_REST = {"getForObject": "GET", "getForEntity": "GET", "postForObject": "POST", "postForEntity": "POST",
             "postForLocation": "POST", "patchForObject": "PATCH", "put": "PUT", "delete": "DELETE"}
NEW_REQUEST = {"NewRequest", "NewRequestWithContext", "Request"}  # Go http/httptest, Python requests.Request
RE_SUFFIX = re.compile(r"(FromJson|AsJson|String|Stream|ByteArray|Json)?(Async)?$")
RE_CLIENT = re.compile(r"(client|session|http|api|rest|request|conn|faraday|party|guzzle|reqwest|agent|fetch|webclient"
                       r"|^requests$|^httpx$|^net$|^ky$)", re.I)
RE_TEST = re.compile(r"(^|/)(tests?|spec|__tests__)/|(_test|_spec|Test|Tests|IT)\.\w+$|(^|/)test_[^/]*$")


def _verb(name: str | None) -> str | None:
    if not name:
        return None
    if name in JAVA_REST:
        return JAVA_REST[name]
    base = RE_SUFFIX.sub("", name)  # GetFromJsonAsync -> Get, postJson -> post
    return base.upper() if base.lower() in VERBS else None


def _url(s: str | None) -> str | None:
    """{=baseURL}/users/{=id} -> /users/:param; a bare word (no /) is not a URL."""
    if not s or "/" not in s:
        return None
    s = re.sub(r"^(\{=[^{}]*\}|:param)+", "", s)
    s = re.sub(r"\{=[^{}]*\}", ":param", s)
    return normalize_url(s) if s else None


def _method_arg(a: dict | None) -> str | None:
    v = (a or {}).get("v") or ""
    m = re.search(r"(?:Method)?(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Get|Post|Put|Patch|Delete)\b", v)
    return m.group(1).upper() if m else None


def candidates(c: dict, test_file: bool) -> list[tuple[str, str]]:
    """(method, raw url) pairs a call may be making."""
    args = c.get("args") or []
    prop, callee = c.get("prop"), c.get("callee")
    out: list[tuple[str, str]] = []
    if prop in NEW_REQUEST or callee in NEW_REQUEST:  # http.NewRequest("GET", url, nil) / (ctx, method, url)
        strs = [a for a in args if a.get("t") in ("str", "tpl", "member")]
        meth = next((_method_arg(a) for a in strs if _method_arg(a) and "/" not in (a.get("v") or "")), None)
        url = next((a["v"] for a in strs if "/" in (a.get("v") or "")), None)
        return [(meth or "ANY", url)] if url else []
    if prop == "exchange" and len(args) >= 2:  # restTemplate.exchange(url, HttpMethod.POST, ...)
        return [(_method_arg(args[1]) or "ANY", arg_str(args[0]))] if arg_str(args[0]) else []
    verb = _verb(prop) if c.get("obj") else None
    generic = verb is not None and prop.lower() in VERBS  # get/post...: also dict.get, cache.delete...
    if verb is None and callee and (test_file or callee in ("perform",)):  # get "/x" (Rails spec), get("/x") (MockMvc)
        verb, generic = _verb(callee), False
    if verb and args:
        u = arg_str(args[0])
        obj = (c.get("obj") or "").rsplit(".", 1)[-1]
        if u and (not generic or RE_CLIENT.search(obj) or (test_file and u.startswith("/"))
                  or u.startswith(("http://", "https://"))):
            out.append((verb, u))
    for a in args:  # mockMvc.perform(get("/api/users")): the request builder is an argument
        if a.get("t") == "call" and _verb((a.get("callee") or "").rsplit(".", 1)[-1]) and a.get("args"):
            u = arg_str(a["args"][0])
            if u:
                out.append((_verb((a.get("callee") or "").rsplit(".", 1)[-1]), u))
    return out


def detect(ctx: Context) -> None:
    route_files = {r.file for r in ctx.routes}
    known = {(c.file, c.line, c.url) for c in ctx.api_calls}
    for f, rec in sorted(ctx.parsed.items()):
        if f in route_files and not RE_TEST.search(f):
            continue  # a routes file declares (get "/x" in routes.rb), it does not call
        test_file = bool(RE_TEST.search(f))
        for c in rec.get("calls", []):
            for verb, raw in candidates(c, test_file):
                url = _url(raw)
                if url is None:
                    continue
                rid = match(ctx.routes, verb, url)
                if rid is None or (f, c.get("line", 0), url) in known:
                    continue
                known.add((f, c.get("line", 0), url))
                ctx.api_calls.append(ApiCall(f, c.get("sym"), verb if verb != "ANY" else "ANY", url, c.get("line", 0),
                                             rid))
