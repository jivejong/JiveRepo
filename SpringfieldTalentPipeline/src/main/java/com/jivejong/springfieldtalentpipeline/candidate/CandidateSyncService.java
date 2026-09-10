package com.jivejong.springfieldtalentpipeline.candidate;

import com.jivejong.springfieldtalentpipeline.config.SimpsonsApiProperties;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * Walks the whole character list and upserts it into the candidate pool.
 *
 * <p>Deliberately not transactional as a whole, and deliberately not parallel: each record commits
 * independently via {@link CandidateUpserter} so one bad record is isolated and reported rather
 * than failing the batch, and pages are fetched serially with a pause between them (see
 * docs/SIMPSONS_API.md on being a reasonable citizen against a free, unmetered API).
 */
@Service
public class CandidateSyncService {

    /** Cap on failures echoed back in the response; the {@code failed} count is always exact. */
    public static final int MAX_REPORTED_FAILURES = 50;

    private static final Logger log = LoggerFactory.getLogger(CandidateSyncService.class);

    private final SimpsonsApiClient client;
    private final CandidateUpserter upserter;
    private final SimpsonsApiProperties properties;

    public CandidateSyncService(
            SimpsonsApiClient client, CandidateUpserter upserter, SimpsonsApiProperties properties) {
        this.client = client;
        this.upserter = upserter;
        this.properties = properties;
    }

    public SyncSummary syncAll() {
        Instant startedAt = Instant.now();
        long startNanos = System.nanoTime();
        log.info("Starting candidate sync from {}", properties.getBaseUrl());

        SimpsonsCharacterPage firstPage;
        try {
            firstPage = client.fetchPage(1);
        } catch (SimpsonsApiException e) {
            // Nothing to isolate here: without page 1 there is no page count and no work to do.
            log.error("Candidate sync aborted - could not fetch the first page", e);
            throw e;
        }

        int pagesExpected = Math.max(firstPage.pages(), 1);
        Counters counters = new Counters();
        List<SyncSummary.FailedRecord> failures = new ArrayList<>();

        ingestPage(firstPage, startedAt, counters, failures);

        for (int page = 2; page <= pagesExpected; page++) {
            pause(properties.getPageDelay());
            try {
                ingestPage(client.fetchPage(page), startedAt, counters, failures);
            } catch (SimpsonsApiException e) {
                // A page that will not load costs us its 20 records, not the other 1,160.
                counters.pagesFailed++;
                log.warn("Skipping page {} of {}: {}", page, pagesExpected, e.getMessage());
            }
        }

        SyncSummary summary = new SyncSummary(
                startedAt,
                Duration.ofNanos(System.nanoTime() - startNanos).toMillis(),
                pagesExpected,
                counters.pagesFetched,
                counters.pagesFailed,
                counters.recordsSeen,
                counters.created,
                counters.updated,
                counters.failed,
                List.copyOf(failures));

        log.info(
                "Candidate sync finished in {} ms: {} seen, {} created, {} updated, {} failed, "
                        + "{}/{} pages fetched",
                summary.durationMs(),
                summary.recordsSeen(),
                summary.created(),
                summary.updated(),
                summary.failed(),
                summary.pagesFetched(),
                summary.pagesExpected());
        return summary;
    }

    private void ingestPage(
            SimpsonsCharacterPage page,
            Instant syncedAt,
            Counters counters,
            List<SyncSummary.FailedRecord> failures) {
        counters.pagesFetched++;
        for (SimpsonsCharacter character : page.resultsOrEmpty()) {
            counters.recordsSeen++;
            try {
                switch (upserter.upsert(character, syncedAt)) {
                    case CREATED -> counters.created++;
                    case UPDATED -> counters.updated++;
                }
            } catch (Exception e) {
                counters.failed++;
                if (failures.size() < MAX_REPORTED_FAILURES) {
                    failures.add(new SyncSummary.FailedRecord(
                            character.id(), character.name(), describe(e)));
                }
                log.warn(
                        "Skipping character id={} name={}: {}",
                        character.id(),
                        character.name(),
                        describe(e));
            }
        }
    }

    private static String describe(Exception e) {
        String message = e.getMessage();
        return message == null || message.isBlank()
                ? e.getClass().getSimpleName()
                : e.getClass().getSimpleName() + ": " + message;
    }

    private static void pause(Duration delay) {
        if (delay == null || delay.isZero() || delay.isNegative()) {
            return;
        }
        try {
            Thread.sleep(delay.toMillis());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new SimpsonsApiException("Candidate sync interrupted", e);
        }
    }

    private static final class Counters {
        private int pagesFetched;
        private int pagesFailed;
        private int recordsSeen;
        private int created;
        private int updated;
        private int failed;
    }
}
