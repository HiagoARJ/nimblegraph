import type { NextFunction, Request, Response } from 'express'
export function requireAuth(req: Request, _res: Response, next: NextFunction) {
  if (!req.headers.authorization) throw new Error('unauthorized')
  next()
}
