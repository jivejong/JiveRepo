namespace SuperHeroOps.Ingestion.Heroes;

// The shape written to seed-data/heroes.json - a 1:1 match for SuperHeroOps.Core's
// Hero entity. ImagePath is always null: SuperheroAPI's image CDN sits behind a
// Cloudflare bot challenge that blocks non-browser HTTP clients (confirmed via direct
// testing - no header combination or source IP got past it), so portrait download was
// dropped rather than worked around with heavier automation.
internal sealed record HeroSeedRecord(
    int Id,
    string Name,
    string? ImagePath,
    string? Publisher,
    string? Alignment,
    int Intelligence,
    int Strength,
    int Speed,
    int Durability,
    int Power,
    int Combat,
    string? FullName,
    string? Aliases,
    string? PlaceOfBirth,
    string? FirstAppearance,
    string? Height,
    string? Weight,
    string? EyeColor,
    string? HairColor,
    string? Occupation,
    string? Base,
    string? GroupAffiliation)
{
    public static bool TryFromApiResponse(SuperheroApiResponse api, out HeroSeedRecord hero)
    {
        hero = null!;

        if (api.Powerstats is null
            || string.IsNullOrWhiteSpace(api.Id)
            || string.IsNullOrWhiteSpace(api.Name)
            || !int.TryParse(api.Id, out var id))
        {
            return false;
        }

        if (!TryParseStat(api.Powerstats.Intelligence, out var intelligence)
            || !TryParseStat(api.Powerstats.Strength, out var strength)
            || !TryParseStat(api.Powerstats.Speed, out var speed)
            || !TryParseStat(api.Powerstats.Durability, out var durability)
            || !TryParseStat(api.Powerstats.Power, out var power)
            || !TryParseStat(api.Powerstats.Combat, out var combat))
        {
            // Spec: drop any record with missing/null powerstats at ingestion time.
            return false;
        }

        hero = new HeroSeedRecord(
            id,
            api.Name,
            ImagePath: null,
            NormalizeOptional(api.Biography?.Publisher),
            NormalizeOptional(api.Biography?.Alignment),
            intelligence, strength, speed, durability, power, combat,
            NormalizeOptional(api.Biography?.FullName),
            NormalizeAliases(api.Biography?.Aliases),
            NormalizeOptional(api.Biography?.PlaceOfBirth),
            NormalizeOptional(api.Biography?.FirstAppearance),
            NormalizeMeasurementPair(api.Appearance?.Height),
            NormalizeMeasurementPair(api.Appearance?.Weight),
            NormalizeOptional(api.Appearance?.EyeColor),
            NormalizeOptional(api.Appearance?.HairColor),
            NormalizeOptional(api.Work?.Occupation),
            NormalizeOptional(api.Work?.Base),
            NormalizeOptional(api.Connections?.GroupAffiliation));
        return true;
    }

    private static bool TryParseStat(string? raw, out int value)
    {
        value = 0;
        if (string.IsNullOrWhiteSpace(raw) || raw.Equals("null", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        return int.TryParse(raw, out value);
    }

    // SuperheroAPI marks missing biography/appearance/work/connections data with a
    // literal "-" (or "- lb" / "- kg" for the weight pair) - distinct from the "null"
    // string convention powerstats use. No real value in these fields starts with a
    // hyphen, so a leading "-" after trimming is an unambiguous missing-data marker.
    private static bool IsMissingMarker(string? raw)
        => string.IsNullOrWhiteSpace(raw)
            || raw.Trim().StartsWith('-')
            || raw.Equals("null", StringComparison.OrdinalIgnoreCase);

    private static string? NormalizeOptional(string? raw)
        => IsMissingMarker(raw) ? null : raw!.Trim();

    private static string? NormalizeAliases(List<string>? aliases)
    {
        if (aliases is null || aliases.Count == 0)
        {
            return null;
        }

        var real = aliases.Where(a => !IsMissingMarker(a)).Select(a => a.Trim()).ToList();
        return real.Count == 0 ? null : string.Join(", ", real);
    }

    // Height/weight come back as a two-element [imperial, metric] pair, e.g.
    // ["6'2", "188 cm"], or ["-", "0 cm"] / ["- lb", "0 kg"] when unknown.
    private static string? NormalizeMeasurementPair(List<string>? pair)
    {
        if (pair is null || pair.Count < 2 || IsMissingMarker(pair[0]))
        {
            return null;
        }

        return $"{pair[0].Trim()} ({pair[1].Trim()})";
    }
}
