import { Check, Copy, Cpu, LoaderCircle, RefreshCw, Sparkles, TriangleAlert, X } from 'lucide-react';
import { useState } from 'react';
import { api, type EndpointPayload, type FileInfo } from '../api';
import { METHOD_COLORS, basename, layerStyle } from '../lib/layers';
import { useStore } from '../store';
import { ChainFlow } from './ChainFlow';

function H({ children }: { children: React.ReactNode }) {
  return <div className="mt-3 mb-1 text-[11px] font-semibold tracking-wide text-dim uppercase">{children}</div>;
}

function FileLink({ path, extra }: { path: string; extra?: string }) {
  const graph = useStore((s) => s.graph);
  const { select, setHovered } = useStore.getState();
  const node = graph?.nodes.find((n) => n.id === path);
  const st = layerStyle(node?.layer);
  const Icon = st.icon;
  return (
    <div
      className="flex cursor-pointer items-center gap-1.5 rounded-sm py-[1px] hover:bg-hover"
      onClick={() => void select(path)}
      onMouseEnter={() => setHovered(path)}
      onMouseLeave={() => setHovered(null)}
      title={path}
    >
      <Icon size={13} style={{ color: st.color }} className="shrink-0" />
      <span className="shrink-0">{basename(path)}</span>
      {node?.stale && <span className="text-warn">*</span>}
      <span className="truncate text-[11px] text-dim">{extra ?? node?.note?.title ?? ''}</span>
    </div>
  );
}

/** "Atualizar" + "Atualizar descrições com Claude" (or copy the slash command when Claude Code is not installed). */
function UpdateButtons({ target }: { target: string }) {
  const graph = useStore((s) => s.graph);
  const ai = useStore((s) => s.ai);
  const { notify, set } = useStore.getState();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const running = ai.status === 'running';
  const update = async (withAi: boolean, local = false) => {
    setBusy(true);
    try {
      const r = await api.update(target, withAi, local);
      if (r.error) throw new Error(r.error);
      setResult(r.to_describe.length ? `${r.to_describe.length} arquivos para descrever` : 'fichas em dia');
      if ((withAi || local) && r.ai) {
        if (r.ai.started) set({ ai: { status: 'running', target, lines: [], engine: local ? 'local' : 'claude' } });
        notify(r.ai.message);
      }
    } catch (e) {
      notify(String(e));
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    await navigator.clipboard.writeText(`/nimblegraph update ${target}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const btn = 'flex items-center gap-1.5 rounded-sm px-2 py-1 text-[12px] disabled:opacity-50';
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button className={`${btn} border border-line hover:bg-hover`} disabled={busy} onClick={() => void update(false)}>
        <RefreshCw size={13} className={busy ? 'animate-spin' : ''} /> Atualizar
      </button>
      {graph?.claude ? (
        <button className={`${btn} bg-accent text-white hover:brightness-110`} disabled={busy || running} onClick={() => void update(true)}>
          {running ? <LoaderCircle size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {running ? 'Claude escrevendo…' : 'Atualizar descrições com Claude'}
        </button>
      ) : (
        <button className={`${btn} border border-line hover:bg-hover`} onClick={() => void copy()} title="Claude Code não encontrado: cole numa sessão">
          {copied ? <Check size={13} /> : <Copy size={13} />} Copiar /nimblegraph update
        </button>
      )}
      {graph?.local && (
        <button className={`${btn} border border-accent text-fg hover:bg-accent/20`} disabled={busy || running}
                onClick={() => void update(false, true)} title="Modelo local do Hugging Face (Ollama), sem nuvem">
          {running && ai.engine === 'local' ? <LoaderCircle size={13} className="animate-spin" /> : <Cpu size={13} />}
          Descrever com IA local
        </button>
      )}
      {result && <span className="text-[11px] text-dim">{result}</span>}
    </div>
  );
}

function FileDetail({ file }: { file: FileInfo }) {
  const detail = useStore((s) => s.detail);
  const st = layerStyle(file.layer);
  const Icon = st.icon;
  const note = file.note;
  const chain = detail?.kind === 'file' ? detail.chain : null;
  return (
    <>
      <div className="flex items-start gap-2">
        <Icon size={18} style={{ color: st.color }} className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="truncate text-[14px] font-semibold">{basename(file.path)}</div>
          <div className="truncate text-[11px] text-dim" title={file.path}>{file.path}</div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className="rounded-sm px-1.5 py-[1px]" style={{ background: st.color + '33', color: st.color }}>● {st.label}</span>
        {file.entry && <span className="rounded-sm bg-[#ffd16633] px-1.5 py-[1px] text-[#ffd166]">entrada</span>}
        <span className="text-dim">{file.loc} linhas · ~{file.tokens.toLocaleString('pt-BR')} tokens</span>
      </div>
      {file.stale && (
        <div className="mt-2 flex items-center gap-1.5 rounded-sm border border-warn/40 bg-warn/10 px-2 py-1 text-[12px] text-warn">
          <TriangleAlert size={13} /> O arquivo mudou depois da ficha: ficha desatualizada.
        </div>
      )}
      {note ? (
        <div className="mt-2">
          <div className="text-[13px] font-medium">{note.title}</div>
          {note.summary && <div className="mt-1">{note.summary}</div>}
          <dl className="mt-1 grid grid-cols-[70px_1fr] gap-x-2 gap-y-0.5 text-[12px]">
            {note.resp && (<><dt className="text-dim">resp.</dt><dd>{note.resp}</dd></>)}
            {note.scope && (<><dt className="text-dim">escopo</dt><dd>{note.scope}</dd></>)}
            {note.flow && (<><dt className="text-dim">fluxo</dt><dd className="mono text-[11px] text-[#9cdcfe]">{note.flow}</dd></>)}
          </dl>
          {!!note.tags?.length && (
            <div className="mt-1 flex flex-wrap gap-1">
              {note.tags.map((t) => <span key={t} className="rounded-sm bg-hover px-1.5 text-[11px]">#{t}</span>)}
            </div>
          )}
        </div>
      ) : (
        <div className="mt-2 text-dim italic">Sem ficha ainda.</div>
      )}
      <UpdateButtons target={file.path} />
      {!!file.routes.length && (
        <>
          <H>Rotas</H>
          {file.routes.map((r) => (
            <div key={r.id} className="mono text-[12px]">
              <span style={{ color: METHOD_COLORS[r.method] }}>{r.method}</span> {r.path}
              {r.handler && r.handler !== 'default' && <span className="text-dim"> #{r.handler}</span>}
            </div>
          ))}
        </>
      )}
      {!!file.calls.length && (
        <>
          <H>Chamadas de API</H>
          {file.calls.map((c, i) => (
            <div key={i} className="mono text-[12px]">
              <span style={{ color: METHOD_COLORS[c.method] }}>{c.method}</span> {c.url}
              {c.target ? <span className="text-dim"> → {basename(c.target)}</span> : <span className="text-dim"> (sem rota)</span>}
            </div>
          ))}
        </>
      )}
      {chain && chain.flow.length > 1 && (
        <>
          <H>Cadeia</H>
          <ChainFlow chain={chain} />
        </>
      )}
      {!!file.exports.length && (
        <>
          <H>Exports</H>
          <div className="mono flex flex-wrap gap-x-2 text-[12px]">
            {file.exports.map((e) => <span key={e.name} title={`${e.kind}:${e.line}`}>{e.name}</span>)}
          </div>
        </>
      )}
      {!!file.imports.length && (
        <>
          <H>Importa ({file.imports.length})</H>
          {file.imports.map((i) => <FileLink key={i.path + i.kind} path={i.path} extra={i.kind !== 'import' ? i.kind : undefined} />)}
        </>
      )}
      {!!file.importers.length && (
        <>
          <H>É importado por ({file.importers.length})</H>
          {file.importers.map((i) => <FileLink key={i.path} path={i.path} />)}
        </>
      )}
      {!!file.callers.length && (
        <>
          <H>Chamado por</H>
          {file.callers.map((c, i) => <FileLink key={i} path={c.path} extra={c.route} />)}
        </>
      )}
      {!!file.externals.length && (
        <>
          <H>Pacotes</H>
          <div className="text-[12px] text-dim">{file.externals.join(', ')}</div>
        </>
      )}
    </>
  );
}

const STEP_LABEL: Record<string, string> = {
  mount: 'montagem', middleware: 'middleware', validation: 'validação', handler: 'handler', down: 'usa',
};

function EndpointDetail({ ep }: { ep: EndpointPayload }) {
  const { showEndpoint, select } = useStore.getState();
  if (ep.matches) {
    return (
      <>
        <div className="text-[14px] font-semibold">{ep.matches.length} rotas</div>
        {ep.matches.map((r) => (
          <div key={r.id} className="mono cursor-pointer text-[12px] hover:bg-hover" onClick={() => void showEndpoint(`${r.method} ${r.path}`)}>
            <span style={{ color: METHOD_COLORS[r.method] }}>{r.method}</span> {r.path}
          </div>
        ))}
      </>
    );
  }
  const r = ep.route!;
  let n = 0;
  const seen = new Map<string, number>();
  return (
    <>
      <div className="mono text-[15px] font-semibold">
        <span style={{ color: METHOD_COLORS[r.method] }}>{r.method}</span> {r.path}
      </div>
      <div className="text-[11px] text-dim">{ep.files?.length ?? 0} arquivos, na ordem em que a requisição passa</div>
      <UpdateButtons target={`${r.method} ${r.path}`} />
      <H>Etapas</H>
      {(ep.steps ?? []).map((s, i) => {
        let num: number | undefined;
        if (s.file) {
          if (!seen.has(s.file)) seen.set(s.file, ++n);
          num = seen.get(s.file);
        }
        return (
          <div
            key={i}
            className={`flex items-baseline gap-1.5 rounded-sm py-[1px] ${s.file && !s.external ? 'cursor-pointer hover:bg-hover' : ''}`}
            style={{ paddingLeft: s.step === 'down' ? 8 + (s.depth ?? 0) * 12 : 0 }}
            onClick={() => s.file && !s.external && void select(s.file, { fit: false })}
          >
            <span className="w-[18px] shrink-0 text-right text-[10px] text-accent">{num ?? ''}</span>
            <span className="w-[74px] shrink-0 text-[10px] text-dim uppercase">{s.step === 'down' ? '' : s.scope && s.step === 'middleware' ? s.scope : STEP_LABEL[s.step]}</span>
            <span className="min-w-0">
              <span className="mono text-[12px]">
                {s.step === 'mount' ? `${basename(s.file ?? '')} ${s.prefix ?? ''}` : s.external ? s.symbol : `${basename(s.file ?? '')}${s.symbol && s.symbol !== 'default' ? '#' + s.symbol : ''}`}
              </span>
              {s.title && <span className="text-[11px] text-dim"> — {s.title}</span>}
            </span>
          </div>
        );
      })}
      {!!ep.front?.length && (
        <>
          <H>Front</H>
          {ep.front.map((path, i) => (
            <div key={i} className="mono text-[11px]">{path.map((p) => basename(p.file) + (p.symbol ? '#' + p.symbol : '')).join(' › ')}</div>
          ))}
        </>
      )}
      {!!ep.shared?.length && (<><H>Compartilhados</H><div className="text-[12px] text-dim">{ep.shared.map(basename).join(', ')}</div></>)}
      {!!ep.tests?.length && (<><H>Testes</H>{ep.tests.map((t) => <FileLink key={t} path={t} />)}</>)}
      {!!ep.externals?.length && (<><H>Pacotes</H><div className="text-[12px] text-dim">{ep.externals.join(', ')}</div></>)}
    </>
  );
}

export function DetailPanel() {
  const detail = useStore((s) => s.detail);
  const loading = useStore((s) => s.detailLoading);
  const { clear } = useStore.getState();
  if (!detail) return null;
  return (
    <aside className="relative flex w-[340px] shrink-0 flex-col border-l border-line bg-panel">
      <div className="flex h-[35px] shrink-0 items-center px-3 text-[11px] tracking-wide text-dim uppercase">
        {detail.kind === 'file' ? 'Arquivo' : detail.kind === 'endpoint' ? 'Endpoint' : 'Funcionalidade'}
        {loading && <LoaderCircle size={12} className="ml-2 animate-spin" />}
        <button className="ml-auto rounded-sm p-0.5 hover:bg-hover" onClick={clear} title="Fechar (Esc)">
          <X size={14} />
        </button>
      </div>
      <div className="flex-1 overflow-auto px-3 pb-6">
        {detail.kind === 'file' && <FileDetail file={detail.file} />}
        {detail.kind === 'endpoint' && <EndpointDetail ep={detail.endpoint} />}
        {detail.kind === 'feature' && (
          <>
            <div className="text-[14px] font-semibold">{detail.title}</div>
            {detail.subtitle && <div className="mt-1 text-dim">{detail.subtitle}</div>}
            <UpdateButtons target={detail.chain?.target ?? detail.title} />
            <H>Cadeia ({detail.files.length} arquivos)</H>
            {detail.chain ? <ChainFlow chain={detail.chain} /> : detail.files.map((f) => <FileLink key={f} path={f} />)}
          </>
        )}
      </div>
    </aside>
  );
}
