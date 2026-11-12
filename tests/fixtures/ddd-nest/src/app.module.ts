import { Module } from '@nestjs/common'
import { OrdersModule } from './modules/orders/orders.module'
import { BillingModule } from './modules/billing/billing.module'

@Module({ imports: [OrdersModule, BillingModule] })
export class AppModule {}
