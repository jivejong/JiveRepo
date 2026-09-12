using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace SuperHeroOps.Data;

public static class ServiceCollectionExtensions
{
    // Registers an IDbContextFactory<SuperHeroOpsDbContext> rather than a scoped
    // DbContext - Blazor Server circuits are long-lived, so components should create
    // a short-lived context per operation instead of holding one for the circuit's
    // lifetime.
    public static IServiceCollection AddSuperHeroOpsDbContext(
        this IServiceCollection services, string? connectionString = null)
    {
        return services.AddDbContextFactory<SuperHeroOpsDbContext>(builder =>
        {
            builder.UseNpgsql(connectionString ?? SuperHeroOpsDbContextOptionsFactory.ResolveConnectionString());
            builder.UseSnakeCaseNamingConvention();
        });
    }
}
