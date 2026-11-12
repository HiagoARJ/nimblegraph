<?php

namespace Tests\Feature;

use App\Models\User;

class UserTest extends TestCase
{
    public function test_lists(): void { $this->getJson('/api/users'); User::factory(); }
}
