package com.jivejong.springfieldtalentpipeline.candidate;

import static org.assertj.core.api.Assertions.assertThat;

import com.jivejong.springfieldtalentpipeline.config.SimpsonsApiProperties;
import org.junit.jupiter.api.Test;

/** Portrait URL assembly only - no network involved. */
class SimpsonsApiClientPortraitTest {

    private SimpsonsApiClient clientWithCdnBase(String cdnBaseUrl) {
        SimpsonsApiProperties properties = new SimpsonsApiProperties();
        properties.setCdnBaseUrl(cdnBaseUrl);
        return new SimpsonsApiClient(properties);
    }

    @Test
    void resolvesAgainstTheConfirmedCdnBase() {
        assertThat(clientWithCdnBase("https://cdn.thesimpsonsapi.com/500")
                        .resolvePortraitUrl("/character/1.webp"))
                .isEqualTo("https://cdn.thesimpsonsapi.com/500/character/1.webp");
    }

    @Test
    void doesNotDoubleUpSlashes() {
        assertThat(clientWithCdnBase("https://cdn.thesimpsonsapi.com/500/")
                        .resolvePortraitUrl("/character/1.webp"))
                .isEqualTo("https://cdn.thesimpsonsapi.com/500/character/1.webp");
    }

    @Test
    void returnsNullWhenTheCandidateHasNoPortrait() {
        SimpsonsApiClient client = clientWithCdnBase("https://cdn.thesimpsonsapi.com/500");
        assertThat(client.resolvePortraitUrl(null)).isNull();
        assertThat(client.resolvePortraitUrl("  ")).isNull();
    }
}
