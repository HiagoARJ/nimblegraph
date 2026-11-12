// nimblegraph Node engine: parses JS/TS with the TypeScript compiler API and resolves imports
// with ts.resolveModuleName against the nearest tsconfig/jsconfig.
//
// stdin : {"root": "<abs>", "files": ["src/a.ts", ...]}
// stdout: NDJSON. First line {"engine": {...}}, then one object per file (see docs/HANDOFF.md).
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const TOOL_DIR = path.dirname(fileURLToPath(import.meta.url));

const HTTP = new Set(['get', 'post', 'put', 'patch', 'delete', 'del', 'head', 'options', 'all']);
const ROUTE_PROPS = new Set([...HTTP, 'use', 'route', 'register', 'setGlobalPrefix', 'addHook', 'mount']);
const CLIENT_OBJS = new Set(['axios', 'ky']);
// built-ins whose .get/.delete/.all are never routes or HTTP calls
const OBJ_DENY = new Set(['Promise', 'Object', 'Array', 'Map', 'Set', 'WeakMap', 'JSON', 'Math', 'Reflect', 'console',
  'window', 'document', 'localStorage', 'sessionStorage', 'process', 'searchParams', 'headers', 'cookies', 'formData']);
const CALLEES = new Set(['fetch', 'axios', '$fetch', 'ky', 'ofetch',
  'createBrowserRouter', 'createHashRouter', 'createMemoryRouter', 'useRoutes',
  'createFileRoute', 'createLazyFileRoute', 'createRootRoute', 'createRootRouteWithContext', 'createRoute']);
const ROUTE_KEYS = ['element', 'Component', 'children', 'lazy'];
const MAX_ARGS = 8;
const MAX_DEPTH = 3;

function loadTypescript(root) {
  for (const [base, from] of [[root, 'project'], [TOOL_DIR, 'tool']]) {
    try {
      const ts = createRequire(path.join(base, 'package.json'))('typescript');
      if (Number(ts.version.split('.')[0]) >= 5) return { ts, from };
    } catch { /* try next */ }
  }
  return { ts: null, from: null };
}

const posix = (p) => p.split(path.sep).join('/');
const isWin = process.platform === 'win32';
const canon = (p) => (isWin ? path.resolve(p).toLowerCase() : path.resolve(p));

// ---------------------------------------------------------------- resolution

function makeResolver(ts, root) {
  const configForDir = new Map(); // dir -> config file path | null
  const parsedConfigs = new Map(); // config file -> {options, files:Set, cache}

  // nearest tsconfig/jsconfig, walking up until the repository root (.git) or the filesystem root
  function findConfig(dir) {
    if (configForDir.has(dir)) return configForDir.get(dir);
    let found = null;
    for (const name of ['tsconfig.json', 'jsconfig.json']) {
      const p = path.join(dir, name);
      if (fs.existsSync(p)) { found = p; break; }
    }
    const parent = path.dirname(dir);
    if (!found && parent !== dir && !fs.existsSync(path.join(dir, '.git'))) found = findConfig(parent);
    configForDir.set(dir, found);
    return found;
  }

  function normalizeOptions(o) {
    const opts = { ...o, allowJs: true, resolveJsonModule: true, noEmit: true };
    const MR = ts.ModuleResolutionKind;
    const MK = ts.ModuleKind;
    if (opts.moduleResolution === undefined) {
      if (![MK.Node16, MK.NodeNext, MK.CommonJS].includes(opts.module)) opts.moduleResolution = MR.Bundler;
    } else if (opts.moduleResolution === MR.Classic) {
      opts.moduleResolution = MR.Bundler;
    }
    return opts;
  }

  function parseConfig(file) {
    if (parsedConfigs.has(file)) return parsedConfigs.get(file);
    let entry = null;
    try {
      const read = ts.readConfigFile(file, ts.sys.readFile);
      if (!read.error) {
        const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, path.dirname(file), undefined, file);
        const options = normalizeOptions(parsed.options);
        entry = {
          options,
          files: new Set(parsed.fileNames.map(canon)),
          refs: (parsed.projectReferences || []).map((r) => ts.resolveProjectReferencePath(r)),
          cache: ts.createModuleResolutionCache(path.dirname(file), (x) => (isWin ? x.toLowerCase() : x), options),
        };
      }
    } catch { /* broken config: fall back to defaults */ }
    parsedConfigs.set(file, entry);
    return entry;
  }

  const defaults = (() => {
    const options = normalizeOptions({ module: ts.ModuleKind.ESNext });
    return { options, files: new Set(), refs: [], cache: ts.createModuleResolutionCache(root, (x) => x, options) };
  })();

  function configFor(absFile) {
    const cfgFile = findConfig(path.dirname(absFile));
    const base = cfgFile ? parseConfig(cfgFile) : null;
    if (!base) return defaults;
    if (base.options.paths || !base.refs.length) return base;
    // Vite pattern: tsconfig.json only holds references; pick the referenced config that owns the file
    const refs = base.refs.map((r) => (fs.existsSync(r) ? parseConfig(r) : null)).filter(Boolean);
    const key = canon(absFile);
    return refs.find((r) => r.files.has(key)) || refs.find((r) => r.options.paths) || base;
  }

  const bundlerCache = new Map();
  function bundlerVariant(cfg) {
    if (!bundlerCache.has(cfg)) {
      const options = { ...cfg.options, moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext };
      bundlerCache.set(cfg, { options, cache: ts.createModuleResolutionCache(root, (x) => x, options) });
    }
    return bundlerCache.get(cfg);
  }

  return function resolve(spec, absFile) {
    const cfg = configFor(absFile);
    let r;
    try {
      r = ts.resolveModuleName(spec, absFile, cfg.options, ts.sys, cfg.cache).resolvedModule;
      const MR = ts.ModuleResolutionKind;
      if (!r && [MR.Node16, MR.NodeNext, MR.Node10].includes(cfg.options.moduleResolution)) {
        const b = bundlerVariant(cfg);
        r = ts.resolveModuleName(spec, absFile, b.options, ts.sys, b.cache).resolvedModule;
      }
    } catch {
      r = undefined;
    }
    if (!r) return { resolved: null, external: false };
    const rel = posix(path.relative(root, r.resolvedFileName));
    const inNodeModules = /(^|\/)node_modules\//.test(posix(r.resolvedFileName));
    return { resolved: rel, external: Boolean(r.isExternalLibraryImport && inNodeModules) };
  };
}

// ---------------------------------------------------------------- extraction

function extractFile(ts, sf, rel) {
  const ext = path.extname(rel).toLowerCase();
  const jsxFile = ext === '.tsx' || ext === '.jsx';
  const SK = ts.SyntaxKind;
  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
  const startLine = (n) => lineOf(n.getStart(sf));
  const endLine = (n) => lineOf(n.getEnd());

  const imports = [];
  const exports = [];
  const symbols = [];
  const calls = [];
  const routeObjects = [];
  const jsx = [];
  const jsxSeen = new Set();
  const dynLocals = {};
  const vars = {};
  const consts = {};
  const directives = [];
  const importLocals = new Set();
  const topNames = new Set();

  const isStr = (n) => n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n));
  const hasMod = (n, k) => Boolean(ts.canHaveModifiers(n) && ts.getModifiers(n)?.some((m) => m.kind === k));
  const unwrap = (n) => {
    while (n && (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n)
      || ts.isSatisfiesExpression(n) || ts.isTypeAssertionExpression(n))) n = n.expression;
    return n;
  };
  const isFn = (n) => n && (ts.isArrowFunction(n) || ts.isFunctionExpression(n));
  const nameText = (n) => (n && (ts.isIdentifier(n) || ts.isStringLiteral(n) || ts.isPrivateIdentifier(n) || ts.isNumericLiteral(n)) ? n.text : null);

  function chainText(n) {
    if (!n) return null;
    if (ts.isIdentifier(n)) return n.text;
    if (n.kind === SK.ThisKeyword) return 'this';
    if (ts.isPropertyAccessExpression(n) && ts.isIdentifier(n.name)) {
      const left = chainText(n.expression);
      return left ? `${left}.${n.name.text}` : null;
    }
    return null;
  }

  function tplText(n) {
    const spans = n.templateSpans;
    if (n.head.text === '' && spans.length) {
      return spans[0].literal.text + spans.slice(1).map((s) => ':param' + s.literal.text).join('');
    }
    return n.head.text + spans.map((s) => ':param' + s.literal.text).join('');
  }

  function concatParts(n, out) {
    n = unwrap(n);
    if (ts.isBinaryExpression(n) && n.operatorToken.kind === SK.PlusToken) {
      concatParts(n.left, out);
      concatParts(n.right, out);
    } else if (isStr(n)) out.push(n.text);
    else if (ts.isTemplateExpression(n)) out.push(tplText(n));
    else out.push(null);
    return out;
  }

  function toArg(node, d = 0) {
    const n = unwrap(node);
    if (!n) return { t: 'other' };
    if (isStr(n)) return { t: 'str', v: n.text };
    if (ts.isTemplateExpression(n)) return { t: 'tpl', v: tplText(n) };
    if (ts.isBinaryExpression(n) && n.operatorToken.kind === SK.PlusToken) {
      const parts = concatParts(n, []);
      if (parts.every((p) => p !== null)) return { t: 'str', v: parts.join('') };
      const rest = parts[0] === null ? parts.slice(1) : parts;
      return { t: 'tpl', v: rest.map((p) => (p === null ? ':param' : p)).join('') };
    }
    if (ts.isIdentifier(n)) return { t: 'id', v: n.text };
    if (ts.isPropertyAccessExpression(n)) {
      const c = chainText(n);
      return c ? { t: 'member', v: c } : { t: 'other' };
    }
    if (ts.isCallExpression(n)) {
      const c = chainText(n.expression);
      if (c === 'require' && isStr(n.arguments[0])) return { t: 'require', v: n.arguments[0].text };
      if (c && d < MAX_DEPTH) return { t: 'call', callee: c, args: n.arguments.slice(0, MAX_ARGS).map((a) => toArg(a, d + 1)) };
      return { t: 'other' };
    }
    if (ts.isNewExpression(n)) {
      const c = chainText(n.expression);
      if (c && d < MAX_DEPTH) return { t: 'new', callee: c, args: (n.arguments || []).slice(0, MAX_ARGS).map((a) => toArg(a, d + 1)) };
      return { t: 'other' };
    }
    if (isFn(n)) return { t: 'fn' };
    if (ts.isObjectLiteralExpression(n)) {
      if (d >= MAX_DEPTH) return { t: 'other' };
      const props = {};
      for (const p of n.properties) {
        const k = nameText(p.name);
        if (k === null) continue;
        if (ts.isPropertyAssignment(p)) props[k] = toArg(p.initializer, d + 1);
        else if (ts.isShorthandPropertyAssignment(p)) props[k] = { t: 'id', v: k };
        else if (ts.isMethodDeclaration(p)) props[k] = { t: 'fn' };
      }
      return { t: 'obj', props };
    }
    if (ts.isArrayLiteralExpression(n)) {
      if (d >= MAX_DEPTH) return { t: 'other' };
      return { t: 'arr', items: n.elements.slice(0, 16).map((e) => toArg(e, d + 1)) };
    }
    return { t: 'other' };
  }

  const argsOf = (call) => call.arguments.slice(0, MAX_ARGS).map((a) => toArg(a));

  function jsxTagName(tag) {
    if (ts.isIdentifier(tag)) return tag.text;
    return chainText(tag);
  }
  const isCompTag = (t) => Boolean(t) && /^[A-Z]/.test(t.split('.').pop());

  function containsJsx(node) {
    let found = false;
    const visit = (n) => {
      if (found) return;
      if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) { found = true; return; }
      ts.forEachChild(n, visit);
    };
    visit(node);
    return found;
  }

  function fnKind(name, node) {
    if (!name) return 'function';
    if (/^use[A-Z0-9]/.test(name)) return 'hook';
    if (/^[A-Z]/.test(name) && (jsxFile || containsJsx(node))) return 'component';
    return 'function';
  }

  function varKind(name, init) {
    const n = unwrap(init);
    if (!n) return 'const';
    if (isFn(n)) return fnKind(name, n);
    if (ts.isCallExpression(n) && isFn(unwrap(n.arguments[0]))) return fnKind(name, n);
    return 'const';
  }

  // identifiers that are declarations/property names, not references
  function isDeclName(n) {
    const p = n.parent;
    if (!p) return false;
    if (ts.isPropertyAccessExpression(p) && p.name === n) return true;
    if ((ts.isPropertyAssignment(p) || ts.isMethodDeclaration(p) || ts.isPropertyDeclaration(p)
      || ts.isPropertySignature(p) || ts.isMethodSignature(p) || ts.isGetAccessor(p) || ts.isSetAccessor(p)
      || ts.isEnumMember(p) || ts.isParameter(p)) && p.name === n) return true;
    if (ts.isBindingElement(p) && p.propertyName === n) return true;
    if (ts.isJsxAttribute(p)) return true;
    if (ts.isQualifiedName(p) && p.right === n) return true;
    return false;
  }

  let known = new Set();
  function refsOf(node, self) {
    const out = new Set();
    const visit = (n) => {
      if (ts.isIdentifier(n)) {
        if (n.text !== self && known.has(n.text) && !isDeclName(n)) out.add(n.text);
        return;
      }
      ts.forEachChild(n, visit);
    };
    visit(node);
    return [...out].sort();
  }

  // `x.y` on a known name (import/top-level) and `this.x.y`: lets references point at methods
  function membersOf(node, self) {
    const out = new Set();
    const visit = (n) => {
      if (ts.isPropertyAccessExpression(n) && ts.isIdentifier(n.name) && !ts.isJsxOpeningElement(n.parent)
        && !ts.isJsxSelfClosingElement(n.parent) && !ts.isJsxClosingElement(n.parent)) {
        const e = n.expression;
        if (ts.isIdentifier(e) && known.has(e.text) && e.text !== self) out.add(`${e.text}.${n.name.text}`);
        else if (ts.isPropertyAccessExpression(e) && e.expression.kind === SK.ThisKeyword && ts.isIdentifier(e.name)) {
          out.add(`this.${e.name.text}.${n.name.text}`);
        }
      } else if (ts.isQualifiedName(n) && ts.isIdentifier(n.left) && known.has(n.left.text) && n.left.text !== self) {
        out.add(`${n.left.text}.${n.right.text}`);
      }
      ts.forEachChild(n, visit);
    };
    visit(node);
    return [...out].sort();
  }

  function thisRefsOf(node) {
    const out = new Set();
    const visit = (n) => {
      if (ts.isPropertyAccessExpression(n) && n.expression.kind === SK.ThisKeyword && ts.isIdentifier(n.name)) out.add(n.name.text);
      ts.forEachChild(n, visit);
    };
    visit(node);
    return [...out].sort();
  }

  function decoratorsOf(n) {
    const ds = ts.canHaveDecorators(n) ? (ts.getDecorators(n) || []) : [];
    return ds.map((d) => {
      const e = d.expression;
      if (ts.isCallExpression(e)) return { name: chainText(e.expression) || '?', args: argsOf(e) };
      return { name: chainText(e) || '?', args: [] };
    });
  }

  function typeName(t) {
    if (!t) return null;
    if (ts.isTypeReferenceNode(t)) return ts.isIdentifier(t.typeName) ? t.typeName.text : t.typeName.right.text;
    if (ts.isArrayTypeNode(t)) return typeName(t.elementType);
    return null;
  }

  function methodInfo(node, name, params) {
    return {
      name,
      line: startLine(node.name),
      refs: refsOf(node, null), members: membersOf(node, null),
      this_refs: thisRefsOf(node),
      decorators: decoratorsOf(node),
      param_types: (params || []).map((p) => typeName(p.type)).filter(Boolean),
    };
  }

  function classInfo(cls) {
    const ctor = {};
    const methods = [];
    for (const m of cls.members) {
      if (ts.isConstructorDeclaration(m)) {
        for (const p of m.parameters) {
          const tn = typeName(p.type);
          if (ts.isIdentifier(p.name) && tn) ctor[p.name.text] = tn;
        }
        continue;
      }
      const name = nameText(m.name);
      if (!name) continue;
      if (ts.isMethodDeclaration(m) && m.body) {
        methods.push(methodInfo(m, name, m.parameters));
      } else if (ts.isPropertyDeclaration(m)) {
        const init = unwrap(m.initializer);
        if (isFn(init)) {
          methods.push(methodInfo(m, name, init.parameters));
        } else {
          const tn = typeName(m.type);
          if (tn && decoratorsOf(m).length) ctor[name] = tn;
          if (init && ts.isCallExpression(init) && chainText(init.expression) === 'inject' && init.arguments[0] && ts.isIdentifier(init.arguments[0])) {
            ctor[name] = init.arguments[0].text;
          }
        }
      }
    }
    return { ctor, methods };
  }

  // `class X extends A.B<T> implements I, J` -> {ext: 'B', impl: ['I', 'J']}; mixin calls are not names
  function heritageOf(decl) {
    let ext = null;
    const impl = [];
    for (const h of decl.heritageClauses || []) {
      for (const t of h.types) {
        const e = t.expression;
        const n = ts.isIdentifier(e) ? e.text : ts.isPropertyAccessExpression(e) ? e.name.text : null;
        if (!n) continue;
        if (h.token === SK.ExtendsKeyword && ts.isClassDeclaration(decl)) { if (ext === null) ext = n; } else impl.push(n);
      }
    }
    return { ext, impl };
  }

  function objectMethods(obj) {
    const methods = [];
    for (const p of obj.properties) {
      const name = nameText(p.name);
      if (!name) continue;
      if (ts.isMethodDeclaration(p) && p.body) methods.push(methodInfo(p, name, p.parameters));
      else if (ts.isPropertyAssignment(p) && isFn(unwrap(p.initializer))) methods.push(methodInfo(p, name, unwrap(p.initializer).parameters));
    }
    return methods;
  }

  // ---- directives
  for (const st of sf.statements) {
    if (ts.isExpressionStatement(st) && ts.isStringLiteral(st.expression)) directives.push(st.expression.text);
    else break;
  }

  // ---- static imports / re-exports
  const addImport = (spec, kind, names, node) => imports.push({ spec, kind, names, line: startLine(node) });
  for (const st of sf.statements) {
    if (ts.isImportDeclaration(st) && isStr(st.moduleSpecifier)) {
      const spec = st.moduleSpecifier.text;
      const c = st.importClause;
      if (!c) { addImport(spec, 'side-effect', [], st); continue; }
      const names = [];
      let anyValue = false;
      if (c.name) { names.push(['default', c.name.text]); anyValue = true; }
      if (c.namedBindings) {
        if (ts.isNamespaceImport(c.namedBindings)) {
          names.push(['*', c.namedBindings.name.text]);
          anyValue = true;
        } else {
          for (const el of c.namedBindings.elements) {
            names.push([(el.propertyName || el.name).text, el.name.text]);
            if (!el.isTypeOnly) anyValue = true;
          }
        }
      }
      names.forEach(([, local]) => importLocals.add(local));
      addImport(spec, c.isTypeOnly || (!anyValue && names.length) ? 'type' : 'import', names, st);
    } else if (ts.isImportEqualsDeclaration(st) && ts.isExternalModuleReference(st.moduleReference) && isStr(st.moduleReference.expression)) {
      importLocals.add(st.name.text);
      addImport(st.moduleReference.expression.text, st.isTypeOnly ? 'type' : 'require', [['*', st.name.text]], st);
    } else if (ts.isExportDeclaration(st)) {
      if (st.moduleSpecifier && isStr(st.moduleSpecifier)) {
        const spec = st.moduleSpecifier.text;
        if (!st.exportClause) addImport(spec, 'reexport-all', [], st);
        else if (ts.isNamespaceExport(st.exportClause)) addImport(spec, 'reexport', [['*', st.exportClause.name.text]], st);
        else addImport(spec, 'reexport', st.exportClause.elements.map((el) => [(el.propertyName || el.name).text, el.name.text]), st);
      } else if (st.exportClause && ts.isNamedExports(st.exportClause)) {
        for (const el of st.exportClause.elements) {
          exports.push({ name: el.name.text, kind: 'local', local: (el.propertyName || el.name).text, line: startLine(st) });
        }
      }
    }
  }

  // ---- top-level names (for refs)
  for (const st of sf.statements) {
    if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isInterfaceDeclaration(st)
      || ts.isTypeAliasDeclaration(st) || ts.isEnumDeclaration(st)) && st.name) topNames.add(st.name.text);
    else if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) if (ts.isIdentifier(d.name)) topNames.add(d.name.text);
    }
  }
  known = new Set([...importLocals, ...topNames]);

  // ---- declarations, exports, symbols
  const symRanges = []; // [startPos, endPos, symName] for call attribution
  for (const st of sf.statements) {
    if (hasMod(st, SK.DeclareKeyword)) continue;
    const exported = hasMod(st, SK.ExportKeyword);
    const isDefault = hasMod(st, SK.DefaultKeyword);
    const line = startLine(st);
    if (ts.isFunctionDeclaration(st)) {
      if (!st.body) continue;
      const name = st.name?.text;
      const kind = fnKind(name, st);
      if (name) {
        symbols.push({ name, kind, line: startLine(st.name), end: endLine(st), exported, refs: refsOf(st, name), members: membersOf(st, name) });
        symRanges.push([st.pos, st.end, name]);
      }
      if (exported) exports.push({ name: isDefault ? 'default' : name, kind, line, ...(isDefault && name ? { local: name } : {}) });
    } else if (ts.isClassDeclaration(st)) {
      const name = st.name?.text;
      const { ctor, methods } = classInfo(st);
      if (name) {
        symbols.push({
          name, kind: 'class', line: startLine(st.name), end: endLine(st), exported, refs: refsOf(st, name), members: membersOf(st, name),
          decorators: decoratorsOf(st), ctor_types: ctor, methods,
          extends: heritageOf(st).ext, implements: heritageOf(st).impl, abstract: hasMod(st, SK.AbstractKeyword),
        });
        for (const m of st.members) {
          const mn = ts.isConstructorDeclaration(m) ? 'constructor' : nameText(m.name);
          if (mn) symRanges.push([m.pos, m.end, `${name}.${mn}`]);
        }
        symRanges.push([st.pos, st.end, name]);
      }
      if (exported) exports.push({ name: isDefault ? 'default' : name, kind: 'class', line, ...(isDefault && name ? { local: name } : {}) });
    } else if (ts.isVariableStatement(st)) {
      for (const d of st.declarationList.declarations) {
        if (!ts.isIdentifier(d.name)) continue;
        const name = d.name.text;
        const init = unwrap(d.initializer);
        const kind = varKind(name, d.initializer);
        const sym = { name, kind, line: startLine(d.name), end: endLine(d), exported, refs: refsOf(d, name), members: membersOf(d, name) };
        if (init && ts.isObjectLiteralExpression(init)) {
          const methods = objectMethods(init);
          if (methods.length) sym.methods = methods;
          for (const p of init.properties) {
            const pn = nameText(p.name);
            if (pn) symRanges.push([p.pos, p.end, `${name}.${pn}`]);
          }
        }
        symbols.push(sym);
        symRanges.push([d.pos, d.end, name]);
        if (exported) {
          exports.push({ name, kind, line });
          if (init && (ts.isObjectLiteralExpression(init) || ts.isArrayLiteralExpression(init) || isStr(init) || ts.isTemplateExpression(init))) {
            consts[name] = toArg(init);
          }
        }
        if (init) {
          let spec = null;
          const find = (n) => {
            if (spec) return;
            if (ts.isCallExpression(n) && n.expression.kind === SK.ImportKeyword && isStr(n.arguments[0])) { spec = n.arguments[0].text; return; }
            ts.forEachChild(n, find);
          };
          find(init);
          if (spec) dynLocals[name] = spec;
        }
      }
    } else if (ts.isInterfaceDeclaration(st) || ts.isTypeAliasDeclaration(st) || ts.isEnumDeclaration(st)) {
      const name = st.name.text;
      const sym = { name, kind: 'type', line: startLine(st.name), end: endLine(st), exported, refs: refsOf(st, name), members: membersOf(st, name) };
      if (ts.isInterfaceDeclaration(st)) {
        const { impl } = heritageOf(st);
        if (impl.length) sym.implements = impl;
      }
      symbols.push(sym);
      if (exported) exports.push({ name, kind: 'type', line });
    } else if (ts.isExportAssignment(st)) {
      const e = unwrap(st.expression);
      if (ts.isIdentifier(e)) exports.push({ name: 'default', kind: 'id', local: e.text, line });
      else if (isFn(e)) exports.push({ name: 'default', kind: 'function', line });
      else if (ts.isClassExpression(e)) exports.push({ name: 'default', kind: 'class', line });
      else exports.push({ name: 'default', kind: 'expr', line });
    } else if (ts.isExpressionStatement(st) && ts.isBinaryExpression(st.expression) && st.expression.operatorToken.kind === SK.EqualsToken) {
      const left = chainText(st.expression.left);
      const right = unwrap(st.expression.right);
      if (left === 'module.exports') {
        if (ts.isIdentifier(right)) exports.push({ name: 'default', kind: 'cjs', local: right.text, line });
        else if (ts.isObjectLiteralExpression(right)) {
          for (const p of right.properties) {
            const k = nameText(p.name);
            if (k === null) continue;
            const local = ts.isShorthandPropertyAssignment(p) ? k
              : (ts.isPropertyAssignment(p) && ts.isIdentifier(unwrap(p.initializer)) ? unwrap(p.initializer).text : undefined);
            exports.push({ name: k, kind: 'cjs', line, ...(local ? { local } : {}) });
          }
        } else exports.push({ name: 'default', kind: 'cjs', line });
      } else if (left && /^(module\.)?exports\.[\w$]+$/.test(left)) {
        const k = left.split('.').pop();
        exports.push({ name: k, kind: 'cjs', line, ...(ts.isIdentifier(right) ? { local: right.text } : {}) });
      }
    }
  }
  const exportedLocals = new Set(exports.map((e) => e.local).filter(Boolean));
  for (const s of symbols) if (exportedLocals.has(s.name)) s.exported = true;

  // ---- whole-tree walk: calls, require/import(), JSX, vars
  const symAt = (pos) => {
    let best = null;
    for (const [a, b, name] of symRanges) {
      if (pos >= a && pos < b && (!best || b - a < best[1] - best[0])) best = [a, b, name];
    }
    return best ? best[2] : null;
  };

  function handleCall(n) {
    const callee = n.expression;
    if (callee.kind === SK.ImportKeyword) {
      if (isStr(n.arguments[0])) imports.push({ spec: n.arguments[0].text, kind: 'dynamic', names: [], line: startLine(n) });
      return;
    }
    if (ts.isIdentifier(callee)) {
      const c = callee.text;
      if (c === 'require') {
        if (n.arguments.length === 1 && isStr(n.arguments[0])) {
          const p = n.parent;
          let names = [];
          if (ts.isVariableDeclaration(p) && p.initializer === n) {
            if (ts.isIdentifier(p.name)) names = [['*', p.name.text]];
            else if (ts.isObjectBindingPattern(p.name)) {
              names = p.name.elements.filter((el) => ts.isIdentifier(el.name)).map((el) => [(el.propertyName && nameText(el.propertyName)) || el.name.text, el.name.text]);
            }
          }
          imports.push({ spec: n.arguments[0].text, kind: 'require', names, line: startLine(n) });
        }
        return;
      }
      if (CALLEES.has(c) || c.startsWith('useSWR')) {
        calls.push({ obj: null, prop: null, callee: c, args: argsOf(n), line: startLine(n), sym: symAt(n.getStart(sf)) });
      } else if (n.arguments.length) {
        // request('/me'), api(`/users/${id}`): calls to local HTTP wrappers are told apart later (frameworks/api_calls)
        const a0 = toArg(n.arguments[0]);
        if ((a0.t === 'str' || a0.t === 'tpl') && a0.v.startsWith('/')) {
          calls.push({ obj: null, prop: null, callee: c, args: argsOf(n), line: startLine(n), sym: symAt(n.getStart(sf)) });
        }
      }
      return;
    }
    if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.name)) {
      const prop = callee.name.text;
      let objNode = callee.expression;
      let pre = [];
      while (ts.isCallExpression(objNode) && ts.isPropertyAccessExpression(objNode.expression) && ts.isIdentifier(objNode.expression.name)) {
        const ip = objNode.expression.name.text;
        if (ip === 'route' && objNode.arguments.length === 1) {
          pre = [toArg(objNode.arguments[0])];
          objNode = objNode.expression.expression;
          break;
        }
        if (HTTP.has(ip)) { objNode = objNode.expression.expression; continue; }
        break;
      }
      let obj = chainText(objNode);
      // request(app).post('/x') (supertest): the object is the result of a call -> 'request()'
      if (!obj && HTTP.has(prop) && ts.isCallExpression(objNode)) {
        const cc = chainText(objNode.expression);
        if (cc) obj = `${cc}()`;
      }
      if (obj && !OBJ_DENY.has(obj.split('.').pop()) && (ROUTE_PROPS.has(prop) || prop.startsWith('useGlobal') || CLIENT_OBJS.has(obj))) {
        calls.push({ obj, prop, callee: null, args: [...pre, ...argsOf(n)], line: startLine(n), sym: symAt(n.getStart(sf)) });
      }
    }
  }

  function walk(n) {
    if (ts.isCallExpression(n)) handleCall(n);
    else if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
      const t = jsxTagName(n.tagName);
      if (isCompTag(t) && !jsxSeen.has(t)) { jsxSeen.add(t); jsx.push(t); }
    } else if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer && !(n.name.text in vars)) {
      let init = unwrap(n.initializer);
      if (init && ts.isAwaitExpression(init)) init = unwrap(init.expression);
      if (init && (ts.isCallExpression(init) || ts.isNewExpression(init))) {
        const c = chainText(init.expression);
        if (c) vars[n.name.text] = c;
      }
    }
    ts.forEachChild(n, walk);
  }
  walk(sf);

  // ---- route objects (React Router)
  const propOf = (o, k) => o.properties.find((p) => nameText(p.name) === k);
  const hasKey = (o, keys) => o.properties.some((p) => keys.includes(nameText(p.name)));
  function compsIn(node) {
    const tags = [];
    const visit = (n) => {
      if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
        const t = jsxTagName(n.tagName);
        if (isCompTag(t)) tags.push(t);
      }
      ts.forEachChild(n, visit);
    };
    visit(node);
    return tags.reverse();
  }
  function importIn(node) {
    let spec = null;
    const visit = (n) => {
      if (spec) return;
      if (ts.isCallExpression(n) && n.expression.kind === SK.ImportKeyword && isStr(n.arguments[0])) { spec = n.arguments[0].text; return; }
      ts.forEachChild(n, visit);
    };
    visit(node);
    return spec;
  }
  const isRouteArray = (a) => a.elements.some((e) => ts.isObjectLiteralExpression(e) && hasKey(e, ['path', 'index']) && hasKey(e, ROUTE_KEYS));

  function routeFromObj(o) {
    const r = { path: null, comps: [], lazy: null, children: [] };
    const elComps = [];
    const compComps = [];
    for (const p of o.properties) {
      const k = nameText(p.name);
      if (!ts.isPropertyAssignment(p)) {
        if (ts.isShorthandPropertyAssignment(p) && k === 'Component') compComps.push(k);
        continue;
      }
      const v = unwrap(p.initializer);
      if (k === 'path') { const a = toArg(v); if (a.t === 'str' || a.t === 'tpl') r.path = a.v; }
      else if (k === 'index') r.index = true;
      else if (k === 'element') elComps.push(...compsIn(v));
      else if (k === 'Component' && ts.isIdentifier(v)) compComps.push(v.text);
      else if (k === 'lazy') r.lazy = importIn(v);
      else if (k === 'children' && ts.isArrayLiteralExpression(v)) {
        r.children = v.elements.filter(ts.isObjectLiteralExpression).map(routeFromObj);
      }
    }
    r.comps = [...elComps, ...compComps];
    return r;
  }

  const isRouteEl = (n) => (ts.isJsxElement(n) && jsxTagName(n.openingElement.tagName) === 'Route')
    || (ts.isJsxSelfClosingElement(n) && jsxTagName(n.tagName) === 'Route');

  function routeFromJsx(el) {
    const open = ts.isJsxElement(el) ? el.openingElement : el;
    const r = { path: null, comps: [], lazy: null, children: [] };
    const elComps = [];
    const compComps = [];
    for (const a of open.attributes.properties) {
      if (!ts.isJsxAttribute(a)) continue;
      const k = a.name.getText(sf);
      const init = a.initializer;
      const v = init && ts.isJsxExpression(init) ? unwrap(init.expression) : init;
      if (k === 'path' && v) { const x = toArg(v); if (x.t === 'str' || x.t === 'tpl') r.path = x.v; }
      else if (k === 'index') r.index = true;
      else if (k === 'element' && v) elComps.push(...compsIn(v));
      else if (k === 'Component' && v && ts.isIdentifier(v)) compComps.push(v.text);
      else if (k === 'lazy' && v) r.lazy = importIn(v);
    }
    r.comps = [...elComps, ...compComps];
    if (ts.isJsxElement(el)) {
      const visit = (n) => {
        if (isRouteEl(n)) { r.children.push(routeFromJsx(n)); return; }
        if (ts.isJsxElement(n)) n.children.forEach(visit);
        else if (ts.isJsxFragment(n)) n.children.forEach(visit);
        else if (ts.isJsxExpression(n) && n.expression) ts.forEachChild(n, visit);
        else if (!ts.isJsxText(n)) ts.forEachChild(n, visit);
      };
      el.children.forEach(visit);
    }
    return r;
  }

  function findRoutes(n) {
    if (ts.isArrayLiteralExpression(n) && isRouteArray(n)) {
      routeObjects.push(...n.elements.filter(ts.isObjectLiteralExpression).map(routeFromObj));
      return;
    }
    if (isRouteEl(n)) { routeObjects.push(routeFromJsx(n)); return; }
    ts.forEachChild(n, findRoutes);
  }
  findRoutes(sf);

  return {
    path: rel,
    imports,
    exports,
    symbols,
    jsx,
    calls,
    route_objects: routeObjects,
    dyn_locals: dynLocals,
    vars,
    consts,
    directives,
  };
}

function scriptKind(ts, rel) {
  const ext = path.extname(rel).toLowerCase();
  if (ext === '.tsx') return ts.ScriptKind.TSX;
  if (ext === '.jsx') return ts.ScriptKind.JSX;
  if (ext === '.js' || ext === '.mjs' || ext === '.cjs') return ts.ScriptKind.JSX; // JSX in .js is common (CRA)
  return ts.ScriptKind.TS;
}

// ---------------------------------------------------------------- main

function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const root = path.resolve(input.root);
  const { ts, from } = loadTypescript(root);
  const out = [];
  const flush = () => { if (out.length) { process.stdout.write(out.join('\n') + '\n'); out.length = 0; } };
  if (!ts) {
    out.push(JSON.stringify({ engine: { node: process.version, error: 'typescript not found (run `nimblegraph setup`)' } }));
    flush();
    process.exitCode = 2;
    return;
  }
  out.push(JSON.stringify({ engine: { node: process.version, ts: ts.version, ts_from: from } }));
  const resolve = makeResolver(ts, root);
  for (const rel of input.files) {
    const abs = path.join(root, rel);
    let rec;
    try {
      const text = fs.readFileSync(abs, 'utf8');
      const sf = ts.createSourceFile(abs, text, ts.ScriptTarget.Latest, true, scriptKind(ts, rel));
      rec = extractFile(ts, sf, rel);
      for (const imp of rec.imports) Object.assign(imp, resolve(imp.spec, abs));
    } catch (e) {
      rec = { path: rel, error: String(e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e) };
    }
    out.push(JSON.stringify(rec));
    if (out.length >= 200) flush();
  }
  flush();
}

main();
