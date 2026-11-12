import axios from 'axios'

export function placeOrder(id: string) {
  return axios.post('/orders', { id })
}

export function getOrder(id: string) {
  return fetch(`/orders/${id}`).then((r) => r.json())
}

export function cancelOrder(id: string) {
  return axios.delete(`/orders/${id}`)
}

export function listRefunds() {
  return fetch('/billing/refunds').then((r) => r.json())
}
