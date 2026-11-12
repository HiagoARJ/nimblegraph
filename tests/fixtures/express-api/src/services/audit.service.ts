import { db } from '../lib/db.js'
export class AuditService { log(action: string, id: string) { return db.insert('audit', { action, id }) } }
