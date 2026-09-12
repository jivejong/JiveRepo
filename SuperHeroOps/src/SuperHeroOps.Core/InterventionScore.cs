namespace SuperHeroOps.Core;

public class InterventionScore
{
    public int Id { get; set; }
    public int CommunityAreaId { get; set; }
    public int HeroId { get; set; }

    public required string CrimeCategory { get; set; } // e.g. "Violent", "Property"
    public double NormalizedScore { get; set; } // 0-100, comparable across heroes
    public double ProjectedEffectPercent { get; set; } // capped, illustrative only
    public DateTime ComputedAt { get; set; }

    public CommunityArea CommunityArea { get; set; } = null!;
    public Hero Hero { get; set; } = null!;
}
