import { Router } from 'express'
import userRoutes from './user.routes.js'
import { requireAuth } from '../middlewares/auth.js'

const router = Router()
router.use(requireAuth)
router.use('/users', userRoutes)
export default router
