import { ChevronDown, ChevronRight, Folder, FolderOpen } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { GNode } from '../../api';
import { layerStyle } from '../../lib/layers';
import { useStore } from '../../store';

interface Dir {
  name: string;
  path: string;
  dirs: Map<string, Dir>;
  files: GNode[];
}

function buildTree(nodes: GNode[]): Dir {
  const root: Dir = { name: '', path: '', dirs: new Map(), files: [] };
  for (const n of nodes) {
    const parts = n.id.split('/');
    let cur = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const path = parts.slice(0, i + 1).join('/');
      if (!cur.dirs.has(parts[i])) cur.dirs.set(parts[i], { name: parts[i], path, dirs: new Map(), files: [] });
      cur = cur.dirs.get(parts[i])!;
    }
    cur.files.push(n);
  }
  return root;
}

/** Collapses chains of single-child folders: src/components/ui -> one row (like VS Code compact folders). */
function compact(d: Dir): Dir {
  let cur = d;
  let name = d.name;
  while (cur.files.length === 0 && cur.dirs.size === 1 && cur.path) {
    const only = [...cur.dirs.values()][0];
    name = `${name}/${only.name}`;
    cur = only;
  }
  return name === d.name ? d : { ...cur, name };
}

export function Explorer() {
  const graph = useStore((s) => s.graph);
  const selected = useStore((s) => s.selected);
  const revealTick = useStore((s) => s.revealTick);
  const tree = useMemo(() => buildTree(graph?.nodes ?? []), [graph]);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const box = useRef<HTMLDivElement>(null);

  // first level open by default
  useEffect(() => {
    setOpen((o) => (o.size ? o : new Set([...tree.dirs.values()].map((d) => compact(d).path))));
  }, [tree]);

  // reveal in explorer
  useEffect(() => {
    if (!selected) return;
    setOpen((o) => {
      const n = new Set(o);
      const parts = selected.split('/');
      for (let i = 1; i < parts.length; i++) n.add(parts.slice(0, i).join('/'));
      return n;
    });
    const t = setTimeout(() => {
      box.current?.querySelector(`[data-path="${CSS.escape(selected)}"]`)?.scrollIntoView({ block: 'center' });
    }, 50);
    return () => clearTimeout(t);
  }, [revealTick, selected]);

  const toggle = (p: string) =>
    setOpen((o) => {
      const n = new Set(o);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });

  return (
    <div ref={box} className="flex-1 overflow-auto pb-4 select-none">
      <DirRows dir={tree} depth={0} open={open} toggle={toggle} selected={selected} />
    </div>
  );
}

function DirRows(props: { dir: Dir; depth: number; open: Set<string>; toggle: (p: string) => void; selected: string | null }) {
  const { dir, depth, open, toggle, selected } = props;
  const { select, setHovered } = useStore.getState();
  const dirs = [...dir.dirs.values()].map(compact).sort((a, b) => a.name.localeCompare(b.name));
  const files = [...dir.files].sort((a, b) => a.id.localeCompare(b.id));
  return (
    <>
      {dirs.map((d) => {
        const isOpen = open.has(d.path);
        return (
          <div key={d.path}>
            <div
              className="flex h-[22px] cursor-pointer items-center gap-1 pr-2 hover:bg-hover"
              style={{ paddingLeft: 6 + depth * 10 }}
              onClick={() => toggle(d.path)}
            >
              {isOpen ? <ChevronDown size={14} className="shrink-0 text-dim" /> : <ChevronRight size={14} className="shrink-0 text-dim" />}
              {isOpen ? <FolderOpen size={14} className="shrink-0 text-[#dcb67a]" /> : <Folder size={14} className="shrink-0 text-[#dcb67a]" />}
              <span className="truncate">{d.name}</span>
            </div>
            {isOpen && <DirRows dir={d} depth={depth + 1} open={open} toggle={toggle} selected={selected} />}
          </div>
        );
      })}
      {files.map((f) => {
        const st = layerStyle(f.layer);
        const Icon = st.icon;
        const name = f.id.slice(f.id.lastIndexOf('/') + 1);
        return (
          <div
            key={f.id}
            data-path={f.id}
            title={`${f.id}\n${st.label}${f.note ? ' — ' + f.note.title : ' — sem ficha'}`}
            className={`flex h-[22px] cursor-pointer items-center gap-1.5 pr-2 ${selected === f.id ? 'bg-sel' : 'hover:bg-hover'}`}
            style={{ paddingLeft: 20 + depth * 10 }}
            onClick={() => void select(f.id)}
            onMouseEnter={() => setHovered(f.id)}
            onMouseLeave={() => setHovered(null)}
          >
            <Icon size={14} style={{ color: st.color }} className="shrink-0" />
            <span className="truncate">{name}</span>
            <span className="ml-auto flex shrink-0 items-center gap-1">
              {f.stale && <span className="font-bold text-warn" title="ficha desatualizada">*</span>}
              {!f.note && !['test', 'config'].includes(f.layer) && (
                <span className="h-1.5 w-1.5 rounded-full bg-dim/70" title="sem ficha" />
              )}
            </span>
          </div>
        );
      })}
    </>
  );
}
