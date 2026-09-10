package com.jivejong.springfieldtalentpipeline.candidate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class CandidateSearchServiceTest {

    private CandidateRepository repository;
    private CandidateSearchService service;

    @BeforeEach
    void setUp() {
        repository = mock(CandidateRepository.class);
        service = new CandidateSearchService(repository);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"   ", "\t"})
    void anAbsentOrBlankQueryListsTheWholePool(String query) {
        when(repository.findAllByOrderByNameAsc())
                .thenReturn(List.of(new Candidate(1, "Homer Simpson")));

        assertThat(service.search(query)).hasSize(1);

        verify(repository).findAllByOrderByNameAsc();
        verify(repository, never())
                .findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
                        eq("any"), eq("any"));
    }

    @Test
    void aQuerySearchesNameAndOccupationWithTheSameTerm() {
        Candidate moe = new Candidate(16, "Moe Szyslak");
        when(repository.findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
                        "bartender", "bartender"))
                .thenReturn(List.of(moe));

        assertThat(service.search("bartender")).containsExactly(moe);
    }

    @Test
    void queriesAreTrimmedBeforeMatching() {
        // A trailing space from a search box should not change what matches.
        when(repository.findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
                        "burns", "burns"))
                .thenReturn(List.of(new Candidate(13, "Charles Montgomery Burns")));

        assertThat(service.search("  burns  ")).hasSize(1);

        verify(repository)
                .findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
                        "burns", "burns");
    }

    @Test
    void noMatchesIsAnEmptyListRatherThanAnError() {
        when(repository.findByNameContainingIgnoreCaseOrOccupationContainingIgnoreCaseOrderByNameAsc(
                        "astronaut", "astronaut"))
                .thenReturn(List.of());

        assertThat(service.search("astronaut")).isEmpty();
    }
}
