// Bounded pull of the trailing 90 days of Chicago crime data (Socrata dataset
// ijzp-q8t2, "Crimes - 2001 to Present") straight into Postgres via EF Core.
//
// Never widen the $where filter below into a full-table pull - that dataset has
// several million rows going back to 2001.
//
// Optional SOCRATA_APP_TOKEN in the environment avoids throttling at higher volumes;
// not required at this (90-day) volume.

using System.Globalization;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using SuperHeroOps.Core;
using SuperHeroOps.Data;
using SuperHeroOps.Data.Configuration;
using SuperHeroOps.Ingestion.Crime;

EnvFileLoader.LoadFromRepoRootIfPresent();

const int TrailingWindowDays = 90;
const int PageSize = 5000;
const int InsertBatchSize = 2000;

var socrataAppToken = Environment.GetEnvironmentVariable("SOCRATA_APP_TOKEN");

using var http = new HttpClient
{
    BaseAddress = new Uri("https://data.cityofchicago.org/resource/"),
    Timeout = TimeSpan.FromSeconds(60),
};
if (!string.IsNullOrWhiteSpace(socrataAppToken))
{
    http.DefaultRequestHeaders.Add("X-App-Token", socrataAppToken);
}

var jsonOptions = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };

var cutoff = DateTime.UtcNow.Date.AddDays(-TrailingWindowDays);
var cutoffText = cutoff.ToString("yyyy-MM-ddTHH:mm:ss.fff", CultureInfo.InvariantCulture);

Console.WriteLine($"Pulling Chicago crime data (dataset ijzp-q8t2) for the trailing {TrailingWindowDays} days (>= {cutoffText})...");

var rawRecords = new List<SocrataCrimeRecord>();
var offset = 0;
while (true)
{
    var url = BuildPageUrl(cutoffText, PageSize, offset);
    var page = await FetchWithRetryAsync(http, url, jsonOptions, maxAttempts: 4);
    if (page is null)
    {
        Console.Error.WriteLine("Aborting: could not fetch a page after retries.");
        return 1;
    }

    rawRecords.AddRange(page);
    Console.WriteLine($"  fetched offset {offset}: {page.Count} rows (running total {rawRecords.Count})");

    if (page.Count < PageSize)
    {
        break;
    }

    offset += PageSize;
    await Task.Delay(300);
}

var validCommunityAreaIds = ChicagoCommunityAreas.All.Select(a => a.Id).ToHashSet();
var incidents = new List<CrimeIncident>();
int droppedMissingFields = 0, droppedBadDate = 0, droppedBadCommunityArea = 0;

foreach (var r in rawRecords)
{
    if (string.IsNullOrWhiteSpace(r.CaseNumber)
        || string.IsNullOrWhiteSpace(r.PrimaryType)
        || string.IsNullOrWhiteSpace(r.Date))
    {
        droppedMissingFields++;
        continue;
    }

    if (!DateTime.TryParse(
            r.Date, CultureInfo.InvariantCulture, DateTimeStyles.None, out var occurredAt))
    {
        droppedBadDate++;
        continue;
    }

    if (!int.TryParse(r.CommunityArea, out var communityAreaId)
        || !validCommunityAreaIds.Contains(communityAreaId))
    {
        droppedBadCommunityArea++;
        continue;
    }

    double? latitude = double.TryParse(r.Latitude, NumberStyles.Float, CultureInfo.InvariantCulture, out var lat)
        ? lat
        : null;
    double? longitude = double.TryParse(r.Longitude, NumberStyles.Float, CultureInfo.InvariantCulture, out var lon)
        ? lon
        : null;

    incidents.Add(new CrimeIncident
    {
        CaseNumber = r.CaseNumber,
        OccurredAt = occurredAt,
        PrimaryType = r.PrimaryType,
        Arrest = r.Arrest ?? false,
        Latitude = latitude,
        Longitude = longitude,
        CommunityAreaId = communityAreaId,
    });
}

Console.WriteLine();
Console.WriteLine("Parsed pull:");
Console.WriteLine($"  raw rows fetched:                          {rawRecords.Count}");
Console.WriteLine($"  dropped (missing case/type/date):          {droppedMissingFields}");
Console.WriteLine($"  dropped (unparseable date):                {droppedBadDate}");
Console.WriteLine($"  dropped (missing/invalid community area):  {droppedBadCommunityArea}");
Console.WriteLine($"  incidents to load:                         {incidents.Count}");

var dbOptions = SuperHeroOpsDbContextOptionsFactory.Build();
await using var db = new SuperHeroOpsDbContext(dbOptions);

Console.WriteLine();
Console.WriteLine("Applying migrations...");
await db.Database.MigrateAsync();

Console.WriteLine("Seeding the 77 fixed community areas (idempotent)...");
var existingAreaIds = (await db.CommunityAreas.Select(a => a.Id).ToListAsync()).ToHashSet();
var newAreas = ChicagoCommunityAreas.All.Where(a => !existingAreaIds.Contains(a.Id)).ToList();
if (newAreas.Count > 0)
{
    db.CommunityAreas.AddRange(newAreas);
    await db.SaveChangesAsync();
}

Console.WriteLine($"  community areas in DB: {await db.CommunityAreas.CountAsync()}");

Console.WriteLine("Replacing crime_incidents with the fresh 90-day window...");
await db.CrimeIncidents.ExecuteDeleteAsync();

for (var i = 0; i < incidents.Count; i += InsertBatchSize)
{
    var batch = incidents.Skip(i).Take(InsertBatchSize).ToList();
    db.CrimeIncidents.AddRange(batch);
    await db.SaveChangesAsync();
    db.ChangeTracker.Clear();
    Console.WriteLine($"  inserted {Math.Min(i + InsertBatchSize, incidents.Count)}/{incidents.Count}");
}

Console.WriteLine();
Console.WriteLine("Crime ingestion complete.");

var areaNames = ChicagoCommunityAreas.All.ToDictionary(a => a.Id, a => a.Name);

var topTypes = await db.CrimeIncidents
    .GroupBy(c => c.PrimaryType)
    .Select(g => new { Type = g.Key, Count = g.Count() })
    .OrderByDescending(g => g.Count)
    .Take(15)
    .ToListAsync();

Console.WriteLine();
Console.WriteLine("Top primary crime types in the 90-day window:");
foreach (var t in topTypes)
{
    Console.WriteLine($"  {t.Type,-25} {t.Count,6}");
}

var topAreas = await db.CrimeIncidents
    .GroupBy(c => c.CommunityAreaId)
    .Select(g => new { AreaId = g.Key, Count = g.Count() })
    .OrderByDescending(g => g.Count)
    .Take(10)
    .ToListAsync();

Console.WriteLine();
Console.WriteLine("Top 10 community areas by incident count:");
foreach (var a in topAreas)
{
    Console.WriteLine($"  {areaNames.GetValueOrDefault(a.AreaId, "?"),-25} {a.Count,6}");
}

var garfieldRidgeCount = await db.CrimeIncidents.CountAsync(c => c.CommunityAreaId == 56);
Console.WriteLine();
Console.WriteLine($"Garfield Ridge (community area 56) incident count in this window: {garfieldRidgeCount}");

return 0;

static string BuildPageUrl(string cutoffText, int limit, int offset)
{
    var where = Uri.EscapeDataString($"date >= '{cutoffText}'");
    var order = Uri.EscapeDataString("date,case_number");
    return $"ijzp-q8t2.json?$where={where}&$order={order}&$limit={limit}&$offset={offset}";
}

static async Task<List<SocrataCrimeRecord>?> FetchWithRetryAsync(
    HttpClient http, string url, JsonSerializerOptions options, int maxAttempts)
{
    var delay = TimeSpan.FromSeconds(2);

    for (var attempt = 1; attempt <= maxAttempts; attempt++)
    {
        try
        {
            using var response = await http.GetAsync(url);
            if (response.IsSuccessStatusCode)
            {
                await using var stream = await response.Content.ReadAsStreamAsync();
                return await JsonSerializer.DeserializeAsync<List<SocrataCrimeRecord>>(stream, options) ?? [];
            }

            var reason = (int)response.StatusCode == 429 ? "rate limited" : $"HTTP {(int)response.StatusCode}";
            Console.WriteLine($"  {reason}, attempt {attempt}/{maxAttempts}, backing off {delay.TotalSeconds:0}s...");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            Console.WriteLine($"  {ex.GetType().Name}, attempt {attempt}/{maxAttempts}, backing off {delay.TotalSeconds:0}s...");
        }

        await Task.Delay(delay);
        delay *= 2;
    }

    return null;
}
