final class TodoService {
    private var items: [Todo] = []

    func all() -> [Todo] { items }

    func add(_ todo: Todo) -> Todo {
        items.append(todo)
        return todo
    }
}
