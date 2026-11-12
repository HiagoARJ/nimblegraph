import { Layers, Maximize2, Moon, Orbit, Search, Sun } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { LAYERS, LAYER_ORDER } from '../lib/layers';
import { useStore } from '../store';

function Toggle({ on, onClick, children, title }: { on: boolean; onClick: () => void; children: React.ReactNode; title?: string }) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={`flex items-center gap-1 rounded-sm border px-2 py-[3px] text-[12px] ${on ? 'border-accent bg-accent/20 text-fg' : 'border-line text-dim hover:text-fg'}`}
    >
      {children}
    </button>
  );
}

export function Toolbar() {
  const filters = useStore((s) => s.filters);
  const mode3d = useStore((s) => s.mode3d);
  const colorBy = useStore((s) => s.colorBy);
  const theme = useStore((s) => s.theme);
  const graph = useStore((s) => s.graph);
  const { set, setFilters, toggleLayer } = useStore.getState();
  const [layersOpen, setLayersOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!layersOpen) return;
    const close = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node)) setLayersOpen(false);
    };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [layersOpen]);

  const counts: Record<string, number> = {};
  for (const n of graph?.nodes ?? []) counts[n.layer] = (counts[n.layer] ?? 0) + 1;
  const hiddenCount = filters.hiddenLayers.size;

  return (
    <div className="absolute top-2 right-2 left-2 z-10 flex flex-wrap items-center gap-1.5">
      <button
        onClick={() => set({ quickOpen: true })}
        className="flex w-[260px] items-center gap-2 rounded-sm border border-line bg-side/90 px-2 py-[3px] text-left text-dim hover:text-fg"
      >
        <Search size={13} /> buscar arquivo <span className="ml-auto text-[11px]">Ctrl+P</span>
      </button>
      <div className="relative" ref={menu}>
        <Toggle on={hiddenCount > 0} onClick={() => setLayersOpen(!layersOpen)} title="camadas visíveis">
          <Layers size={13} /> camadas{hiddenCount ? ` (${hiddenCount} ocultas)` : ''}
        </Toggle>
        {layersOpen && (
          <div className="absolute top-8 left-0 w-[210px] rounded-sm border border-line bg-side p-1 shadow-xl">
            {LAYER_ORDER.filter((l) => counts[l]).map((l) => {
              const st = LAYERS[l];
              const Icon = st.icon;
              const hidden = filters.hiddenLayers.has(l);
              return (
                <label key={l} className="flex cursor-pointer items-center gap-2 px-2 py-[2px] hover:bg-hover">
                  <input type="checkbox" checked={!hidden} onChange={() => toggleLayer(l)} />
                  <Icon size={13} style={{ color: st.color }} />
                  <span>{st.label}</span>
                  <span className="ml-auto text-[11px] text-dim">{counts[l]}</span>
                </label>
              );
            })}
          </div>
        )}
      </div>
      <Toggle on={!filters.hideTests} onClick={() => setFilters({ hideTests: !filters.hideTests })} title="mostrar testes">testes</Toggle>
      <Toggle on={!filters.hideConfig} onClick={() => setFilters({ hideConfig: !filters.hideConfig })} title="mostrar arquivos de config">config</Toggle>
      <Toggle on={!filters.hideTypes} onClick={() => setFilters({ hideTypes: !filters.hideTypes })} title="mostrar arquivos de tipos">tipos</Toggle>
      <Toggle on={filters.typeEdges} onClick={() => setFilters({ typeEdges: !filters.typeEdges })} title="arestas de import type">arestas type</Toggle>
      <Toggle on={filters.apiEdges} onClick={() => setFilters({ apiEdges: !filters.apiEdges })} title="conexões front ↔ back (chamadas de API)">
        <span className="h-[2px] w-3 bg-[#ff9800]" /> front ↔ back
      </Toggle>
      <div className="flex items-center overflow-hidden rounded-sm border border-line text-[12px]" title="colorir os arquivos por">
        {(['layer', 'context', 'ring', 'lang'] as const).map((c) => (
          <button
            key={c}
            onClick={() => set({ colorBy: c })}
            className={`px-2 py-[3px] ${colorBy === c ? 'bg-accent/30 text-fg' : 'text-dim hover:text-fg'}`}
          >
            {c === 'layer' ? 'camada' : c === 'context' ? 'contexto' : c === 'ring' ? 'anel' : 'linguagem'}
          </button>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <Toggle on={mode3d} onClick={() => set({ mode3d: !mode3d })} title="alternar 2D/3D">
          <Orbit size={13} /> {mode3d ? '3D' : '2D'}
        </Toggle>
        <Toggle on={false} onClick={() => set({ fitTick: useStore.getState().fitTick + 1 })} title="enquadrar">
          <Maximize2 size={13} />
        </Toggle>
        <Toggle
          on={false}
          title="tema"
          onClick={() => {
            const t = theme === 'dark' ? 'light' : 'dark';
            set({ theme: t });
            try {
              localStorage.setItem('nimblegraph.theme', t);
            } catch {
              /* private mode */
            }
          }}
        >
          {theme === 'dark' ? <Sun size={13} /> : <Moon size={13} />}
        </Toggle>
      </div>
    </div>
  );
}
