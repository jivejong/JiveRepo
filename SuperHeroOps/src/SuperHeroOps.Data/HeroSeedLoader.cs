using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using SuperHeroOps.Core;

namespace SuperHeroOps.Data;

// Loads seed-data/heroes.json into the heroes table. Runtime never calls the live
// SuperheroAPI - this is the load side of the build-time snapshot produced by
// SuperHeroOps.Ingestion.Heroes. Hero.Id reuses the SuperheroAPI id, so this is a
// direct 1:1 deserialize-and-insert with no id-mapping step (see SCHEMA.md).
public static class HeroSeedLoader
{
    public static string ResolveDefaultSeedFilePath()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var candidate = Path.Combine(dir.FullName, "seed-data", "heroes.json");
            if (File.Exists(candidate))
            {
                return candidate;
            }

            dir = dir.Parent;
        }

        throw new InvalidOperationException(
            "Could not locate seed-data/heroes.json above the build output.");
    }

    // Idempotent: does nothing if the heroes table already has rows, so it's safe to
    // call on every app startup.
    public static async Task<int> LoadIfEmptyAsync(
        SuperHeroOpsDbContext db, string? seedFilePath = null, CancellationToken cancellationToken = default)
    {
        if (await db.Heroes.AnyAsync(cancellationToken))
        {
            return 0;
        }

        var path = seedFilePath ?? ResolveDefaultSeedFilePath();
        var json = await File.ReadAllTextAsync(path, cancellationToken);
        var heroes = JsonSerializer.Deserialize<List<Hero>>(
            json, new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? [];

        db.Heroes.AddRange(heroes);
        await db.SaveChangesAsync(cancellationToken);
        return heroes.Count;
    }
}
