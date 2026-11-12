import Vapor

struct TodoController: RouteCollection {
    let service: TodoService

    func boot(routes: RoutesBuilder) throws {
        let todos = routes.grouped("todos")
        todos.get(use: index)
        todos.post(use: create)
        todos.group(":todoID") { todo in
            todo.delete(use: delete)
        }
    }

    func index(req: Request) async throws -> [Todo] { service.all() }
    func create(req: Request) async throws -> Todo { service.add(Todo(title: "new")) }
    func delete(req: Request) async throws -> HTTPStatus { .noContent }
}
