namespace SuperHeroOps.Core;

public class CrimeIncident
{
    public long Id { get; set; }
    public required string CaseNumber { get; set; }
    public DateTime OccurredAt { get; set; }
    public required string PrimaryType { get; set; } // IUCR category, e.g. "BATTERY"
    public bool Arrest { get; set; }
    public double? Latitude { get; set; }
    public double? Longitude { get; set; }

    public int CommunityAreaId { get; set; }
    public CommunityArea CommunityArea { get; set; } = null!;
}
