import { LoaderCircle, Radio, Sparkles } from 'lucide-react';
import { useStore } from '../store';

export function StatusBar() {
  const graph = useStore((s) => s.graph);
  const live = useStore((s) => s.live);
  const ai = useStore((s) => s.ai);
  const toast = useStore((s) => s.toast);
  if (!graph) return <footer className="h-[22px] shrink-0 bg-accent" />;
  const built = graph.built_at ? new Date(graph.built_at * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—';
  const s = graph.stats;
  const sep = <span className="opacity-60">·</span>;
  return (
    <footer className="flex h-[22px] shrink-0 items-center gap-2 bg-accent px-2 text-[12px] whitespace-nowrap text-white">
      <span className="font-semibold">{graph.root}</span>
      {sep}
      <span title="motor de extração">{graph.engine ?? '?'}</span>
      {sep}
      <span>{s.files.toLocaleString('pt-BR')} arquivos</span>
      {sep}
      <span>{s.links.toLocaleString('pt-BR')} arestas</span>
      {sep}
      <span>{s.routes} rotas</span>
      {sep}
      <span title="arquivos com ficha / arquivos que deveriam ter">fichas {s.notes}/{s.describable}</span>
      {s.stale > 0 && (<>{sep}<span className="text-[#ffe08a]">{s.stale} desatualizadas</span></>)}
      {ai.status === 'running' && (
        <span className="flex items-center gap-1 rounded-sm bg-white/15 px-1.5">
          <LoaderCircle size={11} className="animate-spin" />
          <Sparkles size={11} /> {ai.engine === 'local' ? 'IA local' : 'Claude'} atualizando {ai.target ?? 'fichas'}
          {ai.progress ? ` (${ai.progress})` : '…'}
        </span>
      )}
      {toast && <span className="truncate rounded-sm bg-white/15 px-1.5">{toast}</span>}
      <span className="ml-auto">build {built}</span>
      {sep}
      <span className="flex items-center gap-1" title={live ? 'atualizando ao vivo' : 'desconectado'}>
        <Radio size={12} className={live ? 'text-[#89f0a8]' : 'opacity-50'} />
        {live ? 'ao vivo' : 'offline'}
      </span>
    </footer>
  );
}
