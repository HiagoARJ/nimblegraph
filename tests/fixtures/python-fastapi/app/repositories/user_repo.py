from app.models import User


class UserRepository:
    def all(self) -> list[User]:
        return []

    def find(self, user_id: int) -> User | None:
        return None

    def add(self, body) -> User:
        return User()
