package com.jivejong.springfieldtalentpipeline.web;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateSearchService;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateSyncService;
import com.jivejong.springfieldtalentpipeline.candidate.CharacterStatus;
import com.jivejong.springfieldtalentpipeline.candidate.SimpsonsApiClient;
import com.jivejong.springfieldtalentpipeline.candidate.SimpsonsApiException;
import com.jivejong.springfieldtalentpipeline.candidate.SyncSummary;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/candidates")
public class CandidateController {

    private final CandidateSyncService syncService;
    private final CandidateSearchService searchService;
    private final SimpsonsApiClient simpsonsApiClient;

    public CandidateController(
            CandidateSyncService syncService,
            CandidateSearchService searchService,
            SimpsonsApiClient simpsonsApiClient) {
        this.syncService = syncService;
        this.searchService = searchService;
        this.simpsonsApiClient = simpsonsApiClient;
    }

    /** Catchphrases are left out: this is a list view, and most of the pool has none anyway. */
    public record CandidateResponse(
            UUID id,
            Integer externalId,
            String name,
            Integer age,
            String gender,
            String occupation,
            CharacterStatus characterStatus,
            String portraitUrl) {}

    /**
     * Search or list candidates.
     *
     * <p>{@code q} is a case-insensitive substring match against name and occupation. Omit it and
     * you get the whole pool, alphabetically.
     *
     * <p>Substring matching, not the full-text search behind
     * {@code GET /api/requisitions/{id}/matches}: this is lookup, not relevance ranking. Someone
     * typing "burns" wants Charles Montgomery Burns, and would be baffled to have their input
     * stemmed, stop-worded, and AND-ed across terms.
     */
    @GetMapping
    public List<CandidateResponse> search(@RequestParam(required = false) String q) {
        return searchService.search(q).stream().map(this::toResponse).toList();
    }

    private CandidateResponse toResponse(Candidate candidate) {
        return new CandidateResponse(
                candidate.getId(),
                candidate.getExternalId(),
                candidate.getName(),
                candidate.getAge(),
                candidate.getGender(),
                candidate.getOccupation(),
                candidate.getCharacterStatus(),
                simpsonsApiClient.resolvePortraitUrl(candidate.getPortraitPath()));
    }

    /**
     * Triggers a full sync from The Simpsons API. Runs synchronously and takes roughly a minute for
     * the full ~1,182 characters, since pages are fetched serially with a deliberate pause.
     */
    @PostMapping("/sync")
    public SyncSummary sync() {
        return syncService.syncAll();
    }

    /** The upstream API being unreachable is a 502, not a 500 - the fault is not ours. */
    @ExceptionHandler(SimpsonsApiException.class)
    public ResponseEntity<Map<String, String>> handleUpstreamFailure(SimpsonsApiException e) {
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(Map.of("error", "The Simpsons API is unreachable", "detail", e.getMessage()));
    }
}
