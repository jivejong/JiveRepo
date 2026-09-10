package com.jivejong.springfieldtalentpipeline.candidate;

import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Lookup and browsing over the candidate pool.
 *
 * <p>Separate from {@link CandidateSyncService}, which writes; this only reads. It is also
 * deliberately separate from requisition matching: that ranks by relevance and lives with
 * {@code Requisition}, whereas this answers "find me this person".
 */
@Service
public class CandidateSearchService {

    private final CandidateRepository candidates;

    public CandidateSearchService(CandidateRepository candidates) {
        this.candidates = candidates;
    }

    /**
     * Candidates whose name or occupation contains {@code query}, case-insensitively; the whole pool
     * (alphabetical) when {@code query} is absent or blank.
     *
     * <p>Read-only and transactional so the entities stay managed while the caller maps them.
     * Nothing here touches {@code phrases}, so that collection is never loaded - which is what keeps
     * listing all 1,182 candidates a single query rather than 1,183.
     */
    @Transactional(readOnly = true)
    public List<Candidate> search(String query) {
        if (query == null || query.isBlank()) {
            return candidates.findAllByOrderByNameAsc();
        }
        String trimmed = query.trim();
        return candidates.findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
                trimmed, trimmed);
    }
}
