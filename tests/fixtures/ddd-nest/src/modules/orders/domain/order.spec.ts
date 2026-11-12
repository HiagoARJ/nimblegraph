import { Order } from './order'

describe('Order', () => {
  it('starts empty', () => {
    expect(new Order('1').lines).toHaveLength(0)
  })
})
