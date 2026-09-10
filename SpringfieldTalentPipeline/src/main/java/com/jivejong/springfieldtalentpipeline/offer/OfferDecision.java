package com.jivejong.springfieldtalentpipeline.offer;

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
 * The candidate's answer to a salary offer, and the evidence behind it.
 *
 * <p>Every field except {@code offerAmount} is derived, and none of it comes from a model. The
 * matched occupation and the wage range are stored rather than looked up on read so the record
 * stays explainable even if the reference data is later re-imported: a decision made in the past
 * should still be able to account for itself.
 *
 * <p>{@code decisionRationale} is templated for the same reason. It is a statement of arithmetic
 * that already happened, and generating it would make an auditable record depend on a model's mood.
 */
@Entity
@Table(
        name = "offer_decision",
        indexes = @Index(name = "idx_offer_decision_application", columnList = "application_id"))
public class OfferDecision {

    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;

    @Column(name = "application_id", nullable = false)
    private UUID applicationId;

    /** Recruiter-entered annual salary, in whole dollars. */
    @Column(name = "offer_amount", nullable = false)
    private Integer offerAmount;

    /** Resolved by full-text search, or the aggregate code when nothing matched confidently. */
    @Column(name = "matched_soc_code", nullable = false, length = 16)
    private String matchedSocCode;

    @Column(name = "matched_occupation_title", nullable = false, columnDefinition = "text")
    private String matchedOccupationTitle;

    @Column(name = "wage_range_low", nullable = false)
    private Integer wageRangeLow;

    @Column(name = "wage_range_high", nullable = false)
    private Integer wageRangeHigh;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private OfferOutcome decision;

    @Column(name = "decision_rationale", nullable = false, columnDefinition = "text")
    private String decisionRationale;

    @Column(name = "decided_at", nullable = false)
    private Instant decidedAt;

    protected OfferDecision() {
        // for JPA
    }

    public OfferDecision(
            UUID applicationId,
            Integer offerAmount,
            String matchedSocCode,
            String matchedOccupationTitle,
            Integer wageRangeLow,
            Integer wageRangeHigh,
            OfferOutcome decision,
            String decisionRationale,
            Instant decidedAt) {
        this.applicationId = applicationId;
        this.offerAmount = offerAmount;
        this.matchedSocCode = matchedSocCode;
        this.matchedOccupationTitle = matchedOccupationTitle;
        this.wageRangeLow = wageRangeLow;
        this.wageRangeHigh = wageRangeHigh;
        this.decision = decision;
        this.decisionRationale = decisionRationale;
        this.decidedAt = decidedAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getApplicationId() {
        return applicationId;
    }

    public Integer getOfferAmount() {
        return offerAmount;
    }

    public String getMatchedSocCode() {
        return matchedSocCode;
    }

    public String getMatchedOccupationTitle() {
        return matchedOccupationTitle;
    }

    public Integer getWageRangeLow() {
        return wageRangeLow;
    }

    public Integer getWageRangeHigh() {
        return wageRangeHigh;
    }

    public OfferOutcome getDecision() {
        return decision;
    }

    public String getDecisionRationale() {
        return decisionRationale;
    }

    public Instant getDecidedAt() {
        return decidedAt;
    }

    public boolean wasAccepted() {
        return decision == OfferOutcome.ACCEPTED;
    }

    /** True when the decision fell back to the aggregate row rather than a specific occupation. */
    public boolean usedFallbackOccupation() {
        return OccupationWage.AGGREGATE_SOC_CODE.equals(matchedSocCode);
    }
}
