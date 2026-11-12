import { useEffect, useRef, useState } from 'react';
import { api, type SearchPayload } from '../../api';
import { METHOD_COLORS, basename, layerStyle } from '../../lib/layers';
import { useStore } from '../../store';
import { Section } from './Routes';

/** Search in paths, symbols, routes and notes (server side, Ctrl+Shift+F). */
export function Search() {
  const focusSearch = useStore((s) => s.focusSearch);
  const graph = useStore((s) => s.graph);
  const { select, showEndpoint } = useStore.getState();
  const [q, setQ] = useState('');
  const [res, setRes] = useState<SearchPayload | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => input.current?.focus(), [focusSearch]);
  useEffect(() => {
    if (!q.trim()) {
      setRes(null);
      return;
    }
    const t = setTimeout(() => void api.search(q).then(setRes), 160);
    return () => clearTimeout(t);
  }, [q]);

  const layer = (p: string) => graph?.nodes.find((n) => n.id === p)?.layer;
  const Row = ({ path, text, sub, onClick }: { path?: string; text: React.ReactNode; sub?: string; onClick: () => void }) => {
    const st = layerStyle(path ? layer(path) : undefined);
    const Icon = st.icon;
    return (
      <div className="flex cursor-pointer items-center gap-1.5 px-3 py-0.5 hover:bg-hover" onClick={onClick} title={path}>
        {path && <Icon size={13} style={{ color: st.color }} className="shrink-0" />}
        <span className="truncate">{text}</span>
        {sub && <span className="ml-auto shrink-0 truncate pl-2 text-[11px] text-dim">{sub}</span>}
      </div>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-3 pb-2">
        <input
          ref={input}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="buscar em arquivos, símbolos, rotas e fichas"
          className="w-full rounded-sm border border-line bg-bg px-2 py-1 outline-none focus:border-accent"
        />
      </div>
      <div className="flex-1 overflow-auto pb-4">
        {res && (
          <>
            {!!res.notes.length && (
              <Section title={`Fichas (${res.notes.length})`}>
                {res.notes.map((n) => (
                  <Row key={n.key} path={n.key.split('#')[0]} text={n.title} sub={basename(n.key)} onClick={() => void select(n.key.split('#')[0])} />
                ))}
              </Section>
            )}
            {!!res.files.length && (
              <Section title={`Arquivos (${res.files.length})`}>
                {res.files.map((p) => (
                  <Row key={p} path={p} text={basename(p)} sub={p.slice(0, p.lastIndexOf('/'))} onClick={() => void select(p)} />
                ))}
              </Section>
            )}
            {!!res.symbols.length && (
              <Section title={`Símbolos (${res.symbols.length})`}>
                {res.symbols.map((s) => (
                  <Row key={`${s.file}#${s.name}`} path={s.file} text={<span className="mono">{s.name}</span>}
                       sub={`${s.kind} · ${basename(s.file)}:${s.line}`} onClick={() => void select(s.file)} />
                ))}
              </Section>
            )}
            {!!res.routes.length && (
              <Section title={`Rotas (${res.routes.length})`}>
                {res.routes.map((r) => (
                  <Row key={r.id} text={<><span className="mono" style={{ color: METHOD_COLORS[r.method] }}>{r.method}</span> <span className="mono">{r.path}</span></>}
                       onClick={() => void (r.method === 'PAGE' ? select(r.target) : showEndpoint(`${r.method} ${r.path}`))} />
                ))}
              </Section>
            )}
            {!res.notes.length && !res.files.length && !res.symbols.length && !res.routes.length && (
              <div className="px-3 text-dim">nada encontrado</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
