import { useEffect, useMemo, useRef, useState } from 'react';
import { basename, dirname, layerStyle } from '../lib/layers';
import { useStore } from '../store';

/** Subsequence match with a bonus for matches in the file name (like VS Code Ctrl+P). */
function score(path: string, q: string): number {
  if (!q) return 1;
  const p = path.toLowerCase();
  const name = basename(p);
  if (name.startsWith(q)) return 1000 - name.length;
  if (name.includes(q)) return 800 - name.length;
  let i = 0;
  let s = 0;
  for (const ch of p) {
    if (ch === q[i]) {
      i++;
      s += 2;
      if (i === q.length) break;
    }
  }
  return i === q.length ? s - p.length / 10 : -1;
}

export function QuickOpen() {
  const open = useStore((s) => s.quickOpen);
  const graph = useStore((s) => s.graph);
  const { set, select } = useStore.getState();
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ('');
      setIdx(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const results = useMemo(() => {
    const qq = q.toLowerCase().trim();
    return (graph?.nodes ?? [])
      .map((n) => ({ n, s: score(n.id, qq) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, 40)
      .map((x) => x.n);
  }, [graph, q]);

  if (!open) return null;
  const close = () => set({ quickOpen: false });
  const choose = (id?: string) => {
    if (id) void select(id);
    close();
  };
  return (
    <div className="fixed inset-0 z-50 flex justify-center bg-black/20 pt-[10vh]" onClick={close}>
      <div className="h-fit w-[600px] max-w-[90vw] overflow-hidden rounded-md border border-line bg-side shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <input
          ref={input}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setIdx(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') setIdx((i) => Math.min(i + 1, results.length - 1));
            else if (e.key === 'ArrowUp') setIdx((i) => Math.max(i - 1, 0));
            else if (e.key === 'Enter') choose(results[idx]?.id);
            else if (e.key === 'Escape') close();
            else return;
            e.preventDefault();
          }}
          placeholder="ir para arquivo…"
          className="w-full border-b border-line bg-bg px-3 py-2 outline-none"
        />
        <div className="max-h-[50vh] overflow-auto py-1">
          {results.map((n, i) => {
            const st = layerStyle(n.layer);
            const Icon = st.icon;
            return (
              <div
                key={n.id}
                className={`flex cursor-pointer items-center gap-2 px-3 py-[3px] ${i === idx ? 'bg-sel' : 'hover:bg-hover'}`}
                onMouseEnter={() => setIdx(i)}
                onClick={() => choose(n.id)}
              >
                <Icon size={14} style={{ color: st.color }} className="shrink-0" />
                <span className="shrink-0">{basename(n.id)}</span>
                <span className="truncate text-[11px] text-dim">{dirname(n.id)}</span>
                {n.note && <span className="ml-auto max-w-[45%] truncate pl-2 text-[11px] text-dim">{n.note.title}</span>}
              </div>
            );
          })}
          {!results.length && <div className="px-3 py-2 text-dim">nenhum arquivo</div>}
        </div>
      </div>
    </div>
  );
}
