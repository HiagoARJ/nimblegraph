use crate::models::User;

pub struct UserRepo;

impl UserRepo {
    pub fn new() -> Self {
        UserRepo
    }

    pub fn all(&self) -> Vec<User> {
        vec![]
    }

    pub fn find(&self, _id: u64) -> Option<User> {
        None
    }

    pub fn save(&self, user: User) -> User {
        user
    }
}
