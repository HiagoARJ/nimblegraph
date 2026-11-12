from app.repositories.user_repo import UserRepository
from app.schemas import UserCreate


class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def list(self):
        return self.repo.all()

    def get(self, user_id: int):
        return self.repo.find(user_id)

    def create(self, body: UserCreate):
        return self.repo.add(body)
