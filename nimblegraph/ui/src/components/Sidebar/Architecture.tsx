import { AlertTriangle, Boxes, CircleAlert, FlaskConical, Gauge, Info, Network, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, type ArchPayload, type Violation } from '../../api';
import { setOf } from '../../lib/chain';
import { basename, contextColor } from '../../lib/layers';
import { useStore } from '../../store';
import { Section } from './Routes';

type Tab = 'violations' | 'contexts' | 'metrics' | 'tests';

const SEV: Record<Violation['severity'], { color: string; icon: typeof Info; label: string }> = {
  error: { color: '#f48771', icon: CircleAlert, label: 'erro' },
  warn: { color: '#cca700', icon: AlertTriangle, label: 'aviso' },
  info: { color: '#75beff', icon: Info, label: 'info' },
};

const TABS: { tab: Tab; icon: typeof Info; label: string }[] = [
  { tab: 'violations', icon: CircleAlert, label: 'regras' },
  { tab: 'contexts', icon: Boxes, label: 'contextos' },
  { tab: 'metrics', icon: Gauge, label: 'métricas' },
  { tab: 'tests', icon: FlaskConical, label: 'testes' },
];

/** Architecture side bar: rule violations, bounded contexts, module metrics and structural test coverage. */
export function Architecture() {
  const graph = useStore((s) => s.graph);
  const [data, setData] = useState<ArchPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('violations');
  const [showKnown, setShowKnown] = useState(false);

  const reload = () => {
    setError(null);
    api.arch().then(setData, (e) => setError(String(e)));
  };
  useEffect(reload, [graph]);

  if (error) return <div className="px-3 py-2 text-[12px] text-dim">{error}</div>;
  if (!data) return <div className="px-3 py-2 text-[12px] text-dim">analisando a arquitetura…</div>;
  const n = data.summary.new;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1 px-2 pb-1 text-[11px]">
        {TABS.map((t) => (
          <button
            key={t.tab}
            onClick={() => setTab(t.tab)}
            className={`flex items-center gap-1 rounded-sm px-1.5 py-[2px] ${tab === t.tab ? 'bg-accent/30 text-fg' : 'text-dim hover:text-fg'}`}
          >
            <t.icon size={12} /> {t.label}
          </button>
        ))}
        <button className="ml-auto text-dim hover:text-fg" title="recalcular" onClick={reload}>
          <RefreshCw size={12} />
        </button>
      </div>
      <div className="shrink-0 px-3 pb-1 text-[11px] text-dim">
        {data.auto ? 'presets detectados' : 'rules.toml'}: {data.presets.join(', ')}
        <br />
        novas: <span style={{ color: SEV.error.color }}>{n.error} erros</span> ·{' '}
        <span style={{ color: SEV.warn.color }}>{n.warn} avisos</span> · {n.info} info · conhecidas{' '}
        {Object.values(data.summary.known).reduce((a, b) => a + b, 0)}
      </div>
      {tab === 'violations' && <Violations data={data} showKnown={showKnown} setShowKnown={setShowKnown} />}
      {tab === 'contexts' && <Contexts data={data} />}
      {tab === 'metrics' && <Metrics data={data} />}
      {tab === 'tests' && <Tests data={data} />}
    </div>
  );
}

function Violations({ data, showKnown, setShowKnown }: { data: ArchPayload; showKnown: boolean; setShowKnown: (b: boolean) => void }) {
  const index = useStore((s) => s.index);
  const { select, set } = useStore.getState();
  const groups = useMemo(() => {
    const out = new Map<string, Violation[]>();
    for (const v of data.violations) {
      if (!showKnown && v.state === 'known') continue;
      const list = out.get(v.rule) ?? [];
      list.push(v);
      out.set(v.rule, list);
    }
    return [...out.entries()];
  }, [data, showKnown]);

  const open = (v: Violation) => {
    if (!index) return;
    void select(v.src, { fit: false }).then(() => {
      const files = [v.src, ...(v.dst && index.nodes.has(v.dst) ? [v.dst] : [])];
      set({ pinned: setOf(index, files), fitTick: useStore.getState().fitTick + 1 });
    });
  };

  return (
    <div className="flex-1 overflow-auto pb-4">
      <label className="flex items-center gap-2 px-3 py-1 text-[11px] text-dim">
        <input type="checkbox" checked={showKnown} onChange={() => setShowKnown(!showKnown)} /> incluir as do baseline
      </label>
      {data.rule_errors.map((e) => (
        <div key={e} className="px-3 py-1 text-[11px] text-[#f48771]">regra inválida: {e}</div>
      ))}
      {!groups.length && <div className="px-3 py-1 text-[12px] text-dim">Nenhuma violação nova.</div>}
      {groups.map(([rule, items]) => {
        const sev = SEV[items[0].severity];
        return (
          <Section key={rule} title={`${rule} (${items.length})`}>
            <div className="px-3 pb-1 text-[11px] text-dim" title={items[0].message}>
              <sev.icon size={11} className="mr-1 inline" style={{ color: sev.color }} />
              {items[0].message}
              {items[0].adr && <span className="ml-1 text-[#75beff]">ADR {items[0].adr}</span>}
            </div>
            {items.slice(0, 200).map((v) => (
              <div
                key={v.key}
                className="cursor-pointer px-3 py-[2px] hover:bg-hover"
                title={`${v.src}:${v.line}${v.dst ? ' → ' + v.dst : ''}\n${v.message}`}
                onClick={() => open(v)}
              >
                <div className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: sev.color }} />
                  <span className="truncate">{basename(v.src)}</span>
                  {v.line > 0 && <span className="shrink-0 text-[10px] text-dim">:{v.line}</span>}
                  {v.state === 'known' && <span className="ml-auto shrink-0 text-[10px] text-dim">baseline</span>}
                </div>
                {v.dst && <div className="truncate pl-3 text-[11px] text-dim">→ {v.dst.startsWith('pkg:') ? v.dst.slice(4) : v.dst.includes(' ') || v.dst.startsWith('cycle:') ? v.dst : basename(v.dst)}</div>}
              </div>
            ))}
            {items.length > 200 && <div className="px-3 text-[11px] text-dim">+{items.length - 200} (nimblegraph check --rule {rule})</div>}
          </Section>
        );
      })}
    </div>
  );
}

function Contexts({ data }: { data: ArchPayload }) {
  const { showFeature, set } = useStore.getState();
  const outgoing = (c: string) => data.links.filter((l) => l.source === c);
  return (
    <div className="flex-1 overflow-auto pb-4">
      <div className="px-3 pb-1 text-[11px] text-dim">
        <button className="underline hover:text-fg" onClick={() => set({ colorBy: 'context' })}>colorir o grafo por contexto</button>
      </div>
      <Section title={`Contextos (${data.contexts.length})`}>
        {data.contexts.map((c) => {
          const outs = outgoing(c.name);
          const leaks = outs.reduce((a, l) => a + l.leaks.length, 0);
          return (
            <div
              key={c.name}
              className="cursor-pointer px-3 py-1 hover:bg-hover"
              title={c.root}
              onClick={() => void showFeature(c.name, c.files, undefined, `contexto (${c.how}) · ${c.files.length} arquivos`)}
            >
              <div className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: contextColor(c.name) }} />
                <span className="truncate font-medium">{c.name}</span>
                <span className="ml-auto shrink-0 text-[11px] text-dim">{c.files.length}</span>
              </div>
              <div className="truncate pl-4 text-[11px] text-dim">
                {Object.entries(c.roles).map(([r, k]) => `${r} ${k}`).join(' · ') || c.how}
              </div>
              {outs.length > 0 && (
                <div className="truncate pl-4 text-[11px] text-dim">
                  <Network size={10} className="mr-1 inline" />
                  {outs.map((l) => `${l.target} ${l.imports + l.http}`).join(', ')}
                  {leaks > 0 && <span className="ml-1 text-[#f48771]">{leaks} vazamentos</span>}
                </div>
              )}
            </div>
          );
        })}
      </Section>
      {data.aggregates.length > 0 && (
        <Section title={`Agregados (${data.aggregates.length})`}>
          {data.aggregates.map((ag) => (
            <div
              key={ag.file + ag.root}
              className="cursor-pointer px-3 py-[2px] hover:bg-hover"
              onClick={() => void showFeature(ag.root, [ag.file, ...ag.members], ag.file, `agregado · ${ag.context ?? '-'}`)}
            >
              <span className="font-medium">{ag.root}</span>
              <span className="ml-1 text-[11px] text-dim">{ag.members.map(basename).join(', ')}</span>
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

function Metrics({ data }: { data: ArchPayload }) {
  const { select } = useStore.getState();
  return (
    <div className="flex-1 overflow-auto pb-4 text-[12px]">
      <Section title={`Módulos por ${data.metrics.by}`}>
        <table className="w-full px-3 text-[11px]">
          <thead className="text-dim">
            <tr>
              <th className="pl-3 text-left font-normal">módulo</th>
              <th className="font-normal" title="arquivos">arq</th>
              <th className="font-normal" title="instabilidade Ce/(Ca+Ce)">I</th>
              <th className="font-normal" title="abstração">A</th>
              <th className="pr-3 font-normal" title="distância |A+I-1|">D</th>
            </tr>
          </thead>
          <tbody>
            {data.metrics.modules.map((m) => (
              <tr key={m.name} title={`Ca ${m.ca} · Ce ${m.ce} · ${m.loc} linhas${m.zone ? ' · zona ' + m.zone : ''}`}>
                <td className="truncate pl-3" style={{ maxWidth: 120 }}>{m.name}</td>
                <td className="text-center">{m.files}</td>
                <td className="text-center">{m.i.toFixed(2)}</td>
                <td className="text-center">{m.a.toFixed(2)}</td>
                <td className="pr-3 text-center" style={{ color: m.zone ? '#f48771' : undefined }}>
                  {m.d.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="Arquivos “deus” (importadores × dependências)">
        {data.gods.map((g) => (
          <div key={g.file} className="flex cursor-pointer items-center gap-1.5 px-3 py-[2px] hover:bg-hover" title={g.file}
               onClick={() => void select(g.file)}>
            <span className="truncate">{basename(g.file)}</span>
            <span className="ml-auto shrink-0 text-[11px] text-dim">↓{g.importers} ↑{g.deps} · {g.loc}</span>
          </div>
        ))}
      </Section>
    </div>
  );
}

function Tests({ data }: { data: ArchPayload }) {
  const { showEndpoint, select } = useStore.getState();
  const untested = data.tests.endpoints.filter((e) => !e.e2e.length && !e.unit.length);
  return (
    <div className="flex-1 overflow-auto pb-4">
      <div className="px-3 pb-1 text-[11px] text-dim">
        {data.tests.count} testes · {data.tests.endpoints.length - untested.length}/{data.tests.endpoints.length} endpoints
        com teste
      </div>
      <Section title={`Endpoints sem teste (${untested.length})`}>
        {untested.map((e) => (
          <div key={e.label} className="cursor-pointer truncate px-3 py-[2px] mono text-[11px] hover:bg-hover"
               onClick={() => void showEndpoint(e.label)}>
            {e.label}
          </div>
        ))}
      </Section>
      {Object.entries(data.tests.untested_roles).filter(([, l]) => l.length).map(([role, items]) => (
        <Section key={role} title={`${role} sem teste (${items.length})`}>
          {items.slice(0, 100).map((it) => (
            <div key={it.file + it.symbol} className="cursor-pointer truncate px-3 py-[2px] hover:bg-hover" title={it.file}
                 onClick={() => void select(it.file)}>
              {it.symbol}
            </div>
          ))}
        </Section>
      ))}
      <Section title="Alcance dos testes por contexto">
        {Object.entries(data.tests.contexts).map(([c, v]) => (
          <div key={c} className="flex items-center gap-2 px-3 py-[2px] text-[11px]">
            <span className="w-24 truncate">{c}</span>
            <div className="h-1.5 flex-1 rounded bg-line">
              <div className="h-1.5 rounded bg-[#4ec9b0]" style={{ width: `${v.files ? (100 * v.reached) / v.files : 0}%` }} />
            </div>
            <span className="w-14 text-right text-dim">{v.reached}/{v.files}</span>
          </div>
        ))}
      </Section>
    </div>
  );
}
