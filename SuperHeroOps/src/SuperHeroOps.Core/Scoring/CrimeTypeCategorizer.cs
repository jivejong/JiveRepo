namespace SuperHeroOps.Core.Scoring;

// Maps a CrimeIncident's raw IUCR PrimaryType to a scoring bucket. Finalized against
// the real trailing-90-day distribution rather than the full IUCR taxonomy - see
// HANDOFF.md. Ambiguous catch-all categories (OTHER OFFENSE, CRIMINAL TRESPASS,
// PUBLIC PEACE VIOLATION) are deliberately left unmapped: they still count as real
// crime stats, they just don't get a hero-effect projection.
//
// CRIMINAL SEXUAL ASSAULT and SEX OFFENSE are also deliberately left unmapped, not
// folded into Violent: modeling sexual violence as something a hero's combat stats
// "solve" isn't something this app represents, the same reasoning that excluded
// mental health data from the concept entirely.
public static class CrimeTypeCategorizer
{
    private static readonly IReadOnlyDictionary<string, CrimeCategory> PrimaryTypeToCategory =
        new Dictionary<string, CrimeCategory>(StringComparer.OrdinalIgnoreCase)
        {
            ["BATTERY"] = CrimeCategory.Violent,
            ["ASSAULT"] = CrimeCategory.Violent,
            ["ROBBERY"] = CrimeCategory.Violent,

            ["THEFT"] = CrimeCategory.Property,
            ["BURGLARY"] = CrimeCategory.Property,
            ["MOTOR VEHICLE THEFT"] = CrimeCategory.Property,

            ["CRIMINAL DAMAGE"] = CrimeCategory.CriminalDamage,

            ["NARCOTICS"] = CrimeCategory.Narcotics,

            ["WEAPONS VIOLATION"] = CrimeCategory.WeaponsViolation,

            ["DECEPTIVE PRACTICE"] = CrimeCategory.DeceptivePractice,
        };

    public static CrimeCategory? Categorize(string primaryType)
        => PrimaryTypeToCategory.TryGetValue(primaryType, out var category) ? category : null;
}
