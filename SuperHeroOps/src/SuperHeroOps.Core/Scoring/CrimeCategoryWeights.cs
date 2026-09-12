namespace SuperHeroOps.Core.Scoring;

// The powerstats weighted per crime category, per HANDOFF.md's intervention scoring
// model. Criminal Damage is its own bucket (weighted toward rapid response /
// deterrence-presence) rather than folded into Property, since vandalism/damage calls
// for showing up fast and being visibly imposing, not the smart/stealthy interdiction
// that fits theft and burglary.
public static class CrimeCategoryWeights
{
    public static readonly IReadOnlyDictionary<CrimeCategory, IReadOnlyList<Func<Hero, int>>> ByCategory =
        new Dictionary<CrimeCategory, IReadOnlyList<Func<Hero, int>>>
        {
            [CrimeCategory.Violent] = [h => h.Strength, h => h.Combat, h => h.Durability],
            [CrimeCategory.Property] = [h => h.Speed, h => h.Intelligence],
            [CrimeCategory.CriminalDamage] = [h => h.Speed, h => h.Power],
            [CrimeCategory.Narcotics] = [h => h.Intelligence, h => h.Power],
            [CrimeCategory.WeaponsViolation] = [h => h.Combat, h => h.Strength],
            [CrimeCategory.DeceptivePractice] = [h => h.Intelligence],
        };
}
