from fastapi import APIRouter, Depends

from ..schemas import UserCreate
from ..services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/")
def list_users(svc: UserService = Depends()):
    return svc.list()


@router.post("/")
def create_user(body: UserCreate, svc: UserService = Depends()):
    return svc.create(body)


@router.get("/{user_id}")
def get_user(user_id: int, svc: UserService = Depends()):
    return svc.get(user_id)
