import { Column } from 'typeorm'
import { AggregateRoot } from '../../../shared/domain/aggregate-root'
import { OrderLine } from './order-line'
import { Money } from './money.vo'
import { OrderPlacedEvent } from './events/order-placed.event'

export class Order extends AggregateRoot {
  @Column()
  id: string
  lines: OrderLine[] = []

  constructor(id: string) {
    super()
    this.id = id
  }

  total(): Money {
    return this.lines.reduce((acc, l) => acc.add(l.subtotal()), Money.zero())
  }

  place() {
    this.record(new OrderPlacedEvent(this.id))
  }
}
