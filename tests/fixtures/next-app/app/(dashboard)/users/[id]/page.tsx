import { getUser } from '@/lib/users'
export default async function UserPage({ params }: { params: { id: string } }) {
  const user = await getUser(params.id)
  return <h1>{user.name}</h1>
}
