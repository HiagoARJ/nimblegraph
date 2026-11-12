import { AggregateRoot } from '../../../shared/domain/aggregate-root'
import { OrderLine } from '../../orders/domain/order-line'
import { Money } from '../../orders'

export class Invoice extends AggregateRoot {
  constructor(readonly orderId: string, readonly lines: OrderLine[], readonly total: Money) {
    super()
  }
}
