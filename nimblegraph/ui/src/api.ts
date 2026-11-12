// Types and calls of the Python server API (nimblegraph/server.py, nimblegraph/api.py)

export interface NoteBrief { title: string; summary?: string; scope?: string }
export interface Note extends NoteBrief { resp?: string; flow?: string; tags?: string[]; hash?: string; updated?: string }

export interface GNode {
  id: string;
  layer: string;
  loc: number;
  tokens: number;
  importers: number;
  entry: boolean;
  pkg: string;
  note: NoteBrief | null;
  stale: boolean;
  routes: number;
  hub: boolean;
  context: string | null;
  ring: string | null;
  roles: string[];
  // set by the force engine
  x?: number;
  y?: number;
}

export interface GLink { source: string | GNode; target: string | GNode; kinds: string[] }
export interface RouteInfo { id: number; method: string; path: string; kind: string; file: string; target: string; handler: string | null }
export interface ChainInfo { key: string; title: string; summary: string; flow: string; state: string; files: string[] }

export interface GraphData {
  root: string;
  engine: string | null;
  built_at: number | null;
  claude: boolean;
  local: boolean;
  stats: { files: number; links: number; routes: number; notes: number; stale: number; describable: number };
  nodes: GNode[];
  links: GLink[];
  routes: RouteInfo[];
  chains: ChainInfo[];
  scopes: { scope: string; files: string[] }[];
}

export interface FileInfo {
  path: string; layer: string; loc: number; tokens: number; entry: boolean; pkg: string;
  note: Note | null; stale: boolean; symbol_notes: Record<string, Note>;
  exports: { name: string; kind: string; line: number }[];
  symbols: { name: string; kind: string; line: number; parent: string | null }[];
  imports: { path: string; kind: string; names: string[]; via: string | null; layer: string; note: NoteBrief | null }[];
  importers: { path: string; layer: string; note: NoteBrief | null }[];
  routes: { id: number; method: string; path: string; kind: string; handler: string | null }[];
  calls: { method: string; url: string; symbol: string | null; route_id: number | null; target: string | null }[];
  callers: { path: string; symbol: string | null; route: string }[];
  externals: string[];
  unresolved: { spec: string; line: number }[];
}

export interface FlowStep { path: string; depth: number; label: string | null; ref: boolean; layer?: string; title?: string | null; stale?: boolean }
export interface ChainPayload {
  target: string; files: string[]; seeds: string[]; shared: string[]; tests: string[];
  links: { source: string; target: string; label: string | null }[];
  flow: FlowStep[];
  chain_note: { key: string; title: string; summary: string; flow: string; state: string } | null;
  routes: number[]; missing: string[];
}

export interface EndpointStep {
  step: 'mount' | 'middleware' | 'validation' | 'handler' | 'down';
  file: string | null; symbol?: string | null; prefix?: string; scope?: string; external?: boolean;
  title?: string | null; depth?: number;
}
export interface EndpointPayload {
  route?: RouteInfo;
  steps?: EndpointStep[];
  shared?: string[];
  front?: { file: string; symbol: string | null }[][];
  tests?: string[];
  externals?: string[];
  files?: string[];
  matches?: RouteInfo[];
}

export interface SearchPayload {
  files: string[];
  symbols: { file: string; name: string; kind: string; line: number }[];
  routes: { id: number; method: string; path: string; target: string }[];
  notes: { key: string; title: string; summary?: string }[];
}

export interface Violation {
  rule: string; severity: 'error' | 'warn' | 'info'; src: string; dst: string | null; line: number;
  message: string; adr: string | null; state: 'new' | 'known'; key: string;
}
export interface ArchPayload {
  presets: string[]; auto: boolean; rule_errors: string[];
  summary: { new: Record<string, number>; known: Record<string, number>; resolved: number };
  violations: Violation[];
  contexts: { name: string; how: string; root: string; files: string[]; roles: Record<string, number> }[];
  links: { source: string; target: string; imports: number; http: number; leaks: { src: string; dst: string; line: number }[] }[];
  aggregates: { root: string; file: string; context: string | null; members: string[] }[];
  metrics: { by: string; modules: { name: string; files: number; loc: number; ca: number; ce: number; i: number; a: number; d: number; zone: string }[] };
  gods: { file: string; importers: number; deps: number; loc: number }[];
  tests: {
    count: number;
    endpoints: { label: string; target: string; e2e: string[]; unit: string[] }[];
    untested_roles: Record<string, { file: string; symbol: string }[]>;
    contexts: Record<string, { reached: number; files: number }>;
  };
}

export interface UpdateResult {
  text: string; to_describe: string[]; chain_state: string | null;
  ai: { started: boolean; message: string; command?: string } | null;
  error?: string;
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json() as Promise<T>;
}

const q = encodeURIComponent;
export const api = {
  graph: () => get<GraphData>('/api/graph'),
  file: (path: string) => get<FileInfo>(`/api/file?path=${q(path)}`),
  chain: (target: string) => get<ChainPayload>(`/api/chain?target=${q(target)}`),
  endpoint: (route: string) => get<EndpointPayload>(`/api/endpoint?route=${q(route)}`),
  search: (text: string) => get<SearchPayload>(`/api/search?q=${q(text)}`),
  arch: () => get<ArchPayload>('/api/arch'),
  update: async (target: string | null, ai: boolean, local = false): Promise<UpdateResult> => {
    const r = await fetch('/api/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target, ai, local }),
    });
    return r.json() as Promise<UpdateResult>;
  },
};

export type LiveEvent =
  | { type: 'graph'; data: { added: string[]; removed: string[]; changed: string[] } }
  | { type: 'notes'; data: Record<string, unknown> }
  | { type: 'ai'; data: { status: string; target: string | null; engine?: string; line?: string; lines?: string[];
                           i?: number; total?: number; eta?: string } }
  | { type: 'error'; data: { message: string } };

/** Server-sent events with automatic reconnection. */
export function subscribe(onEvent: (e: LiveEvent) => void, onState: (live: boolean) => void): () => void {
  let es: EventSource | null = null;
  let closed = false;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const open = () => {
    es = new EventSource('/api/events');
    es.addEventListener('hello', () => onState(true));
    for (const type of ['graph', 'notes', 'ai', 'error'] as const) {
      es.addEventListener(type, (ev) => onEvent({ type, data: JSON.parse((ev as MessageEvent).data) } as LiveEvent));
    }
    es.onerror = () => {
      onState(false);
      es?.close();
      if (!closed) retry = setTimeout(open, 2000);
    };
  };
  open();
  return () => {
    closed = true;
    clearTimeout(retry);
    es?.close();
  };
}
