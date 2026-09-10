package com.jivejong.springfieldtalentpipeline.candidate;

import java.util.List;

/**
 * One page of the paginated character list. Page size is fixed at 20 by the API and is not
 * configurable, so {@code pages} is the only thing that tells us how far to walk.
 */
public record SimpsonsCharacterPage(
        int count, String next, String prev, int pages, List<SimpsonsCharacter> results) {

    public List<SimpsonsCharacter> resultsOrEmpty() {
        return results == null ? List.of() : results;
    }
}
