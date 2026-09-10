package com.jivejong.springfieldtalentpipeline.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;

/** Binding checks for the Groq config. Deliberately context-light: no database required. */
class GroqPropertiesTest {

    private final ApplicationContextRunner runner =
            new ApplicationContextRunner().withUserConfiguration(TestConfig.class);

    @Test
    void bindsApiKeyFromConfiguration() {
        runner.withPropertyValues("groq.api-key=key-from-environment")
                .run(context -> {
                    GroqProperties properties = context.getBean(GroqProperties.class);
                    assertThat(properties.getApiKey()).isEqualTo("key-from-environment");
                    assertThat(properties.isConfigured()).isTrue();
                });
    }

    @Test
    void reportsNotConfiguredWhenKeyIsAbsent() {
        runner.run(context ->
                assertThat(context.getBean(GroqProperties.class).isConfigured()).isFalse());
    }

    @Test
    void defaultsToTheModelsChosenInAiFeaturesDoc() {
        runner.run(context -> {
            GroqProperties properties = context.getBean(GroqProperties.class);
            assertThat(properties.getBaseUrl()).isEqualTo("https://api.groq.com/openai/v1");
            assertThat(properties.getModels().getProfile()).isEqualTo("openai/gpt-oss-20b");
            assertThat(properties.getModels().getInterview()).isEqualTo("openai/gpt-oss-120b");
        });
    }

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(GroqProperties.class)
    static class TestConfig {}
}
