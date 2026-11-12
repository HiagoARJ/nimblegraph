import { Controller, Get, Param, Post } from '@nestjs/common'
import { InvoiceService } from '../application/InvoiceService'

@Controller('billing')
export class BillingController {
  constructor(private readonly invoices: InvoiceService) {}

  @Get('invoices/:id')
  get(@Param('id') id: string) {
    return { id }
  }

  @Post('createInvoice')
  create() {
    return this.invoices.issue('1')
  }
}
