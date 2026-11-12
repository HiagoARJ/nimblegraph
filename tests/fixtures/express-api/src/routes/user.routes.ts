import { Router } from 'express'
import { userController } from '../controllers/user.controller.js'
import { validate } from '../middlewares/validate.js'
import { createUserSchema } from '../schemas/user.schema.js'

const router = Router()

router
  .route('/')
  .get(userController.list)
  .post(validate(createUserSchema), userController.create)

router.get('/:id', userController.getById)
router.delete(`/:id`, userController.remove)

export default router
