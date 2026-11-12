using Shop.Api.Models;

namespace Shop.Api.Data;

public class UserRepository
{
    private readonly AppDbContext _db;

    public UserRepository(AppDbContext db) => _db = db;

    public IEnumerable<User> All() => _db.Users;

    public User? Find(int id) => _db.Users.Find(id);

    public User Add(User u) { _db.Users.Add(u); return u; }
}
