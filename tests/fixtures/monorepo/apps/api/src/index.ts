import { db } from '#db'
import { formatMoney } from '@mono/shared'
import { helper } from './helpers'
export const total = formatMoney(db.sum()) + helper
