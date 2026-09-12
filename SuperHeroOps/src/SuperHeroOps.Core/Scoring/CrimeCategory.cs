namespace SuperHeroOps.Core.Scoring;

// The synthetic weight-map buckets from HANDOFF.md's intervention scoring model,
// finalized against the real 90-day IUCR primary-type distribution (see
// CrimeTypeCategorizer). Distinct from CrimeIncident.PrimaryType, which is the raw
// IUCR category as reported by CPD.
public enum CrimeCategory
{
    Violent,
    Property,
    CriminalDamage,
    Narcotics,
    WeaponsViolation,
    DeceptivePractice,
}

public static class CrimeCategoryExtensions
{
    public static string ToLabel(this CrimeCategory category) => category switch
    {
        CrimeCategory.Violent => "Violent",
        CrimeCategory.Property => "Property",
        CrimeCategory.CriminalDamage => "Criminal Damage",
        CrimeCategory.Narcotics => "Narcotics",
        CrimeCategory.WeaponsViolation => "Weapons Violation",
        CrimeCategory.DeceptivePractice => "Deceptive Practice",
        _ => throw new ArgumentOutOfRangeException(nameof(category), category, null),
    };
}
