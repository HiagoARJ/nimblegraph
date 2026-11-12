<?php

use App\Http\Controllers\UserController;
use Illuminate\Support\Facades\Route;

Route::get('/users', [UserController::class, 'index']);
Route::post('/users', [UserController::class, 'store']);
Route::get('/users/{user}', [UserController::class, 'show'])->name('users.show');
Route::prefix('admin')->group(function () {
    Route::apiResource('orders', \App\Http\Controllers\OrderController::class);
});
