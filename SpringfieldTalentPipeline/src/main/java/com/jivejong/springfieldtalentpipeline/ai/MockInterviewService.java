package com.jivejong.springfieldtalentpipeline.ai;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateRepository;
import com.jivejong.springfieldtalentpipeline.config.GroqProperties;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplication;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplicationRepository;
import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionRepository;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/**
 * Generates and stores mock interviews.
 *
 * <p>Not cache-aside, unlike the AI profile: every request generates a <em>new</em> session, because
 * being able to compare attempts is the point (docs/AI_FEATURES.md). Reading existing sessions never
 * calls the model.
 *
 * <p>The Groq call deliberately happens outside any transaction. It takes seconds, and holding a
 * database connection open across it would tie up the pool for no reason; persistence happens
 * afterwards in {@link MockInterviewStore}, which is also why a failed attempt can be recorded even
 * though the request goes on to fail.
 */
@Service
public class MockInterviewService {

    private static final Logger log = LoggerFactory.getLogger(MockInterviewService.class);

    private final MockInterviewStore store;
    private final MockInterviewGenerator generator;
    private final JobApplicationRepository applications;
    private final CandidateRepository candidates;
    private final RequisitionRepository requisitions;
    private final GroqProperties properties;

    public MockInterviewService(
            MockInterviewStore store,
            MockInterviewGenerator generator,
            JobApplicationRepository applications,
            CandidateRepository candidates,
            RequisitionRepository requisitions,
            GroqProperties properties) {
        this.store = store;
        this.generator = generator;
        this.applications = applications;
        this.candidates = candidates;
        this.requisitions = requisitions;
        this.properties = properties;
    }

    public record InterviewResult(MockInterviewSession session, GroqClient.TokenUsage usage) {}

    public InterviewResult generate(UUID applicationId) {
        JobApplication application = applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));
        // Fetches the phrase collection in the same query: the prompt needs it, and there is no
        // transaction open around the Groq call to load it lazily later.
        Candidate candidate = candidates
                .findWithPhrasesById(application.getCandidateId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No candidate " + application.getCandidateId()));
        Requisition requisition = requisitions
                .findById(application.getRequisitionId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No requisition " + application.getRequisitionId()));

        GroqClient.StructuredResult<MockInterviewGenerator.InterviewGeneration> result;
        try {
            log.info(
                    "Generating mock interview: {} for '{}'",
                    candidate.getName(),
                    requisition.getTitle());
            result = generator.generate(candidate, requisition);
        } catch (GroqException e) {
            // Record the attempt before rethrowing, so a run of failures is visible rather than
            // looking like nobody ever tried.
            store.saveFailed(applicationId, e.getMessage(), properties.getModels().getInterview());
            throw e;
        }

        List<MockInterviewGenerator.InterviewTurn> turns = usableTurns(result.value());
        if (turns.isEmpty()) {
            String reason = "Groq returned an interview with no usable turns";
            store.saveFailed(applicationId, reason, result.modelUsed());
            throw new GroqException(reason);
        }

        MockInterviewSession session = store.saveGenerated(
                applicationId,
                result.value().overallAssessment(),
                clampRating(result.value().overallRating()),
                result.modelUsed(),
                Instant.now(),
                turns);
        log.info(
                "Mock interview {} stored: {} turns, rating {}",
                session.getId(),
                turns.size(),
                session.getOverallRating());
        return new InterviewResult(session, result.usage());
    }

    public List<MockInterviewSession> listForApplication(UUID applicationId) {
        return store.listForApplication(applicationId);
    }

    public MockInterviewSession getWithTurns(UUID sessionId) {
        return store.findWithTurns(sessionId)
                .orElseThrow(() -> new NoSuchElementException("No mock interview " + sessionId));
    }

    /**
     * Drops turns missing a question or answer and renumbers sequentially, so a model that skips or
     * repeats a questionNumber cannot produce a transcript with holes in it.
     */
    private static List<MockInterviewGenerator.InterviewTurn> usableTurns(
            MockInterviewGenerator.InterviewGeneration generated) {
        if (generated == null || generated.turns() == null) {
            return List.of();
        }
        return generated.turns().stream()
                .filter(turn -> turn != null
                        && turn.question() != null
                        && !turn.question().isBlank()
                        && turn.answer() != null
                        && !turn.answer().isBlank())
                .sorted(Comparator.comparingInt(turn ->
                        turn.questionNumber() == null ? Integer.MAX_VALUE : turn.questionNumber()))
                .toList();
    }

    /** The schema constrains this to 1-5; clamping guards the documented range regardless. */
    private static Integer clampRating(Integer rating) {
        return rating == null ? null : Math.max(1, Math.min(5, rating));
    }
}
