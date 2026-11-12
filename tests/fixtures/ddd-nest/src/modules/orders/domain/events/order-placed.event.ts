import { DomainEvent } from '../../../../shared/domain/domain-event'

export class OrderPlacedEvent implements DomainEvent {
  occurredAt = new Date()

  constructor(readonly orderId: string) {}
}
