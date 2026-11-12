import { Inject } from '@nestjs/common'
import { CommandHandler, ICommandHandler } from '@nestjs/cqrs'
import { PlaceOrderCommand } from './place-order.command'
import { Order } from '../domain/order'
import { OrderRepository } from '../domain/order.repository'

@CommandHandler(PlaceOrderCommand)
export class PlaceOrderHandler implements ICommandHandler<PlaceOrderCommand> {
  constructor(@Inject('OrderRepository') private readonly orders: OrderRepository) {}

  async execute(cmd: PlaceOrderCommand) {
    const order = new Order(cmd.orderId)
    order.place()
    await this.orders.save(order)
  }
}
