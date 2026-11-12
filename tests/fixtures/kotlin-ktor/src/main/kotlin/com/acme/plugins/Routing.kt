package com.acme.plugins

import com.acme.users.UserService
import io.ktor.server.application.*
import io.ktor.server.response.*
import io.ktor.server.routing.*

fun Application.configureRouting() {
    val service = UserService()
    routing {
        get("/health") { call.respondText("ok") }
        route("/api/users") {
            get { call.respond(service.list()) }
            get("/{id}") { call.respond(service.find(call.parameters["id"]!!)) }
            post { call.respond(service.create()) }
        }
    }
}
