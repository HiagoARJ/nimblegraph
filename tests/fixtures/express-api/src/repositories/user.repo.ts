import { db } from '../lib/db.js'
export const userRepo = {
  findAll: () => db.query('users'),
  findById: (id: string) => db.query('users', id),
  insert: (row: object) => db.insert('users', row),
}
