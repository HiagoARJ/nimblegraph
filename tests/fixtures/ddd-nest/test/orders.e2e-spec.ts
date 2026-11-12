import request from 'supertest'
import { Test } from '@nestjs/testing'
import { AppModule } from '../src/app.module'

describe('orders (e2e)', () => {
  it('POST /orders', async () => {
    const mod = await Test.createTestingModule({ imports: [AppModule] }).compile()
    const app = mod.createNestApplication()
    await app.init()
    await request(app.getHttpServer()).post('/orders').send({ id: '1' }).expect(201)
  })
})
