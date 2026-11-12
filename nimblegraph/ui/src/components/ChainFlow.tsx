import { CornerDownRight, Globe } from 'lucide-react';
import type { ChainPayload } from '../api';
import { basename, layerStyle } from '../lib/layers';
import { useStore } from '../store';

/** The chain as a vertical list: page > component > hook > API > controller > service > model. */
export function ChainFlow({ chain, max = 80 }: { chain: ChainPayload; max?: number }) {
  const { select, setHovered } = useStore.getState();
  const steps = chain.flow.slice(0, max);
  return (
    <div>
      {chain.chain_note && (
        <div className="mb-2 rounded-sm border border-line bg-bg/60 p-2">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{chain.chain_note.title}</span>
            <span className={`ml-auto text-[10px] ${chain.chain_note.state === 'ok' ? 'text-[#4ec9b0]' : 'text-warn'}`}>
              {chain.chain_note.state === 'ok' ? 'ficha ok' : 'ficha de cadeia ' + chain.chain_note.state}
            </span>
          </div>
          {chain.chain_note.summary && <div className="mt-1 text-dim">{chain.chain_note.summary}</div>}
          {chain.chain_note.flow && <div className="mono mt-1 text-[11px] text-[#9cdcfe]">{chain.chain_note.flow}</div>}
        </div>
      )}
      {steps.map((s, i) => {
        const st = layerStyle(s.layer);
        const Icon = st.icon;
        return (
          <div key={i}>
            {s.label && (
              <div className="mono flex items-center gap-1 text-[11px] text-[#ff9800]" style={{ paddingLeft: 4 + s.depth * 12 }}>
                <Globe size={11} /> {s.label}
              </div>
            )}
            <div
              className="flex cursor-pointer items-center gap-1.5 rounded-sm py-[2px] hover:bg-hover"
              style={{ paddingLeft: 4 + s.depth * 12 }}
              onClick={() => void select(s.path)}
              onMouseEnter={() => setHovered(s.path)}
              onMouseLeave={() => setHovered(null)}
              title={s.path}
            >
              {s.depth > 0 && <CornerDownRight size={11} className="shrink-0 text-dim" />}
              <Icon size={13} style={{ color: st.color }} className="shrink-0" />
              <span className={`shrink-0 ${s.ref ? 'text-dim italic' : ''}`}>{s.ref ? '↑ ' : ''}{basename(s.path)}</span>
              {s.stale && <span className="text-warn">*</span>}
              {s.title && !s.ref && <span className="truncate text-[11px] text-dim">— {s.title}</span>}
            </div>
          </div>
        );
      })}
      {chain.flow.length > max && <div className="text-dim">+{chain.flow.length - max} …</div>}
      {!!chain.shared.length && (
        <div className="mt-2 text-[11px] text-dim">
          <span className="font-semibold">compartilhados:</span> {chain.shared.map(basename).join(', ')}
        </div>
      )}
      {!!chain.missing.length && (
        <div className="mt-1 text-[11px] text-dim">
          <span className="font-semibold">sem ficha:</span> {chain.missing.length} arquivos
        </div>
      )}
    </div>
  );
}
