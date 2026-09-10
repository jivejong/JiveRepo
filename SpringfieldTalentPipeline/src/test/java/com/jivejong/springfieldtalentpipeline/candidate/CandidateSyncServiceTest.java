package com.jivejong.springfieldtalentpipeline.candidate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.jivejong.springfieldtalentpipeline.config.SimpsonsApiProperties;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CandidateSyncServiceTest {

    private SimpsonsApiClient client;
    private CandidateUpserter upserter;
    private CandidateSyncService service;

    @BeforeEach
    void setUp() {
        client = mock(SimpsonsApiClient.class);
        upserter = mock(CandidateUpserter.class);
        SimpsonsApiProperties properties = new SimpsonsApiProperties();
        properties.setPageDelay(Duration.ZERO); // no reason to be polite to a mock
        service = new CandidateSyncService(client, upserter, properties);
    }

    private static SimpsonsCharacter character(int id, String name) {
        return new SimpsonsCharacter(
                id, null, "Male", name, "Bartender", "/character/%d.webp".formatted(id), List.of(), "Alive");
    }

    private static SimpsonsCharacterPage page(int totalPages, SimpsonsCharacter... characters) {
        return new SimpsonsCharacterPage(characters.length, null, null, totalPages, List.of(characters));
    }

    @Test
    void walksEveryPageAndCountsCreatesAndUpdates() {
        when(client.fetchPage(1)).thenReturn(page(2, character(1, "Homer"), character(2, "Marge")));
        when(client.fetchPage(2)).thenReturn(page(2, character(3, "Bart")));
        when(upserter.upsert(any(), any()))
                .thenReturn(CandidateUpserter.Outcome.CREATED)
                .thenReturn(CandidateUpserter.Outcome.UPDATED)
                .thenReturn(CandidateUpserter.Outcome.CREATED);

        SyncSummary summary = service.syncAll();

        assertThat(summary.pagesExpected()).isEqualTo(2);
        assertThat(summary.pagesFetched()).isEqualTo(2);
        assertThat(summary.recordsSeen()).isEqualTo(3);
        assertThat(summary.created()).isEqualTo(2);
        assertThat(summary.updated()).isEqualTo(1);
        assertThat(summary.failed()).isZero();
        assertThat(summary.failures()).isEmpty();
    }

    @Test
    void isolatesOneBadRecordInsteadOfFailingTheBatch() {
        SimpsonsCharacter good = character(1, "Homer");
        SimpsonsCharacter bad = character(2, "Corrupt");
        SimpsonsCharacter alsoGood = character(3, "Bart");
        when(client.fetchPage(1)).thenReturn(page(1, good, bad, alsoGood));
        when(upserter.upsert(eq(good), any())).thenReturn(CandidateUpserter.Outcome.CREATED);
        when(upserter.upsert(eq(alsoGood), any())).thenReturn(CandidateUpserter.Outcome.CREATED);
        doThrow(new IllegalArgumentException("character 2 has no name"))
                .when(upserter)
                .upsert(eq(bad), any());

        SyncSummary summary = service.syncAll();

        assertThat(summary.recordsSeen()).isEqualTo(3);
        assertThat(summary.created()).isEqualTo(2);
        assertThat(summary.failed()).isEqualTo(1);
        assertThat(summary.failures())
                .singleElement()
                .satisfies(failure -> {
                    assertThat(failure.externalId()).isEqualTo(2);
                    assertThat(failure.reason()).contains("has no name");
                });
    }

    @Test
    void skipsAnUnreachablePageAndKeepsGoing() {
        when(client.fetchPage(1)).thenReturn(page(3, character(1, "Homer")));
        when(client.fetchPage(2)).thenThrow(new SimpsonsApiException("503 from upstream"));
        when(client.fetchPage(3)).thenReturn(page(3, character(3, "Bart")));
        when(upserter.upsert(any(), any())).thenReturn(CandidateUpserter.Outcome.CREATED);

        SyncSummary summary = service.syncAll();

        assertThat(summary.pagesFetched()).isEqualTo(2);
        assertThat(summary.pagesFailed()).isEqualTo(1);
        assertThat(summary.created()).isEqualTo(2);
    }

    @Test
    void abortsWhenTheVeryFirstPageCannotBeFetched() {
        // Without page 1 there is no page count, so there is no partial run to salvage.
        when(client.fetchPage(1)).thenThrow(new SimpsonsApiException("connection refused"));

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> service.syncAll())
                .isInstanceOf(SimpsonsApiException.class);
    }

    @Test
    void stampsEveryRecordInOneRunWithTheSameTimestamp() {
        when(client.fetchPage(1)).thenReturn(page(1, character(1, "Homer"), character(2, "Marge")));
        when(upserter.upsert(any(), any())).thenReturn(CandidateUpserter.Outcome.CREATED);

        service.syncAll();

        org.mockito.ArgumentCaptor<Instant> stamps = org.mockito.ArgumentCaptor.forClass(Instant.class);
        org.mockito.Mockito.verify(upserter, org.mockito.Mockito.times(2))
                .upsert(any(), stamps.capture());
        assertThat(stamps.getAllValues()).containsOnly(stamps.getAllValues().get(0));
    }
}
