mod db;
mod handlers;
mod models;

use axum::{routing::get, Router};

#[tokio::main]
async fn main() {
    let app = Router::new()
        .route("/health", get(handlers::health))
        .nest("/api", api());
    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

fn api() -> Router {
    Router::new()
        .route("/users", get(handlers::users::list).post(handlers::users::create))
        .route("/users/:id", get(handlers::users::show))
}
