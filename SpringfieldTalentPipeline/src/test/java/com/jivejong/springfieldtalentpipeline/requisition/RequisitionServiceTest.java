package com.jivejong.springfieldtalentpipeline.requisition;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class RequisitionServiceTest {

    private static final UUID ID = UUID.randomUUID();

    private RequisitionService service;
    private Requisition requisition;
    private Instant originalKeywordsUpdatedAt;

    @BeforeEach
    void setUp() {
        RequisitionRepository repository = mock(RequisitionRepository.class);
        originalKeywordsUpdatedAt = Instant.now().minus(1, ChronoUnit.HOURS);
        requisition = new Requisition(
                "Bartender",
                "Food & Beverage",
                "bartender tavern bar drinks",
                "Marge Simpson",
                LocalDate.now(),
                originalKeywordsUpdatedAt);
        when(repository.findById(ID)).thenReturn(Optional.of(requisition));
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        service = new RequisitionService(repository);
    }

    @Test
    void changingKeywordsMovesTheStalenessClock() {
        // This is the whole point of the endpoint: it is what invalidates cached AI profiles.
        Requisition updated = service.update(ID, null, null, "mixologist cocktails", null, null);

        assertThat(updated.getTargetKeywords()).isEqualTo("mixologist cocktails");
        assertThat(updated.getKeywordsUpdatedAt()).isAfter(originalKeywordsUpdatedAt);
    }

    @Test
    void resubmittingIdenticalKeywordsDoesNotMoveTheClock() {
        // Otherwise re-saving an unchanged form would silently cost a round of regeneration.
        Requisition updated = service.update(ID, null, null, "bartender tavern bar drinks", null, null);

        assertThat(updated.getKeywordsUpdatedAt()).isEqualTo(originalKeywordsUpdatedAt);
    }

    @Test
    void whitespaceOnlyDifferencesAreNotAChange() {
        Requisition updated =
                service.update(ID, null, null, "  bartender tavern bar drinks  ", null, null);

        assertThat(updated.getKeywordsUpdatedAt()).isEqualTo(originalKeywordsUpdatedAt);
    }

    @Test
    void editingOtherFieldsLeavesKeywordsAndTheirClockAlone() {
        Requisition updated =
                service.update(ID, "Head Bartender", null, null, "Homer Simpson", null);

        assertThat(updated.getTitle()).isEqualTo("Head Bartender");
        assertThat(updated.getHiringManager()).isEqualTo("Homer Simpson");
        assertThat(updated.getTargetKeywords()).isEqualTo("bartender tavern bar drinks");
        assertThat(updated.getKeywordsUpdatedAt()).isEqualTo(originalKeywordsUpdatedAt);
    }

    @Test
    void omittedFieldsAreLeftAlone() {
        Requisition updated = service.update(ID, null, null, null, null, RequisitionStatus.FILLED);

        assertThat(updated.getStatus()).isEqualTo(RequisitionStatus.FILLED);
        assertThat(updated.getTitle()).isEqualTo("Bartender");
        assertThat(updated.getDepartment()).isEqualTo("Food & Beverage");
        assertThat(updated.getHiringManager()).isEqualTo("Marge Simpson");
    }

    @Test
    void blankKeywordsAreRejectedRatherThanBreakingMatching() {
        // An empty tsquery would match nothing; better a 400 than a requisition that silently
        // stops matching.
        assertThatThrownBy(() -> service.update(ID, null, null, "   ", null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("targetKeywords");
    }

    @Test
    void blankTitleIsRejected() {
        assertThatThrownBy(() -> service.update(ID, "  ", null, null, null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("title");
    }

    @Test
    void createRejectsMissingKeywords() {
        assertThatThrownBy(() -> service.create("Bartender", null, null, null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("targetKeywords");
    }
}
