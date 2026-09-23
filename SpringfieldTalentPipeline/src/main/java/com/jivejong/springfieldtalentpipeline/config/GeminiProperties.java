package com.jivejong.springfieldtalentpipeline.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Gemini API configuration. The key itself is supplied by the {@code GEMINI_API_KEY} environment
 * variable and is never committed — {@code application.yml} only references the variable.
 *
 * <p>Model choices follow {@code docs/AI_FEATURES.md}: the smaller, faster model scores candidate
 * profiles, the larger one generates mock interviews, where response quality actually matters.
 */
@ConfigurationProperties(prefix = "gemini")
public class GeminiProperties {

    /** Gemini API key, from the GEMINI_API_KEY environment variable. Empty until one is supplied. */
    private String apiKey = "";

    /** OpenAI-compatible Gemini endpoint base URL. */
    private String baseUrl = "https://generativelanguage.googleapis.com/v1beta/openai";

    private final Models models = new Models();

    /**
     * Gemini 3 models think before replying, and thinking tokens count against
     * {@code max_completion_tokens}. Left at the default that inflates output-token usage well past
     * the estimates in docs/AI_FEATURES.md, for reasoning neither feature ever surfaces.
     *
     * <p>Gemini accepts "minimal" / "low" / "medium" / "high" here.
     */
    private String reasoningEffort = "low";

    private final Temperatures temperature = new Temperatures();

    private Integer maxCompletionTokens = 3000;

    private Duration connectTimeout = Duration.ofSeconds(10);

    /** Generous - a full interview generation is a large single response. */
    private Duration readTimeout = Duration.ofSeconds(120);

    public boolean isConfigured() {
        return apiKey != null && !apiKey.isBlank();
    }

    public String getReasoningEffort() {
        return reasoningEffort;
    }

    public void setReasoningEffort(String reasoningEffort) {
        this.reasoningEffort = reasoningEffort;
    }

    public Temperatures getTemperature() {
        return temperature;
    }

    public Integer getMaxCompletionTokens() {
        return maxCompletionTokens;
    }

    public void setMaxCompletionTokens(Integer maxCompletionTokens) {
        this.maxCompletionTokens = maxCompletionTokens;
    }

    public Duration getConnectTimeout() {
        return connectTimeout;
    }

    public void setConnectTimeout(Duration connectTimeout) {
        this.connectTimeout = connectTimeout;
    }

    public Duration getReadTimeout() {
        return readTimeout;
    }

    public void setReadTimeout(Duration readTimeout) {
        this.readTimeout = readTimeout;
    }

    public String getApiKey() {
        return apiKey;
    }

    public void setApiKey(String apiKey) {
        this.apiKey = apiKey;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public Models getModels() {
        return models;
    }

    /**
     * Per-feature sampling temperature.
     *
     * <p>These differ for a measured reason. Scoring at 0.4 produced fit scores ranging 20-70 for
     * the same candidate and role across repeated runs - unusable for a value that is cached and
     * then read as a judgement. docs/AI_FEATURES.md calls scoring "a short, fairly mechanical
     * summarization + scoring task", and mechanical is what a near-zero temperature buys. The
     * interview is the opposite case: character voice is the whole point, so it keeps room to vary.
     */
    public static class Temperatures {

        /** Near-deterministic: a fit score should not depend on when it happened to be generated. */
        private Double profile = 0.1;

        /** Personality matters more than repeatability here. */
        private Double interview = 0.8;

        public Double getProfile() {
            return profile;
        }

        public void setProfile(Double profile) {
            this.profile = profile;
        }

        public Double getInterview() {
            return interview;
        }

        public void setInterview(Double interview) {
            this.interview = interview;
        }
    }

    /** Per-feature model selection — see docs/AI_FEATURES.md for why these differ. */
    public static class Models {

        /** Feature 1: candidate profile + fit score. */
        private String profile = "gemini-3.1-flash-lite";

        /** Feature 2: single-shot structured mock interview. */
        private String interview = "gemini-3.1-flash-lite";

        public String getProfile() {
            return profile;
        }

        public void setProfile(String profile) {
            this.profile = profile;
        }

        public String getInterview() {
            return interview;
        }

        public void setInterview(String interview) {
            this.interview = interview;
        }
    }
}
