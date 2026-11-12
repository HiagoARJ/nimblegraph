import type { GLink, GNode, GraphData } from '../api';

/** Adjacency built once per graph; lets hover compute a chain instantly in the browser. */
export interface Index {
  nodes: Map<string, GNode>;
  out: Map<string, { id: string; kinds: string[] }[]>;
  inn: Map<string, { id: string; kinds: string[] }[]>;
}

export const linkId = (l: GLink): [string, string] => [
  typeof l.source === 'string' ? l.source : l.source.id,
  typeof l.target === 'string' ? l.target : l.target.id,
];
export const linkKey = (s: string, t: string) => `${s}>${t}`;

export function buildIndex(g: GraphData): Index {
  const nodes = new Map(g.nodes.map((n) => [n.id, n]));
  const out = new Map<string, { id: string; kinds: string[] }[]>();
  const inn = new Map<string, { id: string; kinds: string[] }[]>();
  for (const l of g.links) {
    const [s, t] = linkId(l);
    if (!out.has(s)) out.set(s, []);
    if (!inn.has(t)) inn.set(t, []);
    out.get(s)!.push({ id: t, kinds: l.kinds });
    inn.get(t)!.push({ id: s, kinds: l.kinds });
  }
  return { nodes, out, inn };
}

const ROOTS = new Set(['page', 'layout', 'entry', 'api-route', 'route']);
const NOISE = new Set(['test', 'config']);
const typeOnly = (kinds: string[]) => kinds.length === 1 && kinds[0] === 'type';

export interface Highlight {
  nodes: Set<string>;
  links: Set<string>;
  order?: Map<string, number>; // endpoint steps, numbered in execution order
  focus?: string;
}

/**
 * The connected chain of a file, like `nimblegraph analyze` on that file: up through importers until
 * pages/routes/entries, and down through what it uses (hubs are not expanded).
 */
export function chainOf(idx: Index, start: string, depth = 4): Highlight {
  const nodes = new Set([start]);
  const links = new Set<string>();
  // up
  const upQ = [start];
  const upSeen = new Set([start]);
  while (upQ.length && upSeen.size < 120) {
    const f = upQ.shift()!;
    const n = idx.nodes.get(f);
    if (f !== start && n && ROOTS.has(n.layer)) continue;
    for (const e of idx.inn.get(f) ?? []) {
      if (typeOnly(e.kinds) || NOISE.has(idx.nodes.get(e.id)?.layer ?? '')) continue;
      links.add(linkKey(e.id, f));
      nodes.add(e.id);
      if (!upSeen.has(e.id)) {
        upSeen.add(e.id);
        upQ.push(e.id);
      }
    }
  }
  // down
  const downQ: [string, number][] = [[start, 0]];
  const downSeen = new Set([start]);
  while (downQ.length) {
    const [f, d] = downQ.shift()!;
    if (d >= depth) continue;
    const n = idx.nodes.get(f);
    if (f !== start && n?.hub) continue;
    for (const e of idx.out.get(f) ?? []) {
      if (typeOnly(e.kinds) || NOISE.has(idx.nodes.get(e.id)?.layer ?? '')) continue;
      links.add(linkKey(f, e.id));
      nodes.add(e.id);
      if (!downSeen.has(e.id)) {
        downSeen.add(e.id);
        downQ.push([e.id, d + 1]);
      }
    }
  }
  return { nodes, links, focus: start };
}

/** Highlight for a set of files (a feature / chain note): the links among them. */
export function setOf(idx: Index, files: Iterable<string>): Highlight {
  const nodes = new Set(files);
  const links = new Set<string>();
  for (const f of nodes) {
    for (const e of idx.out.get(f) ?? []) if (nodes.has(e.id)) links.add(linkKey(f, e.id));
  }
  return { nodes, links };
}

/** Endpoint: files numbered in request order, links between consecutive steps and among the files. */
export function orderedOf(idx: Index, files: string[]): Highlight {
  const h = setOf(idx, files);
  const order = new Map<string, number>();
  files.forEach((f) => {
    if (!order.has(f)) order.set(f, order.size + 1);
  });
  for (let i = 1; i < files.length; i++) {
    if (files[i - 1] !== files[i]) h.links.add(linkKey(files[i - 1], files[i]));
  }
  return { ...h, order };
}
