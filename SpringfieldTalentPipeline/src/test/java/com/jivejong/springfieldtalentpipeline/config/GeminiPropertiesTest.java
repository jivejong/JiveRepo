package com.jivejong.springfieldtalentpipeline.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

/** Binding checks for the Gemini config. Deliberately context-light: no database required. */
class GeminiPropertiesTest {

    private final ApplicationContextRunner runner =
            new ApplicationContextRunner().withUserConfiguration(TestConfig.class);

    @Test
    void bindsApiKeyFromConfiguration() {
        runner.withPropertyValues("gemini.api-key=key-from-environment")
                .run(context -> {
                    GeminiProperties properties = context.getBean(GeminiProperties.class);
                    assertThat(properties.getApiKey()).isEqualTo("key-from-environment");
                    assertThat(properties.isConfigured()).isTrue();
                });
    }

    @Test
    void reportsNotConfiguredWhenKeyIsAbsent() {
        runner.run(context ->
                assertThat(context.getBean(GeminiProperties.class).isConfigured()).isFalse());
    }

    @Test
    void defaultsToTheModelsChosenInAiFeaturesDoc() {
        runner.run(context -> {
            GeminiProperties properties = context.getBean(GeminiProperties.class);
            assertThat(properties.getBaseUrl()).isEqualTo("https://generativelanguage.googleapis.com/v1beta/openai");
            assertThat(properties.getModels().getProfile()).isEqualTo("gemini-3.1-flash-lite");
            assertThat(properties.getModels().getInterview()).isEqualTo("gemini-3.1-flash-lite");
        });
    }

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(GeminiProperties.class)
    static class TestConfig {}
}
