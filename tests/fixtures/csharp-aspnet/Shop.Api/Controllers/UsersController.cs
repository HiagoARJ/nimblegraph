using Microsoft.AspNetCore.Mvc;
using Shop.Api.Dtos;
using Shop.Api.Services;

namespace Shop.Api.Controllers;

[ApiController]
[Route("api/[controller]")]
public class UsersController : ControllerBase
{
    private readonly IUserService _service;

    public UsersController(IUserService service)
    {
        _service = service;
    }

    [HttpGet]
    public IActionResult List() => Ok(_service.List());

    [HttpGet("{id}")]
    public IActionResult Get(int id) => Ok(_service.Get(id));

    [HttpPost]
    public IActionResult Create([FromBody] CreateUserDto dto) => Ok(_service.Create(dto));
}
