package users

type Service struct {
	repo *Repo
}

func NewService(r *Repo) *Service {
	return &Service{repo: r}
}

func (s *Service) List() []User {
	return s.repo.All()
}

func (s *Service) Get(id string) *User {
	return s.repo.Find(id)
}

func (s *Service) Create(u User) User {
	return s.repo.Save(u)
}
