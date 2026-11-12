package com.acme

import com.acme.plugins.configureRouting
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty

fun main() {
    embeddedServer(Netty, port = 8080) { configureRouting() }.start(wait = true)
}
