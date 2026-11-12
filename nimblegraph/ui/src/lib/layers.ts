import {
  Anchor, Box, Braces, Cloud, Cog, Component, Database, FileText, FlaskConical, Funnel, HardDrive,
  PanelsTopLeft, Play, Route, Server, Settings2, SquareTerminal, Table, Wrench, type LucideIcon,
} from 'lucide-react';

export interface LayerStyle { color: string; icon: LucideIcon; label: string }

// front to back, the same order the CLI uses for flows
export const LAYERS: Record<string, LayerStyle> = {
  entry: { color: '#ffd166', icon: Play, label: 'entrada' },
  page: { color: '#4fc1ff', icon: FileText, label: 'página' },
  layout: { color: '#9cdcfe', icon: PanelsTopLeft, label: 'layout' },
  route: { color: '#569cd6', icon: Route, label: 'rota' },
  component: { color: '#c586c0', icon: Component, label: 'componente' },
  hook: { color: '#dcdcaa', icon: Anchor, label: 'hook' },
  store: { color: '#ce9178', icon: Database, label: 'store' },
  'api-client': { color: '#4ec9b0', icon: Cloud, label: 'cliente de API' },
  'api-route': { color: '#ff8a65', icon: Server, label: 'rota de API' },
  middleware: { color: '#d7ba7d', icon: Funnel, label: 'middleware' },
  controller: { color: '#f48771', icon: SquareTerminal, label: 'controller' },
  service: { color: '#b5cea8', icon: Cog, label: 'service' },
  repository: { color: '#7fb069', icon: HardDrive, label: 'repositório' },
  model: { color: '#e06c75', icon: Table, label: 'model' },
  util: { color: '#9d9d9d', icon: Wrench, label: 'util' },
  type: { color: '#6a9955', icon: Braces, label: 'tipo' },
  config: { color: '#858585', icon: Settings2, label: 'config' },
  test: { color: '#6b6b6b', icon: FlaskConical, label: 'teste' },
  module: { color: '#a0a0a0', icon: Box, label: 'módulo' },
};

export const LAYER_ORDER = Object.keys(LAYERS);

export function layerStyle(layer: string | undefined): LayerStyle {
  return LAYERS[layer ?? 'module'] ?? LAYERS.module;
}

export const METHOD_COLORS: Record<string, string> = {
  GET: '#4ec9b0', POST: '#dcdcaa', PUT: '#569cd6', PATCH: '#c586c0', DELETE: '#f48771',
  ALL: '#9d9d9d', HEAD: '#9d9d9d', OPTIONS: '#9d9d9d', PAGE: '#4fc1ff',
};

export const API_LINK_COLOR = '#ff9800';

// clean / hexagonal rings, inside out
export const RINGS: Record<string, { color: string; label: string }> = {
  domain: { color: '#e5c07b', label: 'domínio' },
  application: { color: '#61afef', label: 'aplicação' },
  infrastructure: { color: '#98c379', label: 'infraestrutura' },
  presentation: { color: '#c678dd', label: 'apresentação' },
};
const NO_GROUP = '#5a5a5a';
const PALETTE = ['#4fc1ff', '#f48771', '#b5cea8', '#c586c0', '#dcdcaa', '#4ec9b0', '#ce9178', '#569cd6',
                 '#d7ba7d', '#e06c75', '#9cdcfe', '#7fb069', '#ffd166', '#ff8a65', '#a29bfe', '#00b894'];

export function contextColor(ctx: string | null | undefined): string {
  if (!ctx) return NO_GROUP;
  if (ctx === 'shared' || ctx.endsWith('/shared')) return '#9d9d9d';
  let h = 0;
  for (let i = 0; i < ctx.length; i++) h = (h * 31 + ctx.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// file extension -> language (the same families the indexer parses); GitHub colours except Python, whose blue is\n// too close to TypeScript's for the common TS front + Python back mix
const LANG_BY_EXT: Record<string, string> = {
  ts: 'ts', tsx: 'ts', mts: 'ts', cts: 'ts', js: 'js', jsx: 'js', mjs: 'js', cjs: 'js', py: 'python', pyi: 'python',
  go: 'go', java: 'java', kt: 'kotlin', cs: 'csharp', php: 'php', rb: 'ruby', rake: 'ruby', ru: 'ruby', rs: 'rust',
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', hxx: 'cpp', swift: 'swift',
};
export const LANGS: Record<string, { color: string; label: string }> = {
  ts: { color: '#3178c6', label: 'TypeScript' }, js: { color: '#f1e05a', label: 'JavaScript' },
  python: { color: '#2bbc8a', label: 'Python' }, go: { color: '#00add8', label: 'Go' },
  java: { color: '#b07219', label: 'Java' }, kotlin: { color: '#a97bff', label: 'Kotlin' },
  csharp: { color: '#178600', label: 'C#' }, php: { color: '#4f5d95', label: 'PHP' },
  ruby: { color: '#cc342d', label: 'Ruby' }, rust: { color: '#dea584', label: 'Rust' },
  c: { color: '#a8b9cc', label: 'C' }, cpp: { color: '#f34b7d', label: 'C++' }, swift: { color: '#f05138', label: 'Swift' },
};

export function langOf(path: string | undefined): string | null {
  if (!path) return null;
  const ext = path.slice(path.lastIndexOf('.') + 1).toLowerCase();
  return LANG_BY_EXT[ext] ?? null;
}

/** Node colour for the toolbar's "cor" choice: layer (default), bounded context, ring or language. */
export function nodeColor(n: { id?: string; layer: string; context?: string | null; ring?: string | null }, by: string): string {
  if (by === 'lang') return LANGS[langOf(n.id) ?? '']?.color ?? NO_GROUP;
  if (by === 'context') return contextColor(n.context);
  if (by === 'ring') return n.ring ? (RINGS[n.ring]?.color ?? NO_GROUP) : NO_GROUP;
  return layerStyle(n.layer).color;
}

export function basename(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}

export function dirname(path: string): string {
  const i = path.lastIndexOf('/');
  return i < 0 ? '' : path.slice(0, i);
}

export function hexAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255).toString(16).padStart(2, '0');
  return `${hex}${a}`;
}
