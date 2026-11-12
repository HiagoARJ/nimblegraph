package com.acme.users

class UserRepository {
    fun all(): List<User> = emptyList()
    fun find(id: String): User? = null
    fun save(user: User): User = user
}
