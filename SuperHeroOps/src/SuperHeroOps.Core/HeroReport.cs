namespace SuperHeroOps.Core;

public class HeroReport
{
    public int Id { get; set; }
    public int CommunityAreaId { get; set; }
    public int HeroId { get; set; }

    // Stored as JSONB - parsed structured output from Gemini
    public required string RiskAssessment { get; set; }
    public required string PredictedImpactNarrative { get; set; }
    public required string Recommendation { get; set; }
    public DateTime GeneratedAt { get; set; }

    public CommunityArea CommunityArea { get; set; } = null!;
    public Hero Hero { get; set; } = null!;
}
