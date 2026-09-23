package com.jivejong.springfieldtalentpipeline.web;

import com.jivejong.springfieldtalentpipeline.ai.AiCandidateProfile;
import com.jivejong.springfieldtalentpipeline.ai.AiCandidateProfileService;
import com.jivejong.springfieldtalentpipeline.ai.GeminiClient;
import com.jivejong.springfieldtalentpipeline.ai.MockInterviewService;
import com.jivejong.springfieldtalentpipeline.ai.MockInterviewSession;
import com.jivejong.springfieldtalentpipeline.ai.MockInterviewStatus;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplication;
import com.jivejong.springfieldtalentpipeline.pipeline.PipelineService;
import com.jivejong.springfieldtalentpipeline.pipeline.PipelineStage;
import com.jivejong.springfieldtalentpipeline.pipeline.RecruiterFeedback;
import com.jivejong.springfieldtalentpipeline.pipeline.RecruiterFeedbackService;
import com.jivejong.springfieldtalentpipeline.pipeline.StageTransition;
import com.jivejong.springfieldtalentpipeline.offer.OfferDecision;
import com.jivejong.springfieldtalentpipeline.offer.OfferOutcome;
import com.jivejong.springfieldtalentpipeline.offer.OfferService;
import java.time.Instant;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/applications")
public class JobApplicationController {

    private final PipelineService pipeline;
    private final AiCandidateProfileService aiProfiles;
    private final MockInterviewService interviews;
    private final RecruiterFeedbackService recruiterFeedback;
    private final OfferService offers;

    public JobApplicationController(
            PipelineService pipeline,
            AiCandidateProfileService aiProfiles,
            MockInterviewService interviews,
            RecruiterFeedbackService recruiterFeedback,
            OfferService offers) {
        this.pipeline = pipeline;
        this.aiProfiles = aiProfiles;
        this.interviews = interviews;
        this.recruiterFeedback = recruiterFeedback;
        this.offers = offers;
    }

    public record CreateApplicationRequest(UUID candidateId, UUID requisitionId) {}

    /** {@code note} is optional and lands on the audit row for this move. */
    public record TransitionRequest(PipelineStage toStage, String note) {}

    public record ApplicationResponse(
            UUID id,
            UUID candidateId,
            UUID requisitionId,
            PipelineStage currentStage,
            Set<PipelineStage> allowedNextStages,
            Instant createdAt,
            Instant updatedAt) {}

    public record TransitionResponse(
            UUID applicationId,
            PipelineStage fromStage,
            PipelineStage toStage,
            Instant transitionedAt,
            String note) {

        static TransitionResponse of(StageTransition transition) {
            return new TransitionResponse(
                    transition.getApplicationId(),
                    transition.getFromStage(),
                    transition.getToStage(),
                    transition.getTransitionedAt(),
                    transition.getNote());
        }
    }

    @PostMapping
    public ResponseEntity<ApplicationResponse> create(@RequestBody CreateApplicationRequest request) {
        if (request.candidateId() == null || request.requisitionId() == null) {
            throw new IllegalArgumentException("candidateId and requisitionId are both required");
        }
        JobApplication application =
                pipeline.createApplication(request.candidateId(), request.requisitionId());
        return ResponseEntity.status(HttpStatus.CREATED).body(toResponse(application));
    }

    @GetMapping("/{id}")
    public ApplicationResponse get(@PathVariable UUID id) {
        return toResponse(pipeline.get(id));
    }

    /**
     * Moves an application to a new stage. Returns 409 with the current stage and the stages that
     * are actually reachable when the state machine rejects the move.
     */
    @PostMapping("/{id}/transition")
    public ApplicationResponse transition(
            @PathVariable UUID id, @RequestBody TransitionRequest request) {
        if (request.toStage() == null) {
            throw new IllegalArgumentException("toStage is required");
        }
        return toResponse(pipeline.transition(id, request.toStage(), request.note()));
    }

    /** The audit trail - one entry per stage move, oldest first. */
    @GetMapping("/{id}/history")
    public List<TransitionResponse> history(@PathVariable UUID id) {
        return pipeline.history(id).stream().map(TransitionResponse::of).toList();
    }

    /**
     * The AI profile and fit score.
     *
     * @param origin why the request did or did not reach the model; {@code tokens} is null whenever
     *     the answer came from cache, because no generation happened
     */
    public record AiProfileResponse(
            UUID applicationId,
            String bio,
            Integer fitScore,
            String fitRationale,
            String modelUsed,
            Instant generatedAt,
            boolean cached,
            AiCandidateProfileService.Origin origin,
            GeminiClient.TokenUsage tokens) {

        static AiProfileResponse of(AiCandidateProfileService.ProfileResult result) {
            AiCandidateProfile profile = result.profile();
            return new AiProfileResponse(
                    profile.getApplicationId(),
                    profile.getGeneratedBio(),
                    profile.getFitScore(),
                    profile.getFitRationale(),
                    profile.getModelUsed(),
                    profile.getGeneratedAt(),
                    result.wasCached(),
                    result.origin(),
                    result.usage());
        }
    }

    /**
     * Generates the AI profile and fit score, or returns the cached one.
     *
     * <p>Regenerates only when {@code refresh=true} or the requisition's target keywords have
     * changed since the cached profile was generated - never silently per request.
     */
    @PostMapping("/{id}/ai-profile")
    public AiProfileResponse aiProfile(
            @PathVariable UUID id, @RequestParam(defaultValue = "false") boolean refresh) {
        return AiProfileResponse.of(aiProfiles.generateOrGet(id, refresh));
    }

    /** Reads the cached profile without ever generating one. 404 if none exists yet. */
    @GetMapping("/{id}/ai-profile")
    public AiProfileResponse cachedAiProfile(@PathVariable UUID id) {
        AiCandidateProfile profile = aiProfiles
                .find(id)
                .orElseThrow(() -> new NoSuchElementException(
                        "No AI profile generated yet for application " + id));
        return AiProfileResponse.of(new AiCandidateProfileService.ProfileResult(
                profile, AiCandidateProfileService.Origin.CACHED, null));
    }

    public record InterviewTurnResponse(int questionNumber, String question, String answer) {}

    /**
     * A generated interview. {@code overallAssessment} and {@code overallRating} are the model's own
     * read on the transcript it just wrote - not recruiter judgement, which has its own endpoint
     * below and its own storage.
     */
    public record InterviewResponse(
            UUID sessionId,
            UUID applicationId,
            MockInterviewStatus status,
            List<InterviewTurnResponse> turns,
            String overallAssessment,
            Integer overallRating,
            String modelUsed,
            Instant generatedAt,
            GeminiClient.TokenUsage tokens) {

        static InterviewResponse of(MockInterviewSession session, GeminiClient.TokenUsage tokens) {
            return new InterviewResponse(
                    session.getId(),
                    session.getApplicationId(),
                    session.getStatus(),
                    session.getTurns().stream()
                            .map(turn -> new InterviewTurnResponse(
                                    turn.getQuestionNumber(), turn.getQuestion(), turn.getAnswer()))
                            .toList(),
                    session.getOverallAssessment(),
                    session.getOverallRating(),
                    session.getModelUsed(),
                    session.getGeneratedAt(),
                    tokens);
        }
    }

    /** Summary row; the transcript itself is fetched per session. */
    public record InterviewSummaryResponse(
            UUID sessionId,
            MockInterviewStatus status,
            int turnCount,
            Integer overallRating,
            String modelUsed,
            Instant generatedAt) {}

    /**
     * Generates a mock interview in one structured call.
     *
     * <p>Each request creates a NEW session rather than replacing the previous one, so attempts can
     * be compared. Nothing built on this should assume an application has only one.
     */
    @PostMapping("/{id}/mock-interview")
    public ResponseEntity<InterviewResponse> mockInterview(@PathVariable UUID id) {
        MockInterviewService.InterviewResult result = interviews.generate(id);
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(InterviewResponse.of(result.session(), result.usage()));
    }

    /** Every interview attempt for this application, newest first. */
    @GetMapping("/{id}/mock-interviews")
    public List<InterviewSummaryResponse> mockInterviews(@PathVariable UUID id) {
        return interviews.listForApplication(id).stream()
                .map(session -> new InterviewSummaryResponse(
                        session.getId(),
                        session.getStatus(),
                        session.getTurns().size(),
                        session.getOverallRating(),
                        session.getModelUsed(),
                        session.getGeneratedAt()))
                .toList();
    }

    public record FeedbackRequest(UUID sessionId, Integer rating, String comments) {}

    public record FeedbackResponse(
            UUID id,
            UUID applicationId,
            UUID sessionId,
            Integer rating,
            String comments,
            Instant createdAt) {

        static FeedbackResponse of(RecruiterFeedback feedback) {
            return new FeedbackResponse(
                    feedback.getId(),
                    feedback.getApplicationId(),
                    feedback.getSessionId(),
                    feedback.getRating(),
                    feedback.getComments(),
                    feedback.getCreatedAt());
        }
    }

    /**
     * The recruiter's own verdict, stored separately from the AI's self-assessment.
     * {@code sessionId} is optional - feedback can respond to a specific interview or stand alone.
     */
    @PostMapping("/{id}/feedback")
    public ResponseEntity<FeedbackResponse> recordFeedback(
            @PathVariable UUID id, @RequestBody FeedbackRequest request) {
        RecruiterFeedback saved = recruiterFeedback.record(
                id, request.sessionId(), request.rating(), request.comments());
        return ResponseEntity.status(HttpStatus.CREATED).body(FeedbackResponse.of(saved));
    }

    @GetMapping("/{id}/feedback")
    public List<FeedbackResponse> listFeedback(@PathVariable UUID id) {
        return recruiterFeedback.listForApplication(id).stream()
                .map(FeedbackResponse::of)
                .toList();
    }

    private ApplicationResponse toResponse(JobApplication application) {
        return new ApplicationResponse(
                application.getId(),
                application.getCandidateId(),
                application.getRequisitionId(),
                application.getCurrentStage(),
                pipeline.allowedNextStages(application.getId()),
                application.getCreatedAt(),
                application.getUpdatedAt());
    }

    public record OfferRequest(Integer offerAmount) {}

    /**
     * The offer and what the candidate did with it. {@code fellBackToAggregate} is surfaced so the
     * UI can be honest about whether the range came from the candidate's actual occupation or from
     * the all-occupations aggregate.
     */
    public record OfferResponse(
            UUID applicationId,
            Integer offerAmount,
            String matchedSocCode,
            String matchedOccupationTitle,
            Integer wageRangeLow,
            Integer wageRangeHigh,
            OfferOutcome decision,
            String decisionRationale,
            Instant decidedAt,
            boolean fellBackToAggregate,
            PipelineStage currentStage,
            Set<PipelineStage> allowedNextStages) {

        static OfferResponse of(OfferDecision decision, JobApplication application, boolean fellBack,
                Set<PipelineStage> allowed) {
            return new OfferResponse(
                    decision.getApplicationId(),
                    decision.getOfferAmount(),
                    decision.getMatchedSocCode(),
                    decision.getMatchedOccupationTitle(),
                    decision.getWageRangeLow(),
                    decision.getWageRangeHigh(),
                    decision.getDecision(),
                    decision.getDecisionRationale(),
                    decision.getDecidedAt(),
                    fellBack,
                    application.getCurrentStage(),
                    allowed);
        }
    }

    /**
     * Extends a salary offer to an application sitting at OFFER.
     *
     * <p>Accept/decline is decided against reference wage data, not by a model. Accepting moves the
     * application to HIRED; declining moves it to WITHDRAWN - the candidate walked away rather than
     * being turned down. Both are terminal, so this is one-shot.
     *
     * <p>Returns 409 if the application is not at OFFER, from the same state machine that governs
     * /transition.
     */
    @PostMapping("/{id}/offer")
    public ResponseEntity<OfferResponse> offer(
            @PathVariable UUID id, @RequestBody OfferRequest request) {
        OfferService.OfferResult result = offers.extendOffer(id, request.offerAmount());
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(OfferResponse.of(
                        result.decision(),
                        result.application(),
                        result.fellBack(),
                        pipeline.allowedNextStages(id)));
    }

    /** The offer decision for this application, if one has been made. */
    @GetMapping("/{id}/offer")
    public OfferResponse offer(@PathVariable UUID id) {
        OfferDecision decision = offers
                .findForApplication(id)
                .orElseThrow(() -> new NoSuchElementException("No offer made for application " + id));
        return OfferResponse.of(decision, pipeline.get(id), 
                com.jivejong.springfieldtalentpipeline.offer.OccupationWage.AGGREGATE_SOC_CODE
                        .equals(decision.getMatchedSocCode()),
                pipeline.allowedNextStages(id));
    }
}
