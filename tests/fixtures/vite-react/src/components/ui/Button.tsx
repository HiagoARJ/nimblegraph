import { cn } from '~lib/cn'

export interface ButtonProps { variant?: 'ghost' | 'solid'; children?: unknown }

export const Button = ({ variant = 'solid', children }: ButtonProps) => (
  <button className={cn('btn', variant)}>{children}</button>
)
