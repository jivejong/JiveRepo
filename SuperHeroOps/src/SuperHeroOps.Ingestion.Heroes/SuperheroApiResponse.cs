using System.Text.Json.Serialization;

namespace SuperHeroOps.Ingestion.Heroes;

// Shape of a single-character response from https://superheroapi.com/api.php/{token}/{id}.
// Note: the API returns every powerstat as a string, and uses the literal string "null"
// (not JSON null) for stats it doesn't have data for. Biography/appearance/work/
// connections fields use a different placeholder convention: a literal "-" (or
// ["-", "0 cm"] / ["- lb", "0 kg"] for the two-element height/weight arrays).
internal sealed class SuperheroApiResponse
{
    public string? Response { get; set; }
    public string? Error { get; set; }
    public string? Id { get; set; }
    public string? Name { get; set; }
    public PowerstatsDto? Powerstats { get; set; }
    public BiographyDto? Biography { get; set; }
    public AppearanceDto? Appearance { get; set; }
    public WorkDto? Work { get; set; }
    public ConnectionsDto? Connections { get; set; }
}

internal sealed class PowerstatsDto
{
    public string? Intelligence { get; set; }
    public string? Strength { get; set; }
    public string? Speed { get; set; }
    public string? Durability { get; set; }
    public string? Power { get; set; }
    public string? Combat { get; set; }
}

internal sealed class BiographyDto
{
    [JsonPropertyName("full-name")]
    public string? FullName { get; set; }

    public List<string>? Aliases { get; set; }

    [JsonPropertyName("place-of-birth")]
    public string? PlaceOfBirth { get; set; }

    [JsonPropertyName("first-appearance")]
    public string? FirstAppearance { get; set; }

    public string? Publisher { get; set; }
    public string? Alignment { get; set; }
}

internal sealed class AppearanceDto
{
    public List<string>? Height { get; set; }
    public List<string>? Weight { get; set; }

    [JsonPropertyName("eye-color")]
    public string? EyeColor { get; set; }

    [JsonPropertyName("hair-color")]
    public string? HairColor { get; set; }
}

internal sealed class WorkDto
{
    public string? Occupation { get; set; }
    public string? Base { get; set; }
}

internal sealed class ConnectionsDto
{
    [JsonPropertyName("group-affiliation")]
    public string? GroupAffiliation { get; set; }
}
