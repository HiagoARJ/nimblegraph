package com.acme.users

class UserService(private val repo: UserRepository = UserRepository()) {
    fun list(): List<User> = repo.all()
    fun find(id: String): User? = repo.find(id)
    fun create(): User = repo.save(User("1", "new"))
}
