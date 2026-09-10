package com.jivejong.springfieldtalentpipeline.requisition;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.time.LocalDate;
import java.util.UUID;

/** A job opening. See docs/DATA_MODEL.md. */
@Entity
@Table(name = "requisition")
public class Requisition {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(nullable = false)
    private String title;

    @Column private String department;

    /**
     * Free text used as the full-text search query against {@code candidate.occupation}. Also the
     * cache key for the AI profile in Phase 3: a profile is stale once these change.
     */
    @Column(name = "target_keywords", nullable = false, columnDefinition = "text")
    private String targetKeywords;

    @Column(name = "hiring_manager")
    private String hiringManager;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private RequisitionStatus status = RequisitionStatus.OPEN;

    @Column(name = "opened_date", nullable = false)
    private LocalDate openedDate;

    /** Not in the spec's table; needed so Phase 3 can tell whether a cached profile is stale. */
    @Column(name = "keywords_updated_at", nullable = false)
    private Instant keywordsUpdatedAt;

    protected Requisition() {
        // for JPA
    }

    public Requisition(
            String title,
            String department,
            String targetKeywords,
            String hiringManager,
            LocalDate openedDate,
            Instant now) {
        this.title = title;
        this.department = department;
        this.targetKeywords = targetKeywords;
        this.hiringManager = hiringManager;
        this.openedDate = openedDate;
        this.status = RequisitionStatus.OPEN;
        this.keywordsUpdatedAt = now;
    }

    public UUID getId() {
        return id;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getDepartment() {
        return department;
    }

    public void setDepartment(String department) {
        this.department = department;
    }

    public String getTargetKeywords() {
        return targetKeywords;
    }

    public void updateTargetKeywords(String targetKeywords, Instant now) {
        if (!java.util.Objects.equals(this.targetKeywords, targetKeywords)) {
            this.targetKeywords = targetKeywords;
            this.keywordsUpdatedAt = now;
        }
    }

    public String getHiringManager() {
        return hiringManager;
    }

    public void setHiringManager(String hiringManager) {
        this.hiringManager = hiringManager;
    }

    public RequisitionStatus getStatus() {
        return status;
    }

    public void setStatus(RequisitionStatus status) {
        this.status = status;
    }

    public LocalDate getOpenedDate() {
        return openedDate;
    }

    public Instant getKeywordsUpdatedAt() {
        return keywordsUpdatedAt;
    }
}
