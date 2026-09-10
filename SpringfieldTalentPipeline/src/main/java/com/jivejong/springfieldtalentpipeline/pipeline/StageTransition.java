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

/** Audit log - one row per stage move. See docs/DATA_MODEL.md. */
@Entity
@Table(
        name = "stage_transition",
        indexes = @Index(name = "idx_stage_transition_application", columnList = "application_id"))
public class StageTransition {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "application_id", nullable = false)
    private UUID applicationId;

    /** Null only for the synthetic row recording an application's creation. */
    @Enumerated(EnumType.STRING)
    @Column(name = "from_stage", length = 32)
    private PipelineStage fromStage;

    @Enumerated(EnumType.STRING)
    @Column(name = "to_stage", nullable = false, length = 32)
    private PipelineStage toStage;

    @Column(name = "transitioned_at", nullable = false)
    private Instant transitionedAt;

    @Column(columnDefinition = "text")
    private String note;

    protected StageTransition() {
        // for JPA
    }

    public StageTransition(
            UUID applicationId,
            PipelineStage fromStage,
            PipelineStage toStage,
            Instant transitionedAt,
            String note) {
        this.applicationId = applicationId;
        this.fromStage = fromStage;
        this.toStage = toStage;
        this.transitionedAt = transitionedAt;
        this.note = note;
    }

    public UUID getId() {
        return id;
    }

    public UUID getApplicationId() {
        return applicationId;
    }

    public PipelineStage getFromStage() {
        return fromStage;
    }

    public PipelineStage getToStage() {
        return toStage;
    }

    public Instant getTransitionedAt() {
        return transitionedAt;
    }

    public String getNote() {
        return note;
    }
}
