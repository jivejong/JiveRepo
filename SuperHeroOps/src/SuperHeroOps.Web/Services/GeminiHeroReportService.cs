using System.Globalization;
using System.Net.Http.Headers;
using System.Text.Json;
using SuperHeroOps.Core;
using SuperHeroOps.Core.Reports;

namespace SuperHeroOps.Web.Services;

public sealed record HeroReportContent(string RiskAssessment, string PredictedImpactNarrative, string Recommendation);

// One Gemini call per hero - never a single multi-hero call, so each report can be
// judged (and can fail) independently. Structured JSON output, one-retry validation
// on a malformed response, matching the pattern used elsewhere in this build for
// external-call resilience (log and move on, don't take down the whole page over one
// bad response).
public sealed class GeminiHeroReportService
{
    private const string DefaultModel = "gemini-3.1-flash-lite";
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNameCaseInsensitive = true };

    private readonly HttpClient _http;
    private readonly string _model;

    public GeminiHeroReportService(HttpClient http)
    {
        _http = http;
        _http.BaseAddress = new Uri("https://generativelanguage.googleapis.com/v1beta/openai/");

        var apiKey = Environment.GetEnvironmentVariable("GEMINI_API_KEY");
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        }

        _model = Environment.GetEnvironmentVariable("GEMINI_MODEL") is { Length: > 0 } m ? m : DefaultModel;
    }

    public async Task<HeroReportContent?> GenerateAsync(
        Hero hero,
        CrimeSummary crimeSummary,
        IReadOnlyList<InterventionScore> scores,
        CancellationToken cancellationToken = default)
    {
        var systemPrompt = BuildSystemPrompt();
        var userPrompt = BuildUserPrompt(hero, crimeSummary, scores);

        var json = await CallModelAsync(systemPrompt, userPrompt, cancellationToken);
        if (json is null || !json.IsValid())
        {
            // One retry with a corrective nudge, per HANDOFF's one-retry validation rule.
            var retryUserPrompt = userPrompt
                + "\n\nYour previous response was not valid JSON with all three required "
                + "string fields (risk_assessment, predicted_impact_narrative, recommendation). "
                + "Respond again with ONLY a single valid JSON object containing exactly those "
                + "three non-empty string fields - no markdown, no extra commentary.";

            json = await CallModelAsync(systemPrompt, retryUserPrompt, cancellationToken);
        }

        if (json is null || !json.IsValid())
        {
            return null;
        }

        return new HeroReportContent(
            json.RiskAssessment!.Trim(),
            HeroReportDisclaimer.AppendTo(json.PredictedImpactNarrative!.Trim()),
            json.Recommendation!.Trim());
    }

    private async Task<HeroReportJson?> CallModelAsync(
        string systemPrompt, string userPrompt, CancellationToken cancellationToken)
    {
        var request = new GeminiChatRequest
        {
            Model = _model,
            Messages =
            [
                new GeminiChatMessage { Role = "system", Content = systemPrompt },
                new GeminiChatMessage { Role = "user", Content = userPrompt },
            ],
            ResponseFormat = new GeminiResponseFormat { Type = "json_object" },
        };

        try
        {
            using var response = await _http.PostAsJsonAsync("chat/completions", request, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                return null;
            }

            var chatResponse = await response.Content.ReadFromJsonAsync<GeminiChatResponse>(
                JsonOptions, cancellationToken);
            var content = chatResponse?.Choices?.FirstOrDefault()?.Message?.Content;
            if (string.IsNullOrWhiteSpace(content))
            {
                return null;
            }

            return JsonSerializer.Deserialize<HeroReportJson>(content, JsonOptions);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            return null;
        }
    }

    private static string BuildSystemPrompt() =>
        "You are generating a short, clearly-fictional intervention report for a portfolio "
        + "demo app called SuperHeroOps. The app pairs REAL Chicago crime data with a "
        + "FICTIONAL, illustrative model of deploying comic-book superheroes to a "
        + "neighborhood. Your job is to write a brief narrative interpreting a hero's "
        + "already-computed, deterministic effect numbers for one specific neighborhood - "
        + "you do not compute or alter those numbers. Do not invent superpowers or ability "
        + "descriptions: none were provided to you, and none should be fabricated. Base your "
        + "narrative only on the hero's alignment/occupation (if given) and the computed "
        + "per-category effect percentages provided. Respond with strict JSON only, exactly "
        + "these three string fields: risk_assessment, predicted_impact_narrative, "
        + "recommendation. No other fields, no markdown, no prose outside the JSON object.";

    private static string BuildUserPrompt(Hero hero, CrimeSummary summary, IReadOnlyList<InterventionScore> scores)
    {
        var topTypes = string.Join(
            ", ", summary.TopTypes.Select(t => $"{t.PrimaryType} ({t.Count})"));

        var scoreLines = scores.Count == 0
            ? "  (no scoreable crime categories in this neighborhood's real data window)"
            : string.Join(
                "\n",
                scores.Select(s =>
                    $"  - {s.CrimeCategory}: {s.ProjectedEffectPercent.ToString("0.0", CultureInfo.InvariantCulture)}% "
                    + $"projected effect (normalized score {s.NormalizedScore.ToString("0.0", CultureInfo.InvariantCulture)}/100)"));

        return $"""
            Neighborhood: {summary.AreaName}
            Real crime data (trailing 90 days): {summary.TotalIncidents} incidents, {summary.ArrestRatePercent.ToString("0.0", CultureInfo.InvariantCulture)}% arrest rate.
            Top real crime categories: {topTypes}

            Hero: {hero.Name}
            Alignment: {hero.Alignment ?? "unknown"}
            Occupation: {hero.Occupation ?? "unknown"}

            Computed projected effect per crime category (illustrative, capped, deterministic - not written by you):
            {scoreLines}

            Write:
            - risk_assessment (1-2 sentences) characterizing this neighborhood's real crime profile.
            - predicted_impact_narrative (2-4 sentences) interpreting what deploying {hero.Name} here might look like given the numbers above.
            - recommendation (1-2 sentences) on whether/how to use this hero here.

            Keep the tone clearly speculative/illustrative - this is a modeling exercise, not a real forecast.
            """;
    }
}
