namespace SuperHeroOps.Core;

public class CommunityArea
{
    public int Id { get; set; } // matches CPD's community area number (1-77)
    public required string Name { get; set; }

    public List<CrimeIncident> Incidents { get; set; } = [];
    public List<InterventionScore> Scores { get; set; } = [];
    public List<HeroReport> Reports { get; set; } = [];
}
