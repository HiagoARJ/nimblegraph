<?php

namespace App\Http\Controllers;

use App\Http\Requests\StoreUserRequest;
use App\Services\UserService;

class UserController extends Controller
{
    public function __construct(private UserService $service)
    {
    }

    public function index()
    {
        return $this->service->all();
    }

    public function store(StoreUserRequest $request)
    {
        return $this->service->create($request->validated());
    }

    public function show(int $user)
    {
        return $this->service->find($user);
    }
}
