import { Button, Card } from './ui'
import type { ButtonProps } from './ui'

export function Layout(props: { title?: string }) {
  const extra: ButtonProps = { variant: 'ghost' }
  return <Card><Button {...extra}>{props.title ?? 'Home'}</Button></Card>
}
