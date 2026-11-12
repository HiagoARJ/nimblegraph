import type { Request, Response } from 'express'
import { UserService } from '../services/user.service.js'
import { AuditService } from '../services/audit.service.js'
import { MailService } from '../services/mail.service.js'

const users = new UserService()
const audit = new AuditService()
const mail = new MailService()

export const userController = {
  async list(_req: Request, res: Response) {
    res.json(await users.list())
  },
  create: async (req: Request, res: Response) => {
    const user = await users.create(req.body)
    await mail.welcome(user.email)
    res.status(201).json(user)
  },
  async getById(req: Request, res: Response) {
    res.json(await users.get(req.params.id))
  },
  remove: async (req: Request, res: Response) => {
    await audit.log('remove', req.params.id)
    res.sendStatus(204)
  },
}
