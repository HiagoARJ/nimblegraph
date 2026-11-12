package com.acme.users

import kotlin.test.Test

class UserServiceTest {
    @Test
    fun lists() {
        UserService().list()
    }
}
