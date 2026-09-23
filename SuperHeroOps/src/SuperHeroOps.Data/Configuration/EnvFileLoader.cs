namespace SuperHeroOps.Data.Configuration;

// Loads KEY=VALUE pairs from a .env file at the repo root into the process
// environment, without overwriting any variable already set externally (shell env
// wins). Keeps secrets (SUPERHERO_API_TOKEN, GEMINI_API_KEY, ...) out of the repo while
// every entry point still just reads them via plain Environment.GetEnvironmentVariable.
public static class EnvFileLoader
{
    public static void LoadFromRepoRootIfPresent()
    {
        var repoRoot = FindRepoRoot();
        if (repoRoot is null)
        {
            return;
        }

        var path = Path.Combine(repoRoot, ".env");
        if (!File.Exists(path))
        {
            return;
        }

        foreach (var line in File.ReadAllLines(path))
        {
            var trimmed = line.Trim();
            if (trimmed.Length == 0 || trimmed.StartsWith('#'))
            {
                continue;
            }

            var separatorIndex = trimmed.IndexOf('=');
            if (separatorIndex <= 0)
            {
                continue;
            }

            var key = trimmed[..separatorIndex].Trim();
            var value = trimmed[(separatorIndex + 1)..].Trim().Trim('"');

            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }

    private static string? FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null
            && !File.Exists(Path.Combine(dir.FullName, "SuperHeroOps.sln"))
            && !File.Exists(Path.Combine(dir.FullName, "SuperHeroOps.slnx")))
        {
            dir = dir.Parent;
        }

        return dir?.FullName;
    }
}
