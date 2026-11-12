import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D, { type ForceGraphMethods } from 'react-force-graph-2d';
import type { GLink, GNode } from '../api';
import { linkId, linkKey } from '../lib/chain';
import { API_LINK_COLOR, basename, hexAlpha, nodeColor } from '../lib/layers';
import { useStore } from '../store';

const Graph3D = lazy(() => import('./Graph3D'));

type FGNode = GNode & { x?: number; y?: number; vx?: number; vy?: number };
type FGLink = GLink & { __key?: string; __api?: boolean };

export const PARTICLE_LIMIT = 2000; // above this many nodes, idle particles are switched off

export function radius(n: GNode): number {
  return 3 + Math.min(9, Math.sqrt(n.importers) * 1.6);
}

/** Graph data filtered by the toolbar; node objects are reused so the layout does not restart on reload. */
function useVisibleData() {
  const graph = useStore((s) => s.graph);
  const filters = useStore((s) => s.filters);
  const cache = useRef(new Map<string, FGNode>());
  return useMemo(() => {
    if (!graph) return { nodes: [] as FGNode[], links: [] as FGLink[] };
    const hidden = new Set(filters.hiddenLayers);
    if (filters.hideTests) hidden.add('test');
    if (filters.hideConfig) hidden.add('config');
    if (filters.hideTypes) hidden.add('type');
    const nodes: FGNode[] = [];
    const next = new Map<string, FGNode>();
    for (const n of graph.nodes) {
      if (hidden.has(n.layer)) continue;
      const prev = cache.current.get(n.id);
      const node: FGNode = prev ? Object.assign(prev, n) : { ...n };
      next.set(n.id, node);
      nodes.push(node);
    }
    cache.current = next;
    const links: FGLink[] = [];
    for (const l of graph.links) {
      const [s, t] = linkId(l);
      if (!next.has(s) || !next.has(t)) continue;
      const kinds = l.kinds;
      if (!filters.typeEdges && kinds.length === 1 && kinds[0] === 'type') continue;
      const api = kinds.includes('calls_api');
      if (!filters.apiEdges && api && kinds.length === 1) continue;
      links.push({ source: s, target: t, kinds, __key: linkKey(s, t), __api: api });
    }
    return { nodes, links };
  }, [graph, filters]);
}

export function GraphCanvas() {
  const data = useVisibleData();
  const pinned = useStore((s) => s.pinned);
  const hover = useStore((s) => s.hover);
  const selected = useStore((s) => s.selected);
  const hovered = useStore((s) => s.hovered);
  const fitTick = useStore((s) => s.fitTick);
  const colorBy = useStore((s) => s.colorBy);
  const filters = useStore((s) => s.filters);
  const mode3d = useStore((s) => s.mode3d);
  const theme = useStore((s) => s.theme);
  const { setHovered, select, clear } = useStore.getState();
  const fg = useRef<ForceGraphMethods<FGNode, FGLink> | undefined>(undefined);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const running = useRef(true); // force simulation still moving nodes
  const pendingFit = useRef(true);
  const ticks = useRef(0);
  const touched = useRef(false); // user zoomed/panned: stop auto-framing

  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const f = fg.current;
    if (!f) return;
    f.d3Force('charge')?.strength?.(-90);
    f.d3Force('link')?.distance?.(48);
    ticks.current = 0;
    running.current = true;
  }, [data]);

  // frame the graph while the first layout spreads (until the user takes over with the wheel / drag)
  useEffect(() => {
    const el = box.current;
    const stop = () => {
      touched.current = true;
    };
    el?.addEventListener('wheel', stop, { passive: true });
    el?.addEventListener('pointerdown', stop);
    const fit = () => {
      if (touched.current || !fg.current) return;
      const set = useStore.getState().pinned?.nodes;
      fg.current.zoomToFit(0, 60, set ? (n: FGNode) => set.has(n.id) : undefined);
    };
    const timers = [600, 1500, 3000].map((ms) => setTimeout(fit, ms));
    return () => {
      timers.forEach(clearTimeout);
      el?.removeEventListener('wheel', stop);
      el?.removeEventListener('pointerdown', stop);
    };
  }, [data.nodes.length]);

  // zoom to the pinned chain after a click / route / feature selection (after the layout settles)
  useEffect(() => {
    if (!fitTick || !fg.current) return;
    if (running.current) {
      pendingFit.current = true;
      return;
    }
    const set = useStore.getState().pinned?.nodes;
    const t = setTimeout(() => fg.current?.zoomToFit(700, 80, set ? (n: FGNode) => set.has(n.id) : undefined), 60);
    return () => clearTimeout(t);
  }, [fitTick]);

  const active = hover ?? pinned;
  const big = data.nodes.length > PARTICLE_LIMIT;
  const light = theme === 'light';
  const idleLink = light ? '#00000026' : '#ffffff1f';
  const label = light ? '#333333' : '#d4d4d4';
  // dense graphs show names only when zoomed in (hubs a little earlier)
  const labelZoom = data.nodes.length > 400 ? 3.2 : data.nodes.length > 120 ? 2.4 : 1.4;

  const nodeCanvasObject = useCallback(
    (node: FGNode, ctx: CanvasRenderingContext2D, scale: number) => {
      const st = { color: nodeColor(node, colorBy) };
      const r = radius(node);
      const lit = !active || active.nodes.has(node.id);
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      ctx.save();
      if (active && lit) {
        ctx.shadowColor = st.color;
        ctx.shadowBlur = node.id === active.focus || node.id === selected ? 24 : 12;
      }
      ctx.beginPath();
      ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = hexAlpha(st.color, lit ? 1 : 0.12);
      ctx.fill();
      ctx.shadowBlur = 0;
      if (node.stale && lit) {
        ctx.lineWidth = 1.5 / scale;
        ctx.strokeStyle = '#cca700';
        ctx.stroke();
      }
      if (node.id === selected || node.id === hovered) {
        ctx.beginPath();
        ctx.arc(x, y, r + 2.5, 0, 2 * Math.PI);
        ctx.lineWidth = 1.5 / scale;
        ctx.strokeStyle = light ? '#000000aa' : '#ffffffcc';
        ctx.stroke();
      }
      const order = active?.order?.get(node.id);
      if (order !== undefined) {
        const br = 6 / Math.sqrt(scale) + 2;
        ctx.beginPath();
        ctx.arc(x + r, y - r, br, 0, 2 * Math.PI);
        ctx.fillStyle = '#007acc';
        ctx.fill();
        ctx.fillStyle = '#ffffff';
        ctx.font = `bold ${br * 1.2}px Segoe UI, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(order), x + r, y - r + 0.3);
      }
      const showLabel =
        scale > labelZoom || (node.importers >= 12 && scale > labelZoom * 0.6) || node.id === hovered || node.id === selected ||
        (active && lit && (active.nodes.size <= 25 || active.order?.has(node.id) || node.importers >= 8));
      if (showLabel && lit) {
        const fs = Math.max(10 / scale, 1.6);
        ctx.font = `${fs}px Segoe UI, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = label;
        ctx.fillText(basename(node.id), x, y + r + 1.5);
        if (node.note?.title && (scale > 2.6 || node.id === hovered)) {
          ctx.font = `${fs * 0.85}px Segoe UI, sans-serif`;
          ctx.fillStyle = hexAlpha(st.color, 0.9);
          ctx.fillText(node.note.title, x, y + r + 2.5 + fs);
        }
      }
      ctx.restore();
    },
    [active, selected, hovered, light, label, labelZoom, colorBy],
  );

  const pointerArea = useCallback((node: FGNode, color: string, ctx: CanvasRenderingContext2D) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x ?? 0, node.y ?? 0, radius(node) + 2, 0, 2 * Math.PI);
    ctx.fill();
  }, []);

  const litLink = useCallback((l: FGLink) => !!active && active.links.has(l.__key ?? ''), [active]);
  const srcColor = (l: FGLink) =>
    typeof l.source === 'object' ? nodeColor(l.source as GNode, colorBy) : nodeColor({ layer: 'module' }, 'layer');

  const linkColor = useCallback(
    (l: FGLink) => {
      if (active) {
        if (!litLink(l)) return light ? '#00000008' : '#ffffff08';
        return l.__api ? API_LINK_COLOR : hexAlpha(srcColor(l), 0.85);
      }
      return l.__api ? hexAlpha(API_LINK_COLOR, 0.45) : idleLink;
    },
    [active, litLink, light, idleLink],
  );
  const linkWidth = useCallback((l: FGLink) => (litLink(l) ? 1.8 : 0.6), [litLink]);
  const particles = useCallback(
    (l: FGLink) => {
      if (!filters.particles) return 0;
      if (active) return litLink(l) ? 4 : 0;
      return big ? 0 : 1;
    },
    [active, litLink, filters.particles, big],
  );
  const particleSpeed = useCallback((l: FGLink) => (litLink(l) ? 0.014 : 0.0035), [litLink]);
  const particleWidth = useCallback((l: FGLink) => (litLink(l) ? 3 : 1.3), [litLink]);
  const particleColor = useCallback(
    (l: FGLink) => (l.__api ? API_LINK_COLOR : litLink(l) ? (light ? '#000000' : '#ffffff') : light ? '#00000055' : '#ffffff70'),
    [litLink, light],
  );

  return (
    <div ref={box} className="absolute inset-0" onMouseLeave={() => setHovered(null)}>
      {mode3d ? (
        <Suspense fallback={<div className="p-4 text-dim">carregando 3D…</div>}>
          <Graph3D data={data} width={size.w} height={size.h} />
        </Suspense>
      ) : (
        <ForceGraph2D<FGNode, FGLink>
          ref={fg}
          width={size.w}
          height={size.h}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          nodeId="id"
          nodeRelSize={1}
          nodeVal={(n) => radius(n) ** 2}
          nodeLabel={(n) => `${n.id}${n.note?.title ? ' — ' + n.note.title : ''}`}
          nodeCanvasObject={nodeCanvasObject}
          nodePointerAreaPaint={pointerArea}
          linkColor={linkColor}
          linkWidth={linkWidth}
          linkLineDash={(l) => (l.__api ? [4, 3] : null)}
          linkDirectionalParticles={particles}
          linkDirectionalParticleSpeed={particleSpeed}
          linkDirectionalParticleWidth={particleWidth}
          linkDirectionalParticleColor={particleColor}
          cooldownTicks={250}
          onEngineTick={() => {
            ticks.current += 1;
          }}
          onEngineStop={() => {
            // layout settled: frame the pinned chain (or everything) unless the user already moved the view
            running.current = false;
            if (!pendingFit.current) return;
            pendingFit.current = false;
            const set = useStore.getState().pinned?.nodes;
            if (set || !touched.current) fg.current?.zoomToFit(0, 70, set ? (n: FGNode) => set.has(n.id) : undefined);
          }}
          d3VelocityDecay={0.3}
          minZoom={0.05}
          maxZoom={12}
          onNodeHover={(n) => {
            setHovered(n ? n.id : null);
            if (box.current) box.current.style.cursor = n ? 'pointer' : 'default';
          }}
          onNodeClick={(n) => void select(n.id, { fit: true })}
          onBackgroundClick={() => clear()}
        />
      )}
    </div>
  );
}
