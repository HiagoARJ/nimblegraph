<?php

namespace App\Http\Controllers;

use App\Models\Order;

class OrderController extends Controller
{
    public function index() { return Order::all(); }
    public function show(Order $order) { return $order; }
}
