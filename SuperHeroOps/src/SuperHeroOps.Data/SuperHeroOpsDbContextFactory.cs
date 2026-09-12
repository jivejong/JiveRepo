using Microsoft.EntityFrameworkCore.Design;

namespace SuperHeroOps.Data;

// Lets `dotnet ef migrations add` / `dotnet ef database update` construct a context
// without needing a full app host. Not used at runtime.
public class SuperHeroOpsDbContextFactory : IDesignTimeDbContextFactory<SuperHeroOpsDbContext>
{
    public SuperHeroOpsDbContext CreateDbContext(string[] args)
        => new(SuperHeroOpsDbContextOptionsFactory.Build());
}
