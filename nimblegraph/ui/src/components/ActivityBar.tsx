import { Boxes, Files, Landmark, Route, Search, Settings } from 'lucide-react';
import { useStore, type View } from '../store';

const ITEMS: { view: View; icon: typeof Files; title: string }[] = [
  { view: 'explorer', icon: Files, title: 'Explorer' },
  { view: 'routes', icon: Route, title: 'Rotas' },
  { view: 'features', icon: Boxes, title: 'Features (cadeias e escopos)' },
  { view: 'search', icon: Search, title: 'Busca (Ctrl+Shift+F)' },
  { view: 'architecture', icon: Landmark, title: 'Arquitetura (regras, contextos, métricas, testes)' },
];

export function ActivityBar() {
  const view = useStore((s) => s.view);
  const open = useStore((s) => s.sidebarOpen);
  const { setView } = useStore.getState();
  const btn = (v: View, Icon: typeof Files, title: string) => {
    const active = view === v && open;
    return (
      <button
        key={v}
        title={title}
        onClick={() => setView(v)}
        className={`relative flex h-12 w-12 items-center justify-center ${active ? 'text-white' : 'text-[#858585] hover:text-white'}`}
      >
        {active && <span className="absolute top-0 left-0 h-full w-[2px] bg-white" />}
        <Icon size={24} strokeWidth={1.5} />
      </button>
    );
  };
  return (
    <nav className="flex w-12 shrink-0 flex-col bg-bar">
      {ITEMS.map((i) => btn(i.view, i.icon, i.title))}
      <div className="mt-auto">{btn('settings', Settings, 'Configurações')}</div>
    </nav>
  );
}
