import { Injectable, NotFoundException } from '@nestjs/common'
import type { CreateUserDto } from './dto/create-user.dto'

@Injectable()
export class UsersService {
  private rows: CreateUserDto[] = []
  findAll() { return this.rows }
  findOne(id: string) {
    const row = this.rows.find((r) => r.email === id)
    if (!row) throw new NotFoundException()
    return row
  }
  create(dto: CreateUserDto) { this.rows.push(dto); return dto }
}
