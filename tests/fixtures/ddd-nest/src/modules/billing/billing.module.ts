import { Module } from '@nestjs/common'
import { BillingController } from './presentation/billing.controller'
import { InvoiceService } from './application/InvoiceService'

@Module({ controllers: [BillingController], providers: [InvoiceService] })
export class BillingModule {}
