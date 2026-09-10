package com.jivejong.springfieldtalentpipeline.ai;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import java.util.Map;

/**
 * Wire types for Groq's OpenAI-compatible chat completions API.
 *
 * <p>Both AI features use structured JSON output, which constrains two fields together:
 * {@code reasoning_format} must be {@code parsed} or {@code hidden} when JSON mode is in use -
 * {@code raw} is rejected in that combination - and {@code reasoning_effort} is set low so the
 * GPT-OSS models do not burn output tokens on reasoning nobody reads. See docs/AI_FEATURES.md.
 */
final class GroqChat {

    private GroqChat() {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Request(
            String model,
            List<Message> messages,
            @JsonProperty("response_format") ResponseFormat responseFormat,
            @JsonProperty("reasoning_effort") String reasoningEffort,
            @JsonProperty("reasoning_format") String reasoningFormat,
            Double temperature,
            @JsonProperty("max_completion_tokens") Integer maxCompletionTokens) {}

    record Message(String role, String content) {

        static Message system(String content) {
            return new Message("system", content);
        }

        static Message user(String content) {
            return new Message("user", content);
        }
    }

    record ResponseFormat(String type, @JsonProperty("json_schema") JsonSchema jsonSchema) {

        static ResponseFormat jsonSchema(String name, Map<String, Object> schema) {
            return new ResponseFormat("json_schema", new JsonSchema(name, true, schema));
        }
    }

    record JsonSchema(String name, Boolean strict, Map<String, Object> schema) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Response(List<Choice> choices, Usage usage, String model) {

        String firstContent() {
            if (choices == null || choices.isEmpty() || choices.get(0).message() == null) {
                return null;
            }
            return choices.get(0).message().content();
        }
    }

    record Choice(Message message, @JsonProperty("finish_reason") String finishReason) {}

    record Usage(
            @JsonProperty("prompt_tokens") Integer promptTokens,
            @JsonProperty("completion_tokens") Integer completionTokens,
            @JsonProperty("total_tokens") Integer totalTokens) {}
}
