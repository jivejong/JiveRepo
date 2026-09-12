using Microsoft.EntityFrameworkCore;

namespace SuperHeroOps.Data;

// Centralizes Postgres + naming-convention setup so every project that opens a
// SuperHeroOpsDbContext (ingestion console apps, the web app) configures it the same way.
public static class SuperHeroOpsDbContextOptionsFactory
{
    public const string ConnectionStringEnvVar = "SUPERHEROOPS_DB_CONNECTION";

    // Matches docker-compose.yml at the repo root.
    private const string LocalDevDefaultConnectionString =
        "Host=localhost;Port=5433;Database=superheroops;Username=superheroops;Password=superheroops";

    public static string ResolveConnectionString()
        => Environment.GetEnvironmentVariable(ConnectionStringEnvVar) is { Length: > 0 } fromEnv
            ? fromEnv
            : LocalDevDefaultConnectionString;

    public static DbContextOptions<SuperHeroOpsDbContext> Build(string? connectionString = null)
    {
        var builder = new DbContextOptionsBuilder<SuperHeroOpsDbContext>();
        builder.UseNpgsql(connectionString ?? ResolveConnectionString());
        builder.UseSnakeCaseNamingConvention();
        return builder.Options;
    }
}
