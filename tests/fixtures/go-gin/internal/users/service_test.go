package users

import "testing"

func TestList(t *testing.T) {
	s := NewService(NewRepo())
	_ = s.List()
}
