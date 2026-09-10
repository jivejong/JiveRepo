package com.jivejong.springfieldtalentpipeline.offer;

import java.util.List;
import java.util.NoSuchElementException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Resolves a candidate's free-text occupation to a wage reference row.
 *
 * <p>A confident match returns that occupation's row; anything weaker falls back to the aggregate
 * "All Occupations" row rather than blocking the offer. Falling back is a normal outcome here, not
 * an error: the candidate pool is fictional characters whose occupations include "Owner of the
 * Kwik-E-Mart" and "Bartender and Owner of Moe's Tavern", and plenty of them will not correspond to
 * a real occupation code at all.
 */
@Component
public class OccupationMatcher {

    /**
     * Minimum {@code ts_rank} score to trust a specific occupation over the aggregate row.
     *
     * <p>Chosen against the scores this project has already measured on the same scoring function:
     * with normalisation flag 1, a single matched term on a short document scores around 0.0076-0.013
     * and a two-term match roughly doubles that. A single common word matching by coincidence -
     * "owner" against "Owner of Virgin", say - lands at the bottom of that band, so the threshold
     * sits just under a solid single-term hit. Set it higher and legitimate one-word matches like
     * "Bartender" would be thrown away; set it lower and any incidental shared word would be trusted
     * enough to price someone's salary.
     *
     * <p>This is a judgement call on a scoring function with no cross-document IDF, which is exactly
     * why the fallback exists rather than the threshold being treated as precise.
     */
    static final double MIN_CONFIDENCE = 0.0070;

    private static final Logger log = LoggerFactory.getLogger(OccupationMatcher.class);

    private final OccupationWageRepository wages;

    public OccupationMatcher(OccupationWageRepository wages) {
        this.wages = wages;
    }

    /**
     * The resolved wage row plus how it was reached.
     *
     * @param confidence the {@code ts_rank} score of the winning match, or 0 when it fell back
     */
    public record Match(OccupationWage wage, double confidence, boolean fellBack) {}

    @Transactional(readOnly = true)
    public Match match(String occupation) {
        if (occupation == null || occupation.isBlank()) {
            return fallback("no occupation on record", 0d);
        }

        List<OccupationWage> candidates = wages.findByOccupationText(occupation);
        if (candidates.isEmpty()) {
            return fallback("no occupation title matched \"%s\"".formatted(occupation), 0d);
        }

        OccupationWage best = candidates.get(0);
        double confidence = wages.rankFor(best.getSocCode(), occupation).orElse(0d);

        if (confidence < MIN_CONFIDENCE) {
            return fallback(
                    "best match \"%s\" scored %.5f, below the %.4f threshold"
                            .formatted(best.getOccTitle(), confidence, MIN_CONFIDENCE),
                    confidence);
        }
        if (!best.hasUsableRange()) {
            // The source publishes some rows without both percentile ends; those cannot price an
            // offer, so treat them as no match rather than inventing a bound.
            return fallback(
                    "matched \"%s\" but it has no usable wage range".formatted(best.getOccTitle()),
                    confidence);
        }

        log.debug(
                "Occupation \"{}\" matched {} \"{}\" at {}",
                occupation,
                best.getSocCode(),
                best.getOccTitle(),
                confidence);
        return new Match(best, confidence, false);
    }

    private Match fallback(String reason, double confidence) {
        OccupationWage aggregate = wages
                .findById(OccupationWage.AGGREGATE_SOC_CODE)
                .orElseThrow(() -> new NoSuchElementException(
                        "Wage reference data is missing its aggregate row ("
                                + OccupationWage.AGGREGATE_SOC_CODE
                                + "). Has the import been run?"));
        log.debug("Falling back to the aggregate wage row: {}", reason);
        return new Match(aggregate, confidence, true);
    }
}
