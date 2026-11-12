import { Money } from '../../orders'
import { InvoiceService } from './InvoiceService'

export function calculateTotal(prices: number[]) {
  const digits = InvoiceService.rounding()
  return new Money({ cents: Number(prices.reduce((a, b) => a + b, 0).toFixed(digits)) })
}
