package com.jivejong.springfieldtalentpipeline.pipeline;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.NoSuchElementException;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class RecruiterFeedbackServiceTest {

    private static final UUID APPLICATION_ID = UUID.randomUUID();

    private RecruiterFeedbackService service;

    @BeforeEach
    void setUp() {
        RecruiterFeedbackRepository feedback = mock(RecruiterFeedbackRepository.class);
        JobApplicationRepository applications = mock(JobApplicationRepository.class);
        when(applications.existsById(APPLICATION_ID)).thenReturn(true);
        when(feedback.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
        service = new RecruiterFeedbackService(feedback, applications);
    }

    @Test
    void recordsTheRecruitersOwnVerdictAgainstAnInterview() {
        UUID sessionId = UUID.randomUUID();

        RecruiterFeedback saved =
                service.record(APPLICATION_ID, sessionId, 2, "Reads better than he interviews.");

        assertThat(saved.getApplicationId()).isEqualTo(APPLICATION_ID);
        assertThat(saved.getSessionId()).isEqualTo(sessionId);
        assertThat(saved.getRating()).isEqualTo(2);
        assertThat(saved.getComments()).isEqualTo("Reads better than he interviews.");
        assertThat(saved.getCreatedAt()).isNotNull();
    }

    @Test
    void feedbackCanStandAloneWithoutAnInterview() {
        // sessionId is optional: a recruiter may have a view before any interview is generated.
        RecruiterFeedback saved = service.record(APPLICATION_ID, null, 4, "Worth a screen.");

        assertThat(saved.getSessionId()).isNull();
        assertThat(saved.getRating()).isEqualTo(4);
    }

    @ParameterizedTest
    @ValueSource(ints = {0, 6, -1, 100})
    void ratingsOutsideOneToFiveAreRejected(int rating) {
        assertThatThrownBy(() -> service.record(APPLICATION_ID, null, rating, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("rating");
    }

    @Test
    void ratingIsRequired() {
        assertThatThrownBy(() -> service.record(APPLICATION_ID, null, null, "no number"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void feedbackForAnUnknownApplicationIsRejected() {
        assertThatThrownBy(() -> service.record(UUID.randomUUID(), null, 3, null))
                .isInstanceOf(NoSuchElementException.class);
    }
}
