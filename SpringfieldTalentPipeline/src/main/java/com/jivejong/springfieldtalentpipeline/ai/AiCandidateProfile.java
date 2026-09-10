package com.jivejong.springfieldtalentpipeline.ai;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

/**
 * Cached AI-generated profile and fit score. One per application, because fit is
 * requisition-specific rather than a property of the candidate alone. See docs/DATA_MODEL.md.
 *
 * <p>Regenerated only on an explicit refresh, or when the requisition's target keywords have
 * changed since {@code generatedAt} - never silently on every read.
 */
@Entity
@Table(
        name = "ai_candidate_profile",
        uniqueConstraints =
                @UniqueConstraint(
                        name = "uk_ai_profile_application",
                        columnNames = "application_id"))
public class AiCandidateProfile {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "application_id", nullable = false, unique = true)
    private UUID applicationId;

    @Column(name = "generated_bio", nullable = false, columnDefinition = "text")
    private String generatedBio;

    /** 0-100. */
    @Column(name = "fit_score", nullable = false)
    private Integer fitScore;

    @Column(name = "fit_rationale", nullable = false, columnDefinition = "text")
    private String fitRationale;

    @Column(name = "model_used", nullable = false)
    private String modelUsed;

    @Column(name = "generated_at", nullable = false)
    private Instant generatedAt;

    protected AiCandidateProfile() {
        // for JPA
    }

    public AiCandidateProfile(UUID applicationId) {
        this.applicationId = applicationId;
    }

    public UUID getId() {
        return id;
    }

    public UUID getApplicationId() {
        return applicationId;
    }

    public String getGeneratedBio() {
        return generatedBio;
    }

    public Integer getFitScore() {
        return fitScore;
    }

    public String getFitRationale() {
        return fitRationale;
    }

    public String getModelUsed() {
        return modelUsed;
    }

    public Instant getGeneratedAt() {
        return generatedAt;
    }

    /** Overwrites the cached content with a fresh generation. */
    public void apply(
            String generatedBio,
            int fitScore,
            String fitRationale,
            String modelUsed,
            Instant generatedAt) {
        this.generatedBio = generatedBio;
        this.fitScore = fitScore;
        this.fitRationale = fitRationale;
        this.modelUsed = modelUsed;
        this.generatedAt = generatedAt;
    }

    /**
     * True when this profile predates the requisition's current keywords, and so was scored against
     * a role description that no longer applies.
     */
    public boolean isStaleFor(Instant keywordsUpdatedAt) {
        return keywordsUpdatedAt != null
                && generatedAt != null
                && keywordsUpdatedAt.isAfter(generatedAt);
    }
}
