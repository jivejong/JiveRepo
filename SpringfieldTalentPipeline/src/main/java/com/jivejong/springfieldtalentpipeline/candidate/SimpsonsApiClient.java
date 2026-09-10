package com.jivejong.springfieldtalentpipeline.candidate;

import com.jivejong.springfieldtalentpipeline.config.SimpsonsApiProperties;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** Read-only client for The Simpsons API. See docs/SIMPSONS_API.md. */
@Component
public class SimpsonsApiClient {

    private final RestClient restClient;
    private final SimpsonsApiProperties properties;

    public SimpsonsApiClient(SimpsonsApiProperties properties) {
        this.properties = properties;
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(properties.getConnectTimeout());
        requestFactory.setReadTimeout(properties.getReadTimeout());
        this.restClient = RestClient.builder()
                .baseUrl(properties.getBaseUrl())
                .requestFactory(requestFactory)
                .build();
    }

    /**
     * Fetches one page of characters. Pages are 1-based and fixed at 20 items.
     *
     * @throws SimpsonsApiException if the page cannot be retrieved or parsed
     */
    public SimpsonsCharacterPage fetchPage(int page) {
        try {
            SimpsonsCharacterPage body = restClient
                    .get()
                    .uri(uriBuilder -> uriBuilder.path("/characters").queryParam("page", page).build())
                    .retrieve()
                    .body(SimpsonsCharacterPage.class);
            if (body == null) {
                throw new SimpsonsApiException("Empty response body for page " + page);
            }
            return body;
        } catch (SimpsonsApiException e) {
            throw e;
        } catch (Exception e) {
            throw new SimpsonsApiException("Failed to fetch characters page " + page, e);
        }
    }

    /** Absolute URL for a stored {@code portraitPath}, or null if the candidate has none. */
    public String resolvePortraitUrl(String portraitPath) {
        if (portraitPath == null || portraitPath.isBlank()) {
            return null;
        }
        String base = properties.getCdnBaseUrl();
        String trimmedBase = base.endsWith("/") ? base.substring(0, base.length() - 1) : base;
        String path = portraitPath.startsWith("/") ? portraitPath : "/" + portraitPath;
        return trimmedBase + path;
    }
}
