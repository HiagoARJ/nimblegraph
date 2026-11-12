import Vapor

func routes(_ app: Application) throws {
    app.get("health") { req in "ok" }
    let api = app.grouped("api")
    try api.register(collection: TodoController(service: TodoService()))
}
