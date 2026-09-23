package com.jivejong.springfieldtalentpipeline.ai;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateRepository;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplication;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplicationRepository;
import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionRepository;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataIntegrityViolationException;

/** Cache-aside behaviour - the part the Phase 3 checkpoint scrutinises. No network involved. */
class AiCandidateProfileServiceTest {

    private static final UUID APPLICATION_ID = UUID.randomUUID();
    private static final UUID CANDIDATE_ID = UUID.randomUUID();
    private static final UUID REQUISITION_ID = UUID.randomUUID();

    private AiCandidateProfileStore profiles;
    private CandidateProfileGenerator generator;
    private AiCandidateProfileService service;
    private Requisition requisition;

    @BeforeEach
    void setUp() {
        profiles = mock(AiCandidateProfileStore.class);
        generator = mock(CandidateProfileGenerator.class);
        JobApplicationRepository applications = mock(JobApplicationRepository.class);
        CandidateRepository candidates = mock(CandidateRepository.class);
        RequisitionRepository requisitions = mock(RequisitionRepository.class);

        JobApplication application = new JobApplication(CANDIDATE_ID, REQUISITION_ID, Instant.now());
        when(applications.findById(APPLICATION_ID)).thenReturn(Optional.of(application));

        Candidate candidate = new Candidate(16, "Moe Szyslak");
        candidate.setOccupation("Bartender and Owner of Moe's Tavern");
        // The service fetches phrases eagerly, since the Gemini call runs outside a transaction.
        when(candidates.findWithPhrasesById(CANDIDATE_ID)).thenReturn(Optional.of(candidate));

        requisition = new Requisition(
                "Bartender",
                "Food & Beverage",
                "bartender tavern bar drinks",
                "Marge Simpson",
                LocalDate.now(),
                Instant.now().minus(1, ChronoUnit.HOURS));
        when(requisitions.findById(REQUISITION_ID)).thenReturn(Optional.of(requisition));

        when(profiles.persist(any(), any(), anyInt(), any(), any(), any())).thenAnswer(invocation -> {
            AiCandidateProfile stored = new AiCandidateProfile(invocation.getArgument(0));
            stored.apply(
                    invocation.getArgument(1),
                    invocation.getArgument(2),
                    invocation.getArgument(3),
                    invocation.getArgument(4),
                    invocation.getArgument(5));
            return stored;
        });
        when(generator.generate(any(), any()))
                .thenReturn(new GeminiClient.StructuredResult<>(
                        new CandidateProfileGenerator.ProfileGeneration(
                                "Runs his own tavern.", 88, "Direct match on bartending."),
                        "gemini-3.1-flash-lite",
                        new GeminiClient.TokenUsage(300, 200, 500)));

        service = new AiCandidateProfileService(
                profiles, applications, candidates, requisitions, generator);
    }

    private AiCandidateProfile cachedProfile(Instant generatedAt) {
        AiCandidateProfile profile = new AiCandidateProfile(APPLICATION_ID);
        profile.apply("Cached bio.", 70, "Cached rationale.", "gemini-3.1-flash-lite", generatedAt);
        return profile;
    }

    @Test
    void generatesWhenNoProfileExists() {
        when(profiles.find(APPLICATION_ID)).thenReturn(Optional.empty());

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, false);

        assertThat(result.origin())
                .isEqualTo(AiCandidateProfileService.Origin.GENERATED_FIRST_TIME);
        assertThat(result.wasCached()).isFalse();
        assertThat(result.profile().getFitScore()).isEqualTo(88);
        verify(generator, times(1)).generate(any(), any());
    }

    @Test
    void secondCallWithoutKeywordChangeDoesNotHitTheModel() {
        // The cache half of the Phase 3 checkpoint.
        when(profiles.find(APPLICATION_ID))
                .thenReturn(Optional.of(cachedProfile(Instant.now())));

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, false);

        assertThat(result.origin()).isEqualTo(AiCandidateProfileService.Origin.CACHED);
        assertThat(result.profile().getGeneratedBio()).isEqualTo("Cached bio.");
        assertThat(result.usage()).isNull();
        verify(generator, never()).generate(any(), any());
    }

    @Test
    void regeneratesWhenTargetKeywordsChangedAfterTheProfileWasGenerated() {
        AiCandidateProfile stale = cachedProfile(Instant.now().minus(2, ChronoUnit.HOURS));
        when(profiles.find(APPLICATION_ID)).thenReturn(Optional.of(stale));
        requisition.updateTargetKeywords("mixologist cocktails", Instant.now());

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, false);

        assertThat(result.origin())
                .isEqualTo(AiCandidateProfileService.Origin.REGENERATED_KEYWORDS_CHANGED);
        assertThat(result.profile().getFitScore()).isEqualTo(88);
        verify(generator, times(1)).generate(any(), any());
    }

    @Test
    void rewritingTheSameKeywordsDoesNotInvalidateTheCache() {
        // updateTargetKeywords only moves the clock when the text actually differs, so a no-op
        // edit must not cost a regeneration.
        AiCandidateProfile cached = cachedProfile(Instant.now());
        when(profiles.find(APPLICATION_ID)).thenReturn(Optional.of(cached));
        requisition.updateTargetKeywords("bartender tavern bar drinks", Instant.now());

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, false);

        assertThat(result.origin()).isEqualTo(AiCandidateProfileService.Origin.CACHED);
        verify(generator, never()).generate(any(), any());
    }

    @Test
    void refreshRegeneratesEvenWhenTheCacheIsFresh() {
        when(profiles.find(APPLICATION_ID))
                .thenReturn(Optional.of(cachedProfile(Instant.now())));

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, true);

        assertThat(result.origin())
                .isEqualTo(AiCandidateProfileService.Origin.REGENERATED_ON_REQUEST);
        assertThat(result.profile().getGeneratedBio()).isEqualTo("Runs his own tavern.");
        verify(generator, times(1)).generate(any(), any());
    }

    @Test
    void refreshWritesThroughTheSameApplicationRatherThanAccumulatingProfiles() {
        // One profile per application. The store owns find-or-create, so what matters here is that
        // a refresh persists against the same applicationId rather than creating a second record.
        when(profiles.find(APPLICATION_ID)).thenReturn(Optional.of(cachedProfile(Instant.now())));

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, true);

        verify(profiles, times(1))
                .persist(eq(APPLICATION_ID), any(), anyInt(), any(), any(), any());
        assertThat(result.profile().getApplicationId()).isEqualTo(APPLICATION_ID);
        assertThat(result.profile().getFitScore()).isEqualTo(88);
    }

    @Test
    void aConcurrentGenerationLosingTheInsertRaceReturnsTheWinnersProfile() {
        // React StrictMode makes the UI fire this twice for one application. The loser's insert
        // violates uk_ai_profile_application; surfacing that as a 500 was the reported bug.
        AiCandidateProfile winner = cachedProfile(Instant.now());
        when(profiles.find(APPLICATION_ID))
                .thenReturn(Optional.empty()) // the pre-generation read: nothing there yet
                .thenReturn(Optional.of(winner)); // the recovery read: the other request won
        when(profiles.persist(any(), any(), anyInt(), any(), any(), any()))
                .thenThrow(new DataIntegrityViolationException("uk_ai_profile_application"));

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, false);

        assertThat(result.profile()).isSameAs(winner);
        assertThat(result.origin()).isEqualTo(AiCandidateProfileService.Origin.CACHED);
        assertThat(result.wasCached()).isTrue();
    }

    @Test
    void aConstraintViolationWithNothingToRecoverStillPropagates() {
        // Only swallow the violation when a profile genuinely turns up; otherwise it is a real fault.
        when(profiles.find(APPLICATION_ID)).thenReturn(Optional.empty());
        when(profiles.persist(any(), any(), anyInt(), any(), any(), any()))
                .thenThrow(new DataIntegrityViolationException("something else entirely"));

        assertThatThrownBy(() -> service.generateOrGet(APPLICATION_ID, false))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void aScoreOutsideTheDocumentedRangeIsClamped() {
        when(profiles.find(APPLICATION_ID)).thenReturn(Optional.empty());
        when(generator.generate(any(), any()))
                .thenReturn(new GeminiClient.StructuredResult<>(
                        new CandidateProfileGenerator.ProfileGeneration("Bio.", 140, "Rationale."),
                        "gemini-3.1-flash-lite",
                        new GeminiClient.TokenUsage(1, 1, 2)));

        AiCandidateProfileService.ProfileResult result = service.generateOrGet(APPLICATION_ID, false);

        assertThat(result.profile().getFitScore()).isEqualTo(100);
    }
}
