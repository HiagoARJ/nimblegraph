import { Injectable } from '@nestjs/common'
import { Invoice } from '../domain/invoice'
import { calculateTotal } from './invoice-calculator'

@Injectable()
export class InvoiceService {
  issue(orderId: string) {
    return new Invoice(orderId, [], calculateTotal([]))
  }

  static rounding() {
    return 2
  }
}
