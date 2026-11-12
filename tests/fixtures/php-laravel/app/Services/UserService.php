<?php

namespace App\Services;

use App\Models\User;

class UserService
{
    public function all() { return User::all(); }
    public function find(int $id) { return User::find($id); }
    public function create(array $data) { return User::create($data); }
}
