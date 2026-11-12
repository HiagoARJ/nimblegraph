export const db = {
  query: (table: string, id?: string) => Promise.resolve([{ table, id }]),
  insert: (table: string, row: object) => Promise.resolve({ table, ...row, email: 'x' }),
}
