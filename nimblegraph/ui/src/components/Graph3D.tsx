import ForceGraph3D from 'react-force-graph-3d';
import type { GLink, GNode } from '../api';
import { API_LINK_COLOR, nodeColor } from '../lib/layers';
import { useStore } from '../store';

type L = GLink & { __key?: string; __api?: boolean };

/** 3D variant (loaded on demand: three.js is large). */
export default function Graph3D({ data, width, height }: { data: { nodes: GNode[]; links: L[] }; width: number; height: number }) {
  const pinned = useStore((s) => s.pinned);
  const hover = useStore((s) => s.hover);
  const active = hover ?? pinned;
  const colorBy = useStore((s) => s.colorBy);
  const { setHovered, select } = useStore.getState();
  const lit = (l: L) => !!active && active.links.has(l.__key ?? '');
  return (
    <ForceGraph3D<GNode, L>
      width={width}
      height={height}
      graphData={data}
      backgroundColor="rgba(0,0,0,0)"
      nodeId="id"
      nodeVal={(n) => 1 + n.importers}
      nodeColor={(n) => (!active || active.nodes.has(n.id) ? nodeColor(n, colorBy) : '#444444')}
      nodeLabel={(n) => `${n.id}${n.note?.title ? ' — ' + n.note.title : ''}`}
      linkColor={(l) => (active ? (lit(l) ? (l.__api ? API_LINK_COLOR : '#ffffff') : '#333333') : l.__api ? API_LINK_COLOR : '#666666')}
      linkOpacity={0.35}
      linkDirectionalParticles={(l) => (active ? (lit(l) ? 4 : 0) : 1)}
      linkDirectionalParticleSpeed={(l) => (lit(l) ? 0.014 : 0.004)}
      linkDirectionalParticleWidth={(l) => (lit(l) ? 2.5 : 1)}
      onNodeHover={(n) => setHovered(n ? n.id : null)}
      onNodeClick={(n) => void select(n.id)}
    />
  );
}
