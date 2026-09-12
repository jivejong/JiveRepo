namespace SuperHeroOps.Core.Scoring;

// Deterministic, LLM-free scoring math for the "deploy a hero" step. See HANDOFF.md's
// intervention scoring model: this is the fictional/illustrative layer, kept separate
// from the real crime data it's scored against.
public static class InterventionScoring
{
    // The model's fictional "best case" projected effect - even a hero who tops the
    // roster for a category is only ever illustrated as a 40% swing, never a claim of
    // eliminating crime. Tune here; nothing else in the codebase hardcodes this number.
    public const double MaxEffectCeilingPercent = 40.0;

    // Sum of a hero's powerstats relevant to a category. Not normalized - comparable
    // only to other raw scores for the same category.
    public static double RawScore(Hero hero, CrimeCategory category)
        => CrimeCategoryWeights.ByCategory[category].Sum(statSelector => statSelector(hero));

    // Normalizes every roster member's raw score for a category against the highest
    // raw score anyone in the roster achieves for it, so 100 always means "best in
    // this roster for this category" rather than a theoretical stat maximum that may
    // not be achievable by anyone actually seeded.
    public static IReadOnlyDictionary<int, double> NormalizeRoster(
        IReadOnlyCollection<Hero> roster, CrimeCategory category)
    {
        if (roster.Count == 0)
        {
            return new Dictionary<int, double>();
        }

        var rawScores = roster.ToDictionary(h => h.Id, h => RawScore(h, category));
        var maxRaw = rawScores.Values.Max();

        return maxRaw <= 0
            ? rawScores.ToDictionary(kv => kv.Key, _ => 0.0)
            : rawScores.ToDictionary(kv => kv.Key, kv => kv.Value / maxRaw * 100.0);
    }

    // Capped linear mapping from a 0-100 normalized score to a projected-effect
    // percentage. This is the step that's fiction - label it as illustrative in the UI.
    public static double ProjectedEffectPercent(double normalizedScore)
        => Math.Clamp(normalizedScore, 0.0, 100.0) / 100.0 * MaxEffectCeilingPercent;
}
