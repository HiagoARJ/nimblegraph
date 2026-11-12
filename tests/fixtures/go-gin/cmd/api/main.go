package main

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"github.com/acme/shop/internal/handlers"
	"github.com/acme/shop/internal/users"
)

func main() {
	r := gin.Default()
	h := handlers.NewUserHandler(users.NewService(users.NewRepo()))
	api := r.Group("/api")
	api.GET("/users", h.List)
	api.POST("/users", h.Create)
	api.GET("/users/:id", h.Get)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", health)
	r.Run()
}

func health(w http.ResponseWriter, r *http.Request) {}
