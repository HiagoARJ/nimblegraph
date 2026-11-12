import axios from 'axios'

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL })

export async function createUser(body: { name: string }) {
  return api.post('/users', body)
}
