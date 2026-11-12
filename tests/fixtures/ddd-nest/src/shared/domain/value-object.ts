export abstract class ValueObject<T> {
  constructor(protected readonly props: T) {}

  equals(other: ValueObject<T>) {
    return JSON.stringify(this.props) === JSON.stringify(other.props)
  }
}
