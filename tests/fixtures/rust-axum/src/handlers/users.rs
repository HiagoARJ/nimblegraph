use axum::{extract::Path, Json};

use crate::db::UserRepo;
use crate::models::User;

pub async fn list() -> Json<Vec<User>> {
    Json(UserRepo::new().all())
}

pub async fn show(Path(id): Path<u64>) -> Json<Option<User>> {
    Json(UserRepo::new().find(id))
}

pub async fn create(Json(user): Json<User>) -> Json<User> {
    Json(UserRepo::new().save(user))
}
