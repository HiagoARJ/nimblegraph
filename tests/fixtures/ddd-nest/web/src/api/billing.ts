import { request } from './http'

export function getInvoice(id: string) {
  return request(`/billing/invoices/${id}`)
}

export function issueInvoice() {
  return request('/billing/createInvoice', { method: 'POST' })
}
