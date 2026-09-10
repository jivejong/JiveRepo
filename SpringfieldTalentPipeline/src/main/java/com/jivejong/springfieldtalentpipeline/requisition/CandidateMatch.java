package com.jivejong.springfieldtalentpipeline.requisition;

import java.util.UUID;

/**
 * One ranked candidate for a requisition.
 *
 * <p>Spring Data closed projection: the native query selects exactly these columns, so matching
 * never loads the phrase collection for 1,182 candidates just to rank their occupations.
 */
public interface CandidateMatch {

    UUID getId();

    Integer getExternalId();

    String getName();

    String getOccupation();

    String getCharacterStatus();

    /** {@code ts_rank} score - higher is a better textual match. */
    Double getRank();
}
