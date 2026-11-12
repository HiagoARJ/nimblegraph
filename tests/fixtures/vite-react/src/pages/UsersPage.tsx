import { useUsers } from '@/hooks/useUsers'
import { Card } from '@/components/ui'

export default function UsersPage() {
  const { data } = useUsers()
  return <Card>{data?.map((u) => <p key={u.id}>{u.name}</p>)}</Card>
}
