package com.jivejong.springfieldtalentpipeline.offer;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * Wage reference lookup, including the occupation-title match.
 *
 * <p>The query is the same technique already proven for requisition matching: OR-joined
 * {@code plainto_tsquery} scored with {@code ts_rank}, length-normalised with flag 1. Going through
 * {@code plainto_tsquery} first keeps stemming and stop-word handling and stops arbitrary
 * occupation text from reaching {@code to_tsquery}, where punctuation would be a syntax error.
 *
 * <p>This inherits the same documented {@code ts_rank} limitation as requisition matching - no
 * corpus-wide IDF, so among equal-match-count rows document length is the only tiebreaker. That is
 * accepted behaviour here, not a defect to solve again: the confidence threshold in
 * {@link OccupationMatcher} exists precisely so a weak match falls back rather than being trusted.
 */
public interface OccupationWageRepository extends JpaRepository<OccupationWage, String> {

    @Query(
            value =
                    """
                    SELECT w.*,
                           ts_rank(to_tsvector('english', w.occ_title),
                                   replace(plainto_tsquery('english', :occupation)::text,
                                           '&', '|')::tsquery,
                                   1) AS rank
                    FROM occupation_wage w
                    WHERE w.soc_code <> '00-0000'
                      AND to_tsvector('english', w.occ_title)
                          @@ replace(plainto_tsquery('english', :occupation)::text,
                                     '&', '|')::tsquery
                    ORDER BY rank DESC, length(w.occ_title) ASC
                    LIMIT 5
                    """,
            nativeQuery = true)
    List<OccupationWage> findByOccupationText(@Param("occupation") String occupation);

    @Query(
            value =
                    """
                    SELECT ts_rank(to_tsvector('english', w.occ_title),
                                   replace(plainto_tsquery('english', :occupation)::text,
                                           '&', '|')::tsquery,
                                   1)
                    FROM occupation_wage w
                    WHERE w.soc_code = :socCode
                    """,
            nativeQuery = true)
    Optional<Double> rankFor(@Param("socCode") String socCode, @Param("occupation") String occupation);
}
