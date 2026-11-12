import { DataSource } from 'typeorm'
import { Order } from '../domain/order'
import { OrderRepository } from '../domain/order.repository'

export class TypeormOrderRepository implements OrderRepository {
  constructor(private readonly db: DataSource) {}

  async findById(id: string) {
    return this.db.getRepository(Order).findOneBy({ id })
  }

  async save(order: Order) {
    await this.db.getRepository(Order).save(order)
  }
}
