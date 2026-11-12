import { Body, Controller, Get, Param, Post } from '@nestjs/common'
import { CommandBus } from '@nestjs/cqrs'
import { PlaceOrderCommand } from '../application/place-order.command'
import { GetOrderUseCase } from '../application/get-order.use-case'
import { TypeormOrderRepository } from '../infrastructure/typeorm-order.repository'

@Controller('orders')
export class OrdersController {
  constructor(private readonly bus: CommandBus, private readonly getOrder: GetOrderUseCase,
              private readonly repo: TypeormOrderRepository) {}

  @Post()
  place(@Body() body: { id: string }) {
    return this.bus.execute(new PlaceOrderCommand(body.id))
  }

  @Get(':id')
  get(@Param('id') id: string) {
    return this.getOrder.execute(id)
  }

  @Get(':id/raw')
  raw(@Param('id') id: string) {
    return this.repo.findById(id)
  }
}
