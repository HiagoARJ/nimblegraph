import { DomainEvent } from './domain-event'

export abstract class AggregateRoot {
  private events: DomainEvent[] = []

  protected record(event: DomainEvent) {
    this.events.push(event)
  }
}
