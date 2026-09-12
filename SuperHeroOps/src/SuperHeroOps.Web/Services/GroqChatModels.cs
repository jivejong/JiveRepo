using System.Text.Json.Serialization;

namespace SuperHeroOps.Web.Services;

// Minimal OpenAI-compatible chat-completions wire format for Groq's API
// (https://api.groq.com/openai/v1/chat/completions).
internal sealed class GroqChatRequest
{
    public required string Model { get; set; }
    public required List<GroqChatMessage> Messages { get; set; }

    [JsonPropertyName("response_format")]
    public GroqResponseFormat? ResponseFormat { get; set; }

    public double Temperature { get; set; } = 0.7;
}

internal sealed class GroqChatMessage
{
    public required string Role { get; set; }
    public required string Content { get; set; }
}

internal sealed class GroqResponseFormat
{
    public string Type { get; set; } = "json_object";
}

internal sealed class GroqChatResponse
{
    public List<GroqChoice>? Choices { get; set; }
}

internal sealed class GroqChoice
{
    public GroqChatMessage? Message { get; set; }
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
