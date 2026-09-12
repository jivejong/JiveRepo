// One-time, build-time snapshot of https://superheroapi.com into seed-data/heroes.json.
// This is a deliberate build-time script, not a missing feature: the app never calls
// the live SuperheroAPI at runtime, it loads the seed file this program produces.
//
// Portrait images are NOT downloaded here: SuperheroAPI's image CDN
// (superherodb.com) sits behind a Cloudflare bot challenge that blocks non-browser
// HTTP clients regardless of headers or source IP (confirmed via direct testing).
// Hero.ImagePath is always null - see Hero.cs.
//
// Requires SUPERHERO_API_TOKEN in the environment (never committed). Get a token by
// signing in at https://superheroapi.com with GitHub.

using System.Diagnostics;
using System.Text.Json;
using SuperHeroOps.Ingestion.Heroes;

const int MinId = 1;
const int MaxId = 731;
const int MaxAttemptsPerId = 4;

// This project intentionally has no solution references, so it loads its own .env
// instead of sharing SuperHeroOps.Data's EnvFileLoader.
LoadEnvFileIfPresent();

// SuperheroAPI's free tier allows 60 requests/min; pacing at ~1.1s/request keeps us
// comfortably under that without needing a token-bucket limiter for 731 calls.
var requestPacing = TimeSpan.FromMilliseconds(1100);

var token = Environment.GetEnvironmentVariable("SUPERHERO_API_TOKEN");
if (string.IsNullOrWhiteSpace(token))
{
    Console.Error.WriteLine("SUPERHERO_API_TOKEN environment variable is not set. Aborting.");
    return 1;
}

using var http = new HttpClient
{
    BaseAddress = new Uri($"https://superheroapi.com/api.php/{token}/"),
    Timeout = TimeSpan.FromSeconds(15),
};

var jsonOptions = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
var heroes = new List<HeroSeedRecord>();
int notFound = 0, droppedForMissingData = 0, fetchErrors = 0;

Console.WriteLine($"Fetching superhero ids {MinId}-{MaxId} from SuperheroAPI...");

for (var id = MinId; id <= MaxId; id++)
{
    var stopwatch = Stopwatch.StartNew();
    var api = await FetchWithRetryAsync(http, id, jsonOptions, MaxAttemptsPerId);

    if (api is null)
    {
        fetchErrors++;
    }
    else if (!string.Equals(api.Response, "success", StringComparison.OrdinalIgnoreCase))
    {
        notFound++;
    }
    else if (HeroSeedRecord.TryFromApiResponse(api, out var hero))
    {
        heroes.Add(hero);
    }
    else
    {
        droppedForMissingData++;
    }

    if (id % 50 == 0 || id == MaxId)
    {
        Console.WriteLine($"  progress: {id}/{MaxId} ids processed, {heroes.Count} heroes kept so far");
    }

    var remaining = requestPacing - stopwatch.Elapsed;
    if (remaining > TimeSpan.Zero && id != MaxId)
    {
        await Task.Delay(remaining);
    }
}

var outputPath = Path.Combine(FindRepoRoot(), "seed-data", "heroes.json");
Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);

var output = JsonSerializer.Serialize(
    heroes.OrderBy(h => h.Id),
    new JsonSerializerOptions { WriteIndented = true });
await File.WriteAllTextAsync(outputPath, output);

Console.WriteLine();
Console.WriteLine("Hero ingestion complete.");
Console.WriteLine($"  ids requested:             {MaxId - MinId + 1}");
Console.WriteLine($"  not found / no data:       {notFound}");
Console.WriteLine($"  dropped (missing stats):   {droppedForMissingData}");
Console.WriteLine($"  fetch errors (gave up):    {fetchErrors}");
Console.WriteLine($"  heroes written:            {heroes.Count}");
Console.WriteLine($"  output:                    {outputPath}");

return 0;

static async Task<SuperheroApiResponse?> FetchWithRetryAsync(
    HttpClient http, int id, JsonSerializerOptions options, int maxAttempts)
{
    var delay = TimeSpan.FromSeconds(1);

    for (var attempt = 1; attempt <= maxAttempts; attempt++)
    {
        try
        {
            using var response = await http.GetAsync(id.ToString());
            if (response.IsSuccessStatusCode)
            {
                await using var stream = await response.Content.ReadAsStreamAsync();
                return await JsonSerializer.DeserializeAsync<SuperheroApiResponse>(stream, options);
            }

            var reason = (int)response.StatusCode == 429 ? "rate limited" : $"HTTP {(int)response.StatusCode}";
            Console.WriteLine($"  [id {id}] {reason}, attempt {attempt}/{maxAttempts}, backing off {delay.TotalSeconds:0}s...");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            Console.WriteLine($"  [id {id}] {ex.GetType().Name}, attempt {attempt}/{maxAttempts}, backing off {delay.TotalSeconds:0}s...");
        }

        await Task.Delay(delay);
        delay *= 2;
    }

    Console.WriteLine($"  [id {id}] giving up after {maxAttempts} attempts.");
    return null;
}

static string FindRepoRoot()
{
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir is not null
        && !File.Exists(Path.Combine(dir.FullName, "SuperHeroOps.sln"))
        && !File.Exists(Path.Combine(dir.FullName, "SuperHeroOps.slnx")))
    {
        dir = dir.Parent;
    }

    return dir?.FullName
        ?? throw new InvalidOperationException("Could not locate repo root (SuperHeroOps.sln/.slnx not found above the build output).");
}

// Minimal .env loader (KEY=VALUE per line, '#' comments, shell env takes precedence).
// Duplicated from SuperHeroOps.Data.Configuration.EnvFileLoader since this project
// intentionally has no solution references.
static void LoadEnvFileIfPresent()
{
    string repoRoot;
    try
    {
        repoRoot = FindRepoRoot();
    }
    catch (InvalidOperationException)
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
