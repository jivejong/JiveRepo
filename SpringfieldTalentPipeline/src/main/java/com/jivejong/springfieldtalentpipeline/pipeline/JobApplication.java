package com.jivejong.springfieldtalentpipeline.pipeline;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * Join of Candidate and Requisition - the actual pipeline record.
 *
 * <p>Named {@code JobApplication} rather than {@code Application} to avoid colliding with
 * {@code SpringfieldTalentPipelineApplication} and Spring's own {@code ApplicationContext}
 * vocabulary. See docs/DATA_MODEL.md.
 *
 * <p>{@code currentStage} is only ever changed through the state machine - see
 * {@link PipelineService}.
 */
@Entity
@Table(
        name = "job_application",
        indexes = {
            @Index(name = "idx_job_application_candidate", columnList = "candidate_id"),
            @Index(name = "idx_job_application_requisition", columnList = "requisition_id")
        })
public class JobApplication {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "candidate_id", nullable = false)
    private UUID candidateId;

    @Column(name = "requisition_id", nullable = false)
    private UUID requisitionId;

    @Enumerated(EnumType.STRING)
    @Column(name = "current_stage", nullable = false, length = 32)
    private PipelineStage currentStage = PipelineStage.SOURCED;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected JobApplication() {
        // for JPA
    }

    public JobApplication(UUID candidateId, UUID requisitionId, Instant createdAt) {
        this.candidateId = candidateId;
        this.requisitionId = requisitionId;
        this.currentStage = PipelineStage.SOURCED;
        this.createdAt = createdAt;
        this.updatedAt = createdAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getCandidateId() {
        return candidateId;
    }

    public UUID getRequisitionId() {
        return requisitionId;
    }

    public PipelineStage getCurrentStage() {
        return currentStage;
    }

    void moveTo(PipelineStage stage, Instant at) {
        this.currentStage = stage;
        this.updatedAt = at;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
