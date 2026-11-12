import { cn } from '../../lib/cn.js'

export function Card({ children }: { children?: unknown }) {
  return <div className={cn('card')}>{children}</div>
}
