package com.jivejong.springfieldtalentpipeline.ai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateRepository;
import com.jivejong.springfieldtalentpipeline.config.GeminiProperties;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplication;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplicationRepository;
import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionRepository;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class MockInterviewServiceTest {

    private static final UUID APPLICATION_ID = UUID.randomUUID();
    private static final UUID CANDIDATE_ID = UUID.randomUUID();
    private static final UUID REQUISITION_ID = UUID.randomUUID();

    private MockInterviewStore store;
    private MockInterviewGenerator generator;
    private MockInterviewService service;

    @BeforeEach
    void setUp() {
        store = mock(MockInterviewStore.class);
        generator = mock(MockInterviewGenerator.class);
        JobApplicationRepository applications = mock(JobApplicationRepository.class);
        CandidateRepository candidates = mock(CandidateRepository.class);
        RequisitionRepository requisitions = mock(RequisitionRepository.class);

        when(applications.findById(APPLICATION_ID))
                .thenReturn(Optional.of(
                        new JobApplication(CANDIDATE_ID, REQUISITION_ID, Instant.now())));
        Candidate candidate = new Candidate(16, "Moe Szyslak");
        candidate.setOccupation("Bartender and Owner of Moe's Tavern");
        when(candidates.findWithPhrasesById(CANDIDATE_ID)).thenReturn(Optional.of(candidate));
        when(requisitions.findById(REQUISITION_ID))
                .thenReturn(Optional.of(new Requisition(
                        "Bartender",
                        "Food & Beverage",
                        "bartender tavern bar drinks",
                        "Marge Simpson",
                        LocalDate.now(),
                        Instant.now())));

        when(store.saveGenerated(any(), any(), any(), any(), any(), any()))
                .thenAnswer(invocation -> {
                    MockInterviewSession session = MockInterviewSession.generated(
                            invocation.getArgument(0),
                            invocation.getArgument(1),
                            invocation.getArgument(2),
                            invocation.getArgument(3),
                            invocation.getArgument(4));
                    List<MockInterviewGenerator.InterviewTurn> turns = invocation.getArgument(5);
                    int n = 1;
                    for (MockInterviewGenerator.InterviewTurn turn : turns) {
                        session.addTurn(n++, turn.question(), turn.answer());
                    }
                    return session;
                });

        service = new MockInterviewService(
                store, generator, applications, candidates, requisitions, new GeminiProperties());
    }

    private static MockInterviewGenerator.InterviewTurn turn(int n) {
        return new MockInterviewGenerator.InterviewTurn(n, "Question " + n, "Answer " + n);
    }

    private void generatorReturns(MockInterviewGenerator.InterviewGeneration generation) {
        when(generator.generate(any(), any()))
                .thenReturn(new GeminiClient.StructuredResult<>(
                        generation,
                        "gemini-3.1-flash-lite",
                        new GeminiClient.TokenUsage(600, 1400, 2000)));
    }

    @Test
    void storesEveryTurnAndTheAssessment() {
        generatorReturns(new MockInterviewGenerator.InterviewGeneration(
                List.of(turn(1), turn(2), turn(3), turn(4), turn(5)), "Held his own.", 4));

        MockInterviewService.InterviewResult result = service.generate(APPLICATION_ID);

        assertThat(result.session().getTurns()).hasSize(5);
        assertThat(result.session().getOverallAssessment()).isEqualTo("Held his own.");
        assertThat(result.session().getOverallRating()).isEqualTo(4);
        assertThat(result.session().getStatus()).isEqualTo(MockInterviewStatus.GENERATED);
        assertThat(result.usage().totalTokens()).isEqualTo(2000);
    }

    @Test
    void turnsAreRenumberedSequentiallyEvenIfTheModelMisnumbersThem() {
        generatorReturns(new MockInterviewGenerator.InterviewGeneration(
                List.of(
                        new MockInterviewGenerator.InterviewTurn(3, "Third", "C"),
                        new MockInterviewGenerator.InterviewTurn(1, "First", "A"),
                        new MockInterviewGenerator.InterviewTurn(7, "Seventh", "D"),
                        new MockInterviewGenerator.InterviewTurn(2, "Second", "B"),
                        new MockInterviewGenerator.InterviewTurn(null, "Unnumbered", "E")),
                "Fine.",
                3));

        MockInterviewService.InterviewResult result = service.generate(APPLICATION_ID);

        assertThat(result.session().getTurns())
                .extracting(MockInterviewTurn::getQuestionNumber)
                .containsExactly(1, 2, 3, 4, 5);
        assertThat(result.session().getTurns())
                .extracting(MockInterviewTurn::getQuestion)
                .containsExactly("First", "Second", "Third", "Seventh", "Unnumbered");
    }

    @Test
    void turnsMissingAQuestionOrAnswerAreDropped() {
        generatorReturns(new MockInterviewGenerator.InterviewGeneration(
                List.of(
                        turn(1),
                        new MockInterviewGenerator.InterviewTurn(2, "Question 2", null),
                        new MockInterviewGenerator.InterviewTurn(3, "   ", "Answer 3"),
                        turn(4)),
                "Fine.",
                3));

        MockInterviewService.InterviewResult result = service.generate(APPLICATION_ID);

        assertThat(result.session().getTurns()).hasSize(2);
        assertThat(result.session().getTurns())
                .extracting(MockInterviewTurn::getQuestionNumber)
                .containsExactly(1, 2);
    }

    @Test
    void aFailedGenerationIsRecordedRatherThanVanishing() {
        when(generator.generate(any(), any())).thenThrow(new GeminiException("upstream exploded"));

        assertThatThrownBy(() -> service.generate(APPLICATION_ID))
                .isInstanceOf(GeminiException.class);

        verify(store, times(1))
                .saveFailed(eq(APPLICATION_ID), anyString(), eq("gemini-3.1-flash-lite"));
        verify(store, never()).saveGenerated(any(), any(), any(), any(), any(), any());
    }

    @Test
    void anInterviewWithNoUsableTurnsIsAFailureNotAnEmptySuccess() {
        generatorReturns(new MockInterviewGenerator.InterviewGeneration(List.of(), "Nothing.", 3));

        assertThatThrownBy(() -> service.generate(APPLICATION_ID))
                .isInstanceOf(GeminiException.class)
                .hasMessageContaining("no usable turns");

        verify(store, times(1)).saveFailed(eq(APPLICATION_ID), anyString(), anyString());
        verify(store, never()).saveGenerated(any(), any(), any(), any(), any(), any());
    }

    @Test
    void anOutOfRangeRatingIsClamped() {
        generatorReturns(new MockInterviewGenerator.InterviewGeneration(
                List.of(turn(1), turn(2), turn(3), turn(4), turn(5)), "Superb.", 9));

        service.generate(APPLICATION_ID);

        ArgumentCaptor<Integer> rating = ArgumentCaptor.forClass(Integer.class);
        verify(store).saveGenerated(any(), any(), rating.capture(), any(), any(), any());
        assertThat(rating.getValue()).isEqualTo(5);
    }

    @Test
    void regeneratingAlwaysCreatesAnotherSessionRatherThanReplacingOne() {
        // Comparing attempts is the point - see docs/AI_FEATURES.md.
        generatorReturns(new MockInterviewGenerator.InterviewGeneration(
                List.of(turn(1), turn(2), turn(3), turn(4), turn(5)), "Fine.", 3));

        service.generate(APPLICATION_ID);
        service.generate(APPLICATION_ID);

        verify(store, times(2)).saveGenerated(any(), any(), any(), any(), any(), any());
    }
}
