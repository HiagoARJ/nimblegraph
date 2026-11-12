import { userRepo } from '../repositories/user.repo.js'
import { hash } from '../lib/hash.js'

export class UserService {
  list() { return userRepo.findAll() }
  get(id: string) { return userRepo.findById(id) }
  async create(input: { email: string; password: string }) {
    return userRepo.insert({ ...input, password: hash(input.password) })
  }
}
