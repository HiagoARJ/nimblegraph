export type User = { id: string; name: string }
const rows: User[] = [{ id: '1', name: 'Ana' }]
export async function listUsers() { return rows }
export async function getUser(id: string) { return rows.find((r) => r.id === id) ?? rows[0] }
