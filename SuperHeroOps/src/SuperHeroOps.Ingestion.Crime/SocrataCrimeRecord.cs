using System.Text.Json.Serialization;

namespace SuperHeroOps.Ingestion.Crime;

// The subset of fields we keep from Socrata's ijzp-q8t2 ("Crimes - 2001 to Present")
// dataset. Note community_area/latitude/longitude come back as quoted numeric strings,
// not JSON numbers.
internal sealed class SocrataCrimeRecord
{
    [JsonPropertyName("case_number")]
    public string? CaseNumber { get; set; }

    [JsonPropertyName("date")]
    public string? Date { get; set; }

    [JsonPropertyName("primary_type")]
    public string? PrimaryType { get; set; }

    [JsonPropertyName("community_area")]
    public string? CommunityArea { get; set; }

    [JsonPropertyName("arrest")]
    public bool? Arrest { get; set; }

    [JsonPropertyName("latitude")]
    public string? Latitude { get; set; }

    [JsonPropertyName("longitude")]
    public string? Longitude { get; set; }
}
