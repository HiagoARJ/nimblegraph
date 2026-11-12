import { useEffect } from 'react';
import { subscribe } from './api';
import { ActivityBar } from './components/ActivityBar';
import { DetailPanel } from './components/DetailPanel';
import { GraphCanvas } from './components/GraphCanvas';
import { QuickOpen } from './components/QuickOpen';
import { Explorer } from './components/Sidebar/Explorer';
import { Architecture } from './components/Sidebar/Architecture';
import { Features } from './components/Sidebar/Features';
import { Routes } from './components/Sidebar/Routes';
import { Search } from './components/Sidebar/Search';
import { StatusBar } from './components/StatusBar';
import { Toolbar } from './components/Toolbar';
import { LAYERS, LAYER_ORDER } from './lib/layers';
import { useStore } from './store';

const TITLES = { explorer: 'Explorer', routes: 'Rotas', features: 'Features', search: 'Busca', architecture: 'Arquitetura',
                 settings: 'Configurações' };

function Settings() {
  const filters = useStore((s) => s.filters);
  const { setFilters } = useStore.getState();
  return (
    <div className="flex-1 overflow-auto px-3 pb-4 text-[12px]">
      <label className="flex items-center gap-2 py-1">
        <input type="checkbox" checked={filters.particles} onChange={() => setFilters({ particles: !filters.particles })} />
        partículas nas linhas
      </label>
      <div className="mt-3 mb-1 text-[11px] font-semibold text-dim uppercase">Legenda</div>
      {LAYER_ORDER.map((l) => {
        const st = LAYERS[l];
        const Icon = st.icon;
        return (
          <div key={l} className="flex items-center gap-2 py-[1px]">
            <Icon size={13} style={{ color: st.color }} /> {st.label}
          </div>
        );
      })}
      <div className="mt-3 text-dim">
        <p>● ponto: arquivo sem ficha · <span className="text-warn">*</span> ficha desatualizada</p>
        <p className="mt-1">Passe o mouse num arquivo para acender a cadeia; clique para fixar. Esc limpa.</p>
      </div>
    </div>
  );
}

export function App() {
  const view = useStore((s) => s.view);
  const sidebarOpen = useStore((s) => s.sidebarOpen);
  const graph = useStore((s) => s.graph);
  const error = useStore((s) => s.error);
  const theme = useStore((s) => s.theme);
  const { load, set, clear, notify } = useStore.getState();

  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light');
  }, [theme]);

  useEffect(() => {
    void load().then(() => {
      // deep links: ?select=<file> | ?endpoint=<METHOD /path> | ?hover=<file> | ?view=routes | ?theme=light
      const q = new URLSearchParams(location.search);
      const st = useStore.getState();
      const view = q.get('view');
      if (view && view in TITLES) set({ view: view as keyof typeof TITLES, sidebarOpen: true });
      if (q.get('theme') === 'light' || q.get('theme') === 'dark') set({ theme: q.get('theme') as 'light' | 'dark' });
      const color = q.get('color');
      if (color === 'context' || color === 'ring' || color === 'layer' || color === 'lang') set({ colorBy: color });
      if (q.get('select')) void st.select(q.get('select'));
      else if (q.get('endpoint')) void st.showEndpoint(q.get('endpoint')!);
      else if (q.get('hover')) st.setHovered(q.get('hover'));
    });
    if (new URLSearchParams(location.search).get('live') === '0') return; // static captures (headless screenshots)
    return subscribe(
      (e) => {
        if (e.type === 'graph' || e.type === 'notes') {
          void load();
          if (e.type === 'graph') {
            const n = e.data.changed.length + e.data.added.length + e.data.removed.length;
            notify(`grafo atualizado (${n} arquivo${n === 1 ? '' : 's'})`);
          } else notify('fichas atualizadas');
        } else if (e.type === 'ai') {
          const cur = useStore.getState().ai;
          const lines = e.data.line ? [...cur.lines, e.data.line].slice(-50) : cur.lines;
          const who = e.data.engine === 'local' ? 'A IA local' : 'Claude';
          const progress = e.data.i && e.data.total ? `${e.data.i}/${e.data.total}, ~${e.data.eta}` : cur.progress;
          set({ ai: { status: e.data.status, target: e.data.target, lines, engine: e.data.engine, progress } });
          if (e.data.status === 'done') notify(`${who} terminou de escrever as fichas`);
          if (e.data.status === 'error') notify(`${who} terminou com erro: ` + (e.data.lines ?? []).slice(-1).join(' '));
        } else if (e.type === 'error') notify(e.data.message);
      },
      (live) => set({ live }),
    );
  }, [load, set, notify]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === 'p') {
        e.preventDefault();
        set({ quickOpen: true });
      } else if ((e.ctrlKey || e.metaKey) && e.shiftKey && k === 'f') {
        e.preventDefault();
        set({ view: 'search', sidebarOpen: true, focusSearch: Date.now() });
      } else if (e.key === 'Escape' && !useStore.getState().quickOpen) {
        clear();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [set, clear]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <ActivityBar />
        {sidebarOpen && (
          <aside className="flex w-[280px] shrink-0 flex-col border-r border-line bg-side">
            <div className="flex h-[35px] shrink-0 items-center px-4 text-[11px] tracking-wide text-dim uppercase">
              {TITLES[view]}
              {view === 'explorer' && graph && <span className="ml-auto normal-case">{graph.root}</span>}
            </div>
            {view === 'explorer' && <Explorer />}
            {view === 'routes' && <Routes />}
            {view === 'features' && <Features />}
            {view === 'search' && <Search />}
            {view === 'architecture' && <Architecture />}
            {view === 'settings' && <Settings />}
          </aside>
        )}
        <main className="relative min-w-0 flex-1 overflow-hidden bg-bg">
          {graph ? (
            <>
              <GraphCanvas />
              <Toolbar />
            </>
          ) : (
            <div className="flex h-full items-center justify-center text-dim">{error ?? 'carregando grafo…'}</div>
          )}
        </main>
        <DetailPanel />
      </div>
      <StatusBar />
      <QuickOpen />
    </div>
  );
}
