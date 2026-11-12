import { Module } from '@nestjs/common'
import { CqrsModule } from '@nestjs/cqrs'
import { OrdersController } from './presentation/orders.controller'
import { PlaceOrderHandler } from './application/place-order.handler'
import { GetOrderUseCase } from './application/get-order.use-case'
import { TypeormOrderRepository } from './infrastructure/typeorm-order.repository'

@Module({
  imports: [CqrsModule],
  controllers: [OrdersController],
  providers: [PlaceOrderHandler, GetOrderUseCase, { provide: 'OrderRepository', useClass: TypeormOrderRepository }],
})
export class OrdersModule {}
