package com.jivejong.springfieldtalentpipeline.candidate;

import java.time.Instant;
import java.util.List;

/**
 * Outcome of a sync run. Reported rather than thrown: docs/SIMPSONS_API.md calls for isolating bad
 * records and logging a summary, not failing the whole batch on one of them.
 *
 * @param failures capped at {@link CandidateSyncService#MAX_REPORTED_FAILURES}; {@code failed} is
 *     the true count
 */
public record SyncSummary(
        Instant startedAt,
        long durationMs,
        int pagesExpected,
        int pagesFetched,
        int pagesFailed,
        int recordsSeen,
        int created,
        int updated,
        int failed,
        List<FailedRecord> failures) {

    public record FailedRecord(Integer externalId, String name, String reason) {}

    public int totalPersisted() {
        return created + updated;
    }
}
