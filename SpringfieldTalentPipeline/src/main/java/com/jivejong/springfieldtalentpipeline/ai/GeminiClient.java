package com.jivejong.springfieldtalentpipeline.ai;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.jivejong.springfieldtalentpipeline.config.GeminiProperties;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

/**
 * Gemini chat completions, constrained to structured JSON output.
 *
 * <p>Deliberately thin: it knows how to ask Gemini for JSON matching a schema and hand back the
 * parsed result. What to ask for lives with each feature.
 */
@Component
public class GeminiClient {

    private static final Logger log = LoggerFactory.getLogger(GeminiClient.class);

    /** Enough to ride out a per-minute limit without stalling a request indefinitely. */
    private static final int MAX_RATE_LIMIT_ATTEMPTS = 4;

    private final RestClient restClient;
    private final GeminiProperties properties;
    private final ObjectMapper objectMapper;

    public GeminiClient(GeminiProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(properties.getConnectTimeout());
        requestFactory.setReadTimeout(properties.getReadTimeout());
        this.restClient = RestClient.builder()
                .baseUrl(properties.getBaseUrl())
                .requestFactory(requestFactory)
                .build();
    }

    /** What a generation cost, for logging and for checking the estimates in docs/AI_FEATURES.md. */
    public record TokenUsage(Integer promptTokens, Integer completionTokens, Integer totalTokens) {

        static TokenUsage from(GeminiChat.Usage usage) {
            return usage == null
                    ? new TokenUsage(null, null, null)
                    : new TokenUsage(
                            usage.promptTokens(), usage.completionTokens(), usage.totalTokens());
        }
    }

    public record StructuredResult<T>(T value, String modelUsed, TokenUsage usage) {}

    /**
     * Sends a single structured-output request and parses the reply into {@code responseType}.
     *
     * @param schemaName a name for the JSON schema, required by the API
     * @param schema the JSON schema the reply must satisfy
     * @param temperature chosen per feature - scoring wants near-zero, the interview does not
     * @throws GeminiException if the key is missing, the call fails, or the reply will not parse
     */
    public <T> StructuredResult<T> completeStructured(
            String model,
            String systemPrompt,
            String userPrompt,
            String schemaName,
            Map<String, Object> schema,
            Class<T> responseType,
            Double temperature) {

        if (!properties.isConfigured()) {
            throw new GeminiException(
                    "No Gemini API key configured. Set gemini.api-key in config/local.yml or the "
                            + "GEMINI_API_KEY environment variable.");
        }

        GeminiChat.Request request = new GeminiChat.Request(
                model,
                List.of(GeminiChat.Message.system(systemPrompt), GeminiChat.Message.user(userPrompt)),
                GeminiChat.ResponseFormat.jsonSchema(schemaName, schema),
                properties.getReasoningEffort(),
                temperature,
                properties.getMaxCompletionTokens());

        GeminiChat.Response response = sendWithRateLimitRetry(request, model);

        if (response == null || response.firstContent() == null) {
            throw new GeminiException("Gemini returned no content for model " + model);
        }

        String content = response.firstContent();
        T value;
        try {
            value = objectMapper.readValue(content, responseType);
        } catch (Exception e) {
            // Schema-constrained output should make this unreachable; if it happens, the raw reply
            // is the only useful evidence.
            throw new GeminiException(
                    "Could not parse Gemini reply as %s: %s"
                            .formatted(responseType.getSimpleName(), truncate(content)),
                    e);
        }

        GeminiClient.TokenUsage usage = TokenUsage.from(response.usage());
        log.info(
                "Gemini {} completed: {} prompt + {} completion = {} tokens",
                model,
                usage.promptTokens(),
                usage.completionTokens(),
                usage.totalTokens());
        return new StructuredResult<>(
                value, response.model() == null ? model : response.model(), usage);
    }

    /**
     * Sends the request, retrying on 429.
     *
     * <p>Gemini's free tier is limited per minute (docs/AI_FEATURES.md), and generating profiles for a
     * batch of applications walks straight into that - it is a normal operating condition here, not
     * an exceptional one, so a handful of retries is worth more than a clean stack trace. Honours
     * the {@code Retry-After} header when Gemini sends one, otherwise backs off 2s, 4s, 8s. Every
     * other error fails immediately: retrying a bad request just wastes the budget.
     */
    private GeminiChat.Response sendWithRateLimitRetry(GeminiChat.Request request, String model) {
        RestClientResponseException lastRateLimit = null;
        for (int attempt = 1; attempt <= MAX_RATE_LIMIT_ATTEMPTS; attempt++) {
            try {
                return restClient
                        .post()
                        .uri("/chat/completions")
                        .header("Authorization", "Bearer " + properties.getApiKey())
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(request)
                        .retrieve()
                        .body(GeminiChat.Response.class);
            } catch (RestClientResponseException e) {
                if (e.getStatusCode().value() != 429) {
                    throw new GeminiException(
                            "Gemini returned %d for model %s: %s"
                                    .formatted(
                                            e.getStatusCode().value(),
                                            model,
                                            truncate(e.getResponseBodyAsString())),
                            e);
                }
                lastRateLimit = e;
                if (attempt < MAX_RATE_LIMIT_ATTEMPTS) {
                    Duration wait = retryAfter(e).orElse(Duration.ofSeconds(1L << attempt));
                    log.warn(
                            "Gemini rate-limited {} (attempt {}/{}); waiting {}s",
                            model,
                            attempt,
                            MAX_RATE_LIMIT_ATTEMPTS,
                            wait.toSeconds());
                    sleep(wait);
                }
            } catch (Exception e) {
                throw new GeminiException("Gemini call failed for model " + model, e);
            }
        }
        throw new GeminiException(
                "Gemini rate limit not cleared for model %s after %d attempts: %s"
                        .formatted(
                                model,
                                MAX_RATE_LIMIT_ATTEMPTS,
                                truncate(
                                        lastRateLimit == null
                                                ? null
                                                : lastRateLimit.getResponseBodyAsString())),
                lastRateLimit);
    }

    private static Optional<Duration> retryAfter(RestClientResponseException e) {
        String header = e.getResponseHeaders() == null
                ? null
                : e.getResponseHeaders().getFirst("Retry-After");
        if (header == null || header.isBlank()) {
            return Optional.empty();
        }
        try {
            // The value may be fractional seconds, which Integer.parseInt would reject.
            return Optional.of(Duration.ofMillis(Math.round(Double.parseDouble(header.trim()) * 1000)));
        } catch (NumberFormatException ignored) {
            return Optional.empty();
        }
    }

    private static void sleep(Duration duration) {
        try {
            Thread.sleep(Math.max(0, duration.toMillis()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new GeminiException("Interrupted while waiting out a Gemini rate limit", e);
        }
    }

    private static String truncate(String text) {
        if (text == null) {
            return "(empty)";
        }
        return text.length() <= 500 ? text : text.substring(0, 500) + "...";
    }
}
