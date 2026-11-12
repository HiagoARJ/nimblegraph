package handlers

import (
	"github.com/gin-gonic/gin"

	"github.com/acme/shop/internal/users"
)

type UserHandler struct {
	svc *users.Service
}

func NewUserHandler(s *users.Service) *UserHandler {
	return &UserHandler{svc: s}
}

func (h *UserHandler) List(c *gin.Context) {
	c.JSON(200, h.svc.List())
}

func (h *UserHandler) Get(c *gin.Context) {
	c.JSON(200, h.svc.Get(c.Param("id")))
}

func (h *UserHandler) Create(c *gin.Context) {
	var u users.User
	c.JSON(201, h.svc.Create(u))
}
