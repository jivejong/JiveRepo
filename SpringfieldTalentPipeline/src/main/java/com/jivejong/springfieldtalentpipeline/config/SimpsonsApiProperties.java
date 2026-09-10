package com.jivejong.springfieldtalentpipeline.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/** Configuration for The Simpsons API integration - see docs/SIMPSONS_API.md. */
@ConfigurationProperties(prefix = "simpsons")
public class SimpsonsApiProperties {

    /** Base URL. Fully open - no authentication of any kind. */
    private String baseUrl = "https://thesimpsonsapi.com/api";

    /**
     * Image CDN base. docs/SIMPSONS_API.md said not to guess this; it was confirmed against the
     * live CDN, which serves {@code {cdnBaseUrl}/character/{id}.webp} at widths 200, 500 and 1280.
     */
    private String cdnBaseUrl = "https://cdn.thesimpsonsapi.com/500";

    /**
     * Pause between page requests. There is no published rate limit, but firing ~60 requests
     * concurrently at a free API is not being a reasonable citizen.
     */
    private Duration pageDelay = Duration.ofMillis(200);

    private Duration connectTimeout = Duration.ofSeconds(10);

    private Duration readTimeout = Duration.ofSeconds(30);

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public String getCdnBaseUrl() {
        return cdnBaseUrl;
    }

    public void setCdnBaseUrl(String cdnBaseUrl) {
        this.cdnBaseUrl = cdnBaseUrl;
    }

    public Duration getPageDelay() {
        return pageDelay;
    }

    public void setPageDelay(Duration pageDelay) {
        this.pageDelay = pageDelay;
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
}
