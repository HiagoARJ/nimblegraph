import Vapor

@main
enum Entrypoint {
    static func main() async throws {
        let app = try await Application.make(.detect())
        try routes(app)
        try await app.execute()
    }
}
