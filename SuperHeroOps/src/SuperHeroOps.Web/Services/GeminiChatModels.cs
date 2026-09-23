using System.Text.Json.Serialization;

namespace SuperHeroOps.Web.Services;

// Minimal OpenAI-compatible chat-completions wire format for the Gemini API
// (https://generativelanguage.googleapis.com/v1beta/openai/chat/completions).
internal sealed class GeminiChatRequest
{
    public required string Model { get; set; }
    public required List<GeminiChatMessage> Messages { get; set; }

    [JsonPropertyName("response_format")]
    public GeminiResponseFormat? ResponseFormat { get; set; }

    public double Temperature { get; set; } = 0.7;
}

internal sealed class GeminiChatMessage
{
    public required string Role { get; set; }
    public required string Content { get; set; }
}

internal sealed class GeminiResponseFormat
{
    public string Type { get; set; } = "json_object";
}

internal sealed class GeminiChatResponse
{
    public List<GeminiChoice>? Choices { get; set; }
}

internal sealed class GeminiChoice
{
    public GeminiChatMessage? Message { get; set; }
}

// The strict three-field JSON shape the model is asked to return.
internal sealed class HeroReportJson
{
    [JsonPropertyName("risk_assessment")]
    public string? RiskAssessment { get; set; }

    [JsonPropertyName("predicted_impact_narrative")]
    public string? PredictedImpactNarrative { get; set; }

    public string? Recommendation { get; set; }

    public bool IsValid()
        => !string.IsNullOrWhiteSpace(RiskAssessment)
            && !string.IsNullOrWhiteSpace(PredictedImpactNarrative)
            && !string.IsNullOrWhiteSpace(Recommendation);
}
