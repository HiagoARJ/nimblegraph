import { formatMoney, toSlug } from '@mono/shared'
import { today } from '@mono/shared/utils/dates'
import { missing } from '@/lib/missing'

export function Price({ cents }: { cents: number }) {
  return <span data-slug={toSlug('x')} data-day={today()}>{formatMoney(cents)}{missing}</span>
}
