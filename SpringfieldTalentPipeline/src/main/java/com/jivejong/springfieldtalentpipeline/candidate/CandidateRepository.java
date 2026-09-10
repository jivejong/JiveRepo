package com.jivejong.springfieldtalentpipeline.candidate;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CandidateRepository extends JpaRepository<Candidate, UUID> {

    /** Lookup used by the sync to decide between insert and update. */
    Optional<Candidate> findByExternalId(Integer externalId);

    /**
     * Loads the candidate with their catchphrases in one query, for callers that need them outside a
     * transaction - the AI prompts do, and {@code phrases} is lazy by default.
     */
    @EntityGraph(attributePaths = "phrases")
    Optional<Candidate> findWithPhrasesById(UUID id);

    /** Whole pool, alphabetical - what {@code GET /api/candidates} returns with no query. */
    List<Candidate> findAllByOrderByNameAsc();

    /**
     * Case-insensitive substring match on name or occupation.
     *
     * <p>Deliberately {@code ILIKE} rather than the full-text search used for requisition matching:
     * this endpoint is for looking someone up, where "burns" should find "Charles Montgomery Burns"
     * and a partial word should still hit. Full-text search would stem the input, ignore stop words,
     * and - as the matches endpoint had to work around - AND the terms together, all of which are
     * wrong for a lookup box.
     */
    List<Candidate> findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
            String name, String occupation);
}
