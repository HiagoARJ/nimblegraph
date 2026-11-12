using Shop.Api.Data;
using Shop.Api.Endpoints;
using Shop.Api.Services;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<IUserService, UserService>();
var app = builder.Build();
app.MapControllers();
app.MapGet("/health", () => "ok");
var orders = app.MapGroup("/api/orders");
orders.MapGet("/{id:int}", OrderEndpoints.Get);
app.Run();
