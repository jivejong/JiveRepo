package com.jivejong.springfieldtalentpipeline.pipeline;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * The human's own notes after reviewing a mock interview.
 *
 * <p>Deliberately a separate entity from {@code MockInterviewSession.overallAssessment}, not a
 * merge of the two. They answer different questions: the AI's assessment is its read on how the
 * fictional candidate performed in the transcript it just wrote, while this is the recruiter's
 * judgement of the candidate. Merging them would quietly launder a generated opinion into a human
 * one. Same for {@code rating} against the session's {@code overallRating} - see
 * docs/AI_FEATURES.md and docs/DATA_MODEL.md.
 */
@Entity
@Table(
        name = "recruiter_feedback",
        indexes = {
            @Index(name = "idx_feedback_application", columnList = "application_id"),
            @Index(name = "idx_feedback_session", columnList = "session_id")
        })
public class RecruiterFeedback {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "application_id", nullable = false)
    private UUID applicationId;

    /** Which interview this responds to, if any - feedback can also stand on its own. */
    @Column(name = "session_id")
    private UUID sessionId;

    /** The recruiter's own rating, independent of the AI's {@code overallRating}. */
    @Column(nullable = false)
    private Integer rating;

    @Column(columnDefinition = "text")
    private String comments;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected RecruiterFeedback() {
        // for JPA
    }

    public RecruiterFeedback(
            UUID applicationId, UUID sessionId, Integer rating, String comments, Instant createdAt) {
        this.applicationId = applicationId;
        this.sessionId = sessionId;
        this.rating = rating;
        this.comments = comments;
        this.createdAt = createdAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getApplicationId() {
        return applicationId;
    }

    public UUID getSessionId() {
        return sessionId;
    }

    public Integer getRating() {
        return rating;
    }

    public String getComments() {
        return comments;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
