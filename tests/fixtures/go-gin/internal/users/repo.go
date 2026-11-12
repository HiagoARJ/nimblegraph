package users

type Repo struct{}

func NewRepo() *Repo { return &Repo{} }

func (r *Repo) All() []User          { return nil }
func (r *Repo) Find(id string) *User { return nil }
func (r *Repo) Save(u User) User     { return u }
