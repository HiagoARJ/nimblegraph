import { Money } from './money.vo'

export class OrderLine {
  constructor(readonly sku: string, readonly qty: number, readonly price: Money) {}

  subtotal(): Money {
    return this.price.times(this.qty)
  }
}
