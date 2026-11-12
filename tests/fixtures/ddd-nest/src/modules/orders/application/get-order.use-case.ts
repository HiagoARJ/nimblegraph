import { Inject, Injectable } from '@nestjs/common'
import { OrderRepository } from '../domain/order.repository'

@Injectable()
export class GetOrderUseCase {
  constructor(@Inject('OrderRepository') private readonly orders: OrderRepository) {}

  execute(id: string) {
    return this.orders.findById(id)
  }
}
