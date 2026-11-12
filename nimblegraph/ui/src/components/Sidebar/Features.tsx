import { Waypoints, Tag } from 'lucide-react';
import { useStore } from '../../store';
import { Section } from './Routes';

const STATE_COLOR: Record<string, string> = { ok: '#4ec9b0', missing: '#8b8b8b' };

export function Features() {
  const graph = useStore((s) => s.graph);
  const { showFeature } = useStore.getState();
  const chains = graph?.chains ?? [];
  const scopes = graph?.scopes ?? [];
  return (
    <div className="flex-1 overflow-auto pb-4">
      <Section title={`Cadeias (${chains.length})`}>
        {chains.map((c) => (
          <div
            key={c.key}
            className="cursor-pointer px-3 py-1 hover:bg-hover"
            title={c.summary}
            onClick={() => void showFeature(c.title || c.key, c.files, c.key, c.summary)}
          >
            <div className="flex items-center gap-1.5">
              <Waypoints size={14} className="shrink-0 text-[#4fc1ff]" />
              <span className="truncate font-medium">{c.title || c.key}</span>
              <span className="ml-auto shrink-0 text-[10px]" style={{ color: STATE_COLOR[c.state] ?? '#cca700' }}>
                {c.state === 'ok' ? 'ok' : c.state.startsWith('stale') ? 'desatualizada' : c.state}
              </span>
            </div>
            <div className="truncate pl-5 text-[11px] text-dim">
              {c.key} · {c.files.length} arquivos
            </div>
          </div>
        ))}
        {!chains.length && (
          <div className="px-3 py-1 text-[12px] text-dim">
            Nenhuma ficha de cadeia. Use <span className="mono">nimblegraph describe --chain &lt;termo&gt;</span> ou o botão
            “Atualizar descrições com Claude”.
          </div>
        )}
      </Section>
      <Section title={`Escopos (${scopes.length})`}>
        {scopes.map((s) => (
          <div
            key={s.scope}
            className="flex h-[22px] cursor-pointer items-center gap-1.5 px-3 hover:bg-hover"
            onClick={() => void showFeature(s.scope, s.files, s.scope, `escopo · ${s.files.length} arquivos`)}
          >
            <Tag size={13} className="shrink-0 text-[#dcdcaa]" />
            <span className="truncate">{s.scope}</span>
            <span className="ml-auto text-[11px] text-dim">{s.files.length}</span>
          </div>
        ))}
        {!scopes.length && <div className="px-3 py-1 text-[12px] text-dim">As fichas ainda não têm escopo.</div>}
      </Section>
    </div>
  );
}
