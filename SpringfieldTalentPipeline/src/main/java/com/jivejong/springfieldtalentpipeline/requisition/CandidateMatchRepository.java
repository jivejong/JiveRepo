package com.jivejong.springfieldtalentpipeline.requisition;

import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import java.util.UUID;

/**
 * Postgres full-text search over {@code candidate.occupation}.
 *
 * <p>Native rather than JPQL because {@code to_tsvector} / {@code ts_rank} have no JPA equivalent -
 * this is the actual point of the feature, not a workaround.
 *
 * <p>The {@code to_tsvector('english', coalesce(occupation, ''))} expression must stay
 * character-for-character identical to the one in the GIN index (see {@code data.sql}), or Postgres
 * silently falls back to a sequential scan.
 *
 * <p><strong>Why the query is OR-ed rather than used as-is:</strong> {@code plainto_tsquery} joins
 * its terms with AND, so target keywords of "bartender tavern bar drinks" would require a candidate
 * whose occupation matches all four - which matches nobody, not even Moe Szyslak. Matching is a
 * ranked "who is closest to this" question, not a filter, so the terms are re-joined with OR by
 * rewriting the parsed query. Going through {@code plainto_tsquery} first still does the useful
 * work - lexing, stemming and stop-word removal - and keeps arbitrary recruiter-typed text from
 * reaching {@code to_tsquery}, which would throw a syntax error on characters like {@code &} or
 * {@code !}. {@code ts_rank} then does the discriminating: a candidate matching two terms outranks
 * one matching a single term.
 *
 * <p><strong>Why the {@code 1} normalisation argument:</strong> it divides the rank by
 * {@code 1 + log(document length)}. Without it, every candidate matching exactly one query term
 * scores identically, so a long unrelated occupation that happens to contain one generic keyword
 * ties with a short exact match and the order collapses to alphabetical. Measured against the pool:
 * "Receptionist for the Rubber Baby Buggy Bumper Babysitting Service" tied with Moe Szyslak on a
 * query containing "service"; with normalisation it sorts last.
 *
 * <p>Two approaches that look right and are not, both measured before settling on this:
 *
 * <ul>
 *   <li>{@code ts_rank_cd} (cover density) changes nothing here. It scores how close matched terms
 *       sit to one another, and when each document matches a single term there is no distance to
 *       measure - every result came back at exactly 0.1.
 *   <li>IDF weighting would be new machinery, not a tuning flag - {@code ts_rank} reads no
 *       corpus-wide statistics at all - and it backfires on a corpus this size anyway. "servic"
 *       appears in 1 of 1,182 occupations and "bartend" in 4, so weighting by inverse document
 *       frequency would rank the babysitting receptionist <em>above</em> the bartenders. Term
 *       rarity within 1,182 short strings is not a proxy for term importance.
 * </ul>
 *
 * <p><strong>Residual limitation:</strong> among candidates matching the same number of query terms,
 * document length is the <em>only</em> tiebreaker. Which term matched contributes nothing to the
 * score - "Owner of Barney's Bowlarama" matching "owner" and "Bartender at Moho House" matching
 * "bartender" both come back at exactly 0.010132118, being the same length and the same match
 * count. So an actual bartender can sort below "Owner of Virgin" for a bartending role purely
 * because that string is one lexeme shorter.
 *
 * <p>That is this same normalisation seen from its bad side: demoting a long incidental match and
 * demoting a long relevant match are one behaviour, and it cannot tell which term mattered. Ranking
 * reflects only what the keywords distinguish - several candidates who are literally bartenders,
 * against keywords saying nothing sharper, genuinely tie.
 */
public interface CandidateMatchRepository extends JpaRepository<Candidate, UUID> {

    @Query(
            value =
                    """
                    SELECT c.id                AS id,
                           c.external_id       AS externalId,
                           c.name              AS name,
                           c.occupation        AS occupation,
                           c.character_status  AS characterStatus,
                           ts_rank(to_tsvector('english', coalesce(c.occupation, '')),
                                   replace(plainto_tsquery('english', :keywords)::text,
                                           '&', '|')::tsquery,
                                   1) AS rank
                    FROM candidate c
                    WHERE to_tsvector('english', coalesce(c.occupation, ''))
                          @@ replace(plainto_tsquery('english', :keywords)::text,
                                     '&', '|')::tsquery
                    ORDER BY rank DESC, c.name ASC
                    LIMIT :maxResults
                    """,
            nativeQuery = true)
    List<CandidateMatch> findMatches(
            @Param("keywords") String keywords, @Param("maxResults") int maxResults);
}
