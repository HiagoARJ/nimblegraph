from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_list_users():
    assert client.get("/api/users/").status_code == 200
