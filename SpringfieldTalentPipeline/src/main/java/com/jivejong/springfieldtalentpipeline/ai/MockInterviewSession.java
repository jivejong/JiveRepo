package com.jivejong.springfieldtalentpipeline.ai;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OrderBy;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * One structured mock interview. Generated in a single Groq call, not a live back-and-forth - see
 * docs/AI_FEATURES.md.
 *
 * <p>Unlike {@link AiCandidateProfile}, which is overwritten on refresh, regenerating an interview
 * creates a <em>new</em> session: a recruiter can then compare attempts. Nothing reading these
 * should assume an application has only one.
 */
@Entity
@Table(
        name = "mock_interview_session",
        indexes = @Index(name = "idx_interview_session_application", columnList = "application_id"))
public class MockInterviewSession {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "application_id", nullable = false)
    private UUID applicationId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private MockInterviewStatus status;

    /**
     * The model's own read on how the fictional candidate performed. Explicitly <em>not</em> a
     * stand-in for recruiter judgement - that is {@code RecruiterFeedback}, deliberately separate.
     * On a failed session this holds the failure reason instead.
     */
    @Column(name = "overall_assessment", columnDefinition = "text")
    private String overallAssessment;

    /** 1-5, generated alongside the assessment. Null on a failed session. */
    @Column(name = "overall_rating")
    private Integer overallRating;

    @Column(name = "model_used", nullable = false)
    private String modelUsed;

    @Column(name = "generated_at", nullable = false)
    private Instant generatedAt;

    @OneToMany(
            mappedBy = "session",
            cascade = CascadeType.ALL,
            orphanRemoval = true)
    @OrderBy("questionNumber ASC")
    private List<MockInterviewTurn> turns = new ArrayList<>();

    protected MockInterviewSession() {
        // for JPA
    }

    private MockInterviewSession(
            UUID applicationId,
            MockInterviewStatus status,
            String modelUsed,
            Instant generatedAt) {
        this.applicationId = applicationId;
        this.status = status;
        this.modelUsed = modelUsed;
        this.generatedAt = generatedAt;
    }

    public static MockInterviewSession generated(
            UUID applicationId,
            String overallAssessment,
            Integer overallRating,
            String modelUsed,
            Instant generatedAt) {
        MockInterviewSession session = new MockInterviewSession(
                applicationId, MockInterviewStatus.GENERATED, modelUsed, generatedAt);
        session.overallAssessment = overallAssessment;
        session.overallRating = overallRating;
        return session;
    }

    /**
     * A recorded failed attempt. Kept rather than discarded so a run of failures is visible instead
     * of looking like nobody ever tried - docs/DATA_MODEL.md puts Failed in the status enum for
     * exactly this.
     */
    public static MockInterviewSession failed(
            UUID applicationId, String reason, String modelUsed, Instant attemptedAt) {
        MockInterviewSession session = new MockInterviewSession(
                applicationId, MockInterviewStatus.FAILED, modelUsed, attemptedAt);
        session.overallAssessment = reason;
        return session;
    }

    public void addTurn(int questionNumber, String question, String answer) {
        turns.add(new MockInterviewTurn(this, questionNumber, question, answer));
    }

    public UUID getId() {
        return id;
    }

    public UUID getApplicationId() {
        return applicationId;
    }

    public MockInterviewStatus getStatus() {
        return status;
    }

    public String getOverallAssessment() {
        return overallAssessment;
    }

    public Integer getOverallRating() {
        return overallRating;
    }

    public String getModelUsed() {
        return modelUsed;
    }

    public Instant getGeneratedAt() {
        return generatedAt;
    }

    public List<MockInterviewTurn> getTurns() {
        return turns;
    }
}
