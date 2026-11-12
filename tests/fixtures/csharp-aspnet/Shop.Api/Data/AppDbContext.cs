using Microsoft.EntityFrameworkCore;
using Shop.Api.Models;

namespace Shop.Api.Data;

public class AppDbContext : DbContext
{
    public DbSet<User> Users => Set<User>();
}
