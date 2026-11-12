import useSWR from 'swr'
import { api } from '@/api/client'
import type { User } from '@/api/types'

const BASE = '/api'

export function useUsers() {
  return useSWR<User[]>('/api/users', (url: string) => fetch(url).then((r) => r.json()))
}

export function useUser(id: string) {
  return useSWR(`${BASE}/users/${id}`, () => api.get<User>(`/users/${id}`))
}
