import { create } from 'zustand';
import { api, type ChainPayload, type EndpointPayload, type FileInfo, type GraphData } from './api';
import { buildIndex, chainOf, orderedOf, setOf, type Highlight, type Index } from './lib/chain';

export type View = 'explorer' | 'routes' | 'features' | 'search' | 'architecture' | 'settings';
export type ColorBy = 'layer' | 'context' | 'ring' | 'lang';

export interface Filters {
  hiddenLayers: Set<string>;
  hideTests: boolean;
  hideConfig: boolean;
  hideTypes: boolean;
  typeEdges: boolean;
  apiEdges: boolean;
  particles: boolean;
}

/** What the right panel shows. */
export type Detail =
  | { kind: 'file'; file: FileInfo; chain: ChainPayload | null }
  | { kind: 'endpoint'; endpoint: EndpointPayload }
  | { kind: 'feature'; title: string; subtitle?: string; files: string[]; chain: ChainPayload | null }
  | null;

interface State {
  graph: GraphData | null;
  index: Index | null;
  error: string | null;
  view: View;
  sidebarOpen: boolean;
  selected: string | null;
  hovered: string | null;
  pinned: Highlight | null; // click: stays until Esc / background click
  hover: Highlight | null; // hover: transient
  detail: Detail;
  detailLoading: boolean;
  filters: Filters;
  mode3d: boolean;
  colorBy: ColorBy;
  theme: 'dark' | 'light';
  live: boolean;
  quickOpen: boolean;
  focusSearch: number;
  revealTick: number;
  fitTick: number;
  ai: { status: string; target: string | null; lines: string[]; engine?: string; progress?: string };
  toast: string | null;

  load: () => Promise<void>;
  setView: (v: View) => void;
  setHovered: (id: string | null) => void;
  select: (id: string | null, opts?: { fit?: boolean }) => Promise<void>;
  showEndpoint: (route: string) => Promise<void>;
  showFeature: (title: string, files: string[], target?: string, subtitle?: string) => Promise<void>;
  clear: () => void;
  setFilters: (f: Partial<Filters>) => void;
  toggleLayer: (layer: string) => void;
  set: (partial: Partial<State>) => void;
  notify: (msg: string) => void;
}

const savedTheme = (() => {
  try {
    return (localStorage.getItem('nimblegraph.theme') as 'dark' | 'light') || 'dark';
  } catch {
    return 'dark';
  }
})();

export const useStore = create<State>((set, get) => ({
  graph: null,
  index: null,
  error: null,
  view: 'explorer',
  sidebarOpen: true,
  selected: null,
  hovered: null,
  pinned: null,
  hover: null,
  detail: null,
  detailLoading: false,
  filters: {
    hiddenLayers: new Set(),
    hideTests: true,
    hideConfig: true,
    hideTypes: false,
    typeEdges: false,
    apiEdges: true,
    particles: true,
  },
  mode3d: false,
  colorBy: 'layer',
  theme: savedTheme,
  live: false,
  quickOpen: false,
  focusSearch: 0,
  revealTick: 0,
  fitTick: 0,
  ai: { status: 'idle', target: null, lines: [] },
  toast: null,

  load: async () => {
    try {
      const graph = await api.graph();
      const index = buildIndex(graph);
      set({ graph, index, error: null });
      // refresh what is open, keeping the selection
      const { selected, detail } = get();
      if (selected && index.nodes.has(selected)) {
        const file = await api.file(selected);
        const chain = detail?.kind === 'file' ? await api.chain(selected) : null;
        set({ detail: { kind: 'file', file, chain }, pinned: chainOf(index, selected) });
      }
    } catch (e) {
      set({ error: String(e) });
    }
  },

  setView: (view) => set((s) => ({ view, sidebarOpen: s.view === view ? !s.sidebarOpen : true })),

  setHovered: (id) => {
    const { index, hovered } = get();
    if (id === hovered) return;
    set({ hovered: id, hover: id && index ? chainOf(index, id) : null });
  },

  select: async (id, opts) => {
    const { index } = get();
    if (!id || !index) {
      get().clear();
      return;
    }
    set({ selected: id, pinned: chainOf(index, id), detailLoading: true, revealTick: get().revealTick + 1 });
    if (opts?.fit !== false) set({ fitTick: get().fitTick + 1 });
    try {
      const [file, chain] = await Promise.all([api.file(id), api.chain(id)]);
      if (get().selected === id) set({ detail: { kind: 'file', file, chain }, detailLoading: false });
    } catch (e) {
      set({ detailLoading: false, toast: String(e) });
    }
  },

  showEndpoint: async (route) => {
    const { index } = get();
    if (!index) return;
    set({ detailLoading: true });
    const ep = await api.endpoint(route);
    const files: string[] = [];
    for (const s of ep.steps ?? []) if (s.file && index.nodes.has(s.file)) files.push(s.file);
    for (const path of ep.front ?? []) for (const p of path) if (index.nodes.has(p.file)) files.unshift(p.file);
    set({
      selected: null,
      pinned: orderedOf(index, files),
      detail: { kind: 'endpoint', endpoint: ep },
      detailLoading: false,
      fitTick: get().fitTick + 1,
    });
  },

  showFeature: async (title, files, target, subtitle) => {
    const { index } = get();
    if (!index) return;
    set({ selected: null, pinned: setOf(index, files), detail: { kind: 'feature', title, subtitle, files, chain: null },
          fitTick: get().fitTick + 1 });
    if (target) {
      const chain = await api.chain(target);
      set({ detail: { kind: 'feature', title, subtitle, files, chain } });
    }
  },

  clear: () => set({ selected: null, pinned: null, detail: null }),

  setFilters: (f) => set((s) => ({ filters: { ...s.filters, ...f } })),

  toggleLayer: (layer) =>
    set((s) => {
      const hidden = new Set(s.filters.hiddenLayers);
      if (hidden.has(layer)) hidden.delete(layer);
      else hidden.add(layer);
      return { filters: { ...s.filters, hiddenLayers: hidden } };
    }),

  set: (partial) => set(partial),

  notify: (msg) => {
    set({ toast: msg });
    setTimeout(() => {
      if (get().toast === msg) set({ toast: null });
    }, 3500);
  },
}));
