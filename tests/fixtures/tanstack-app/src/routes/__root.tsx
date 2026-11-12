import { createRootRouteWithContext, Outlet } from '@tanstack/react-router'
interface Ctx { user?: string }
export const Route = createRootRouteWithContext<Ctx>()({ component: () => <Outlet /> })
