using Shop.Api.Data;
using Shop.Api.Dtos;
using Shop.Api.Models;

namespace Shop.Api.Services;

public class UserService : IUserService
{
    private readonly UserRepository _repo;

    public UserService(UserRepository repo) => _repo = repo;

    public IEnumerable<User> List() => _repo.All();

    public User? Get(int id) => _repo.Find(id);

    public User Create(CreateUserDto dto) => _repo.Add(new User { Name = dto.Name });
}
