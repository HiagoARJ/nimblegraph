using Shop.Api.Dtos;
using Shop.Api.Models;

namespace Shop.Api.Services;

public interface IUserService
{
    IEnumerable<User> List();
    User? Get(int id);
    User Create(CreateUserDto dto);
}
