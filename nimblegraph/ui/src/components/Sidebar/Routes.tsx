import { useMemo, useState } from 'react';
import type { RouteInfo } from '../../api';
import { METHOD_COLORS, basename } from '../../lib/layers';
import { useStore } from '../../store';

function MethodBadge({ m }: { m: string }) {
  return (
    <span className="mono w-[46px] shrink-0 text-[10px] font-semibold" style={{ color: METHOD_COLORS[m] ?? '#9d9d9d' }}>
      {m}
    </span>
  );
}

export function Routes() {
  const graph = useStore((s) => s.graph);
  const detail = useStore((s) => s.detail);
  const [filter, setFilter] = useState('');
  const { showEndpoint, select } = useStore.getState();
  const current = detail?.kind === 'endpoint' ? detail.endpoint.route?.id : null;

  const { api, pages } = useMemo(() => {
    const f = filter.toLowerCase();
    const match = (r: RouteInfo) => !f || `${r.method} ${r.path} ${r.target} ${r.handler ?? ''}`.toLowerCase().includes(f);
    const rs = (graph?.routes ?? []).filter(match);
    return { api: rs.filter((r) => r.kind === 'api'), pages: rs.filter((r) => r.kind !== 'api') };
  }, [graph, filter]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-3 pb-2">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filtrar rotas"
          className="w-full rounded-sm border border-line bg-bg px-2 py-1 outline-none focus:border-accent"
        />
      </div>
      <div className="flex-1 overflow-auto pb-4">
        <Section title={`API (${api.length})`}>
          {api.map((r) => (
            <div
              key={r.id}
              title={`${r.method} ${r.path}\n${r.target}${r.handler ? '#' + r.handler : ''}`}
              className={`flex h-[22px] cursor-pointer items-center gap-1 px-3 ${current === r.id ? 'bg-sel' : 'hover:bg-hover'}`}
              onClick={() => void showEndpoint(`${r.method} ${r.path}`)}
            >
              <MethodBadge m={r.method} />
              <span className="mono truncate">{r.path}</span>
            </div>
          ))}
        </Section>
        <Section title={`Páginas (${pages.length})`}>
          {pages.map((r) => (
            <div
              key={r.id}
              title={r.target}
              className="flex h-[22px] cursor-pointer items-center gap-1 px-3 hover:bg-hover"
              onClick={() => void select(r.target)}
            >
              <span className="mono truncate">{r.path}</span>
              <span className="ml-auto truncate pl-2 text-[11px] text-dim">
                {r.kind === 'layout' ? 'layout · ' : ''}
                {basename(r.target)}
              </span>
            </div>
          ))}
        </Section>
        {!api.length && !pages.length && <div className="px-3 text-dim">nenhuma rota</div>}
      </div>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <div
        className="cursor-pointer px-3 py-1 text-[11px] font-semibold tracking-wide text-dim uppercase hover:text-fg"
        onClick={() => setOpen(!open)}
      >
        {open ? '▾' : '▸'} {title}
      </div>
      {open && children}
    </div>
  );
}
