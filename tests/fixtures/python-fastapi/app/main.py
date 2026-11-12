from fastapi import FastAPI

from app.routers import orders, users

app = FastAPI(title="shop")
app.include_router(users.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
