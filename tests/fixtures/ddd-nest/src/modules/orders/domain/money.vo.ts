import { ValueObject } from '../../../shared/domain/value-object'

export class Money extends ValueObject<{ cents: number }> {
  static zero() {
    return new Money({ cents: 0 })
  }

  add(other: Money) {
    return new Money({ cents: this.props.cents + other.props.cents })
  }

  times(n: number) {
    return new Money({ cents: this.props.cents * n })
  }
}
