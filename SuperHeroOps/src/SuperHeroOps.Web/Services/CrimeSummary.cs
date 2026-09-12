namespace SuperHeroOps.Web.Services;

// Real crime summary stats for one community area, passed as LLM report input.
public sealed record CrimeSummary(
    string AreaName,
    int TotalIncidents,
    int TotalArrests,
    IReadOnlyList<(string PrimaryType, int Count)> TopTypes)
{
    public double ArrestRatePercent => TotalIncidents == 0 ? 0 : 100.0 * TotalArrests / TotalIncidents;
}
