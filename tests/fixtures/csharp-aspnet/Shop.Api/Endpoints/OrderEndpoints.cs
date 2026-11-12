namespace Shop.Api.Endpoints;

public static class OrderEndpoints
{
    public static IResult Get(int id) => Results.Ok(id);
}
