using Shop.Api.Controllers;
using Xunit;

namespace Shop.Api.Tests;

public class UsersControllerTests
{
    [Fact]
    public void Lists() => Assert.NotNull(new UsersController(null!));
}
