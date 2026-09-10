package com.jivejong.springfieldtalentpipeline.offer;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateRepository;
import com.jivejong.springfieldtalentpipeline.pipeline.InvalidTransitionException;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplication;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplicationRepository;
import com.jivejong.springfieldtalentpipeline.pipeline.PipelineService;
import com.jivejong.springfieldtalentpipeline.pipeline.PipelineStage;
import java.text.NumberFormat;
import java.time.Instant;
import java.util.Locale;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Extending an offer and recording what the candidate did with it.
 *
 * <p>The accept/decline call is arithmetic against reference wage data - inside the published
 * 10th-90th percentile band for the matched occupation is an acceptance, outside it is a decline.
 * No model is involved, so the same offer always produces the same answer and the stored rationale
 * is a statement of fact rather than a generated opinion.
 *
 * <p>A decline moves the application to {@code WITHDRAWN}, not {@code REJECTED}: the candidate
 * walked away, the employer did not turn them down, and conflating the two would misreport why the
 * pipeline ended. Both are terminal, so an offer is one-shot - there is no second attempt on the
 * same application, which is also how a real offer works.
 *
 * <p>This is a wrapper around {@link PipelineService#transition}, never a bypass. The state machine
 * stays the only thing that decides whether a move is legal; if it refuses, that refusal surfaces
 * unchanged.
 */
@Service
public class OfferService {

    private static final Logger log = LoggerFactory.getLogger(OfferService.class);
    private static final NumberFormat MONEY = NumberFormat.getCurrencyInstance(Locale.US);

    static {
        MONEY.setMaximumFractionDigits(0);
    }

    private final OfferDecisionRepository decisions;
    private final JobApplicationRepository applications;
    private final CandidateRepository candidates;
    private final OccupationMatcher matcher;
    private final PipelineService pipeline;

    public OfferService(
            OfferDecisionRepository decisions,
            JobApplicationRepository applications,
            CandidateRepository candidates,
            OccupationMatcher matcher,
            PipelineService pipeline) {
        this.decisions = decisions;
        this.applications = applications;
        this.candidates = candidates;
        this.matcher = matcher;
        this.pipeline = pipeline;
    }

    public record OfferResult(OfferDecision decision, JobApplication application, boolean fellBack) {}

    @Transactional
    public OfferResult extendOffer(UUID applicationId, Integer offerAmount) {
        if (offerAmount == null || offerAmount <= 0) {
            throw new IllegalArgumentException("offerAmount must be a positive annual salary");
        }

        JobApplication application = applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));

        // An offer only means anything at OFFER. Refusing here mirrors the transition endpoint's own
        // 409 rather than inventing a second way to describe an illegal move.
        if (application.getCurrentStage() != PipelineStage.OFFER) {
            throw new InvalidTransitionException(
                    application.getCurrentStage(),
                    PipelineStage.OFFER,
                    Set.copyOf(pipeline.allowedNextStages(applicationId)));
        }

        Candidate candidate = candidates
                .findById(application.getCandidateId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No candidate " + application.getCandidateId()));

        OccupationMatcher.Match match = matcher.match(candidate.getOccupation());
        OccupationWage wage = match.wage();
        int low = wage.getPct10();
        int high = wage.getPct90();

        boolean within = offerAmount >= low && offerAmount <= high;
        OfferOutcome outcome = within ? OfferOutcome.ACCEPTED : OfferOutcome.DECLINED;

        OfferDecision decision = decisions.save(new OfferDecision(
                applicationId,
                offerAmount,
                wage.getSocCode(),
                wage.getOccTitle(),
                low,
                high,
                outcome,
                rationale(offerAmount, wage, low, high, within, match.fellBack()),
                Instant.now()));

        PipelineStage next = within ? PipelineStage.HIRED : PipelineStage.WITHDRAWN;
        JobApplication moved = pipeline.transition(
                applicationId,
                next,
                within
                        ? "Offer of %s accepted".formatted(MONEY.format(offerAmount))
                        : "Offer of %s declined".formatted(MONEY.format(offerAmount)));

        log.info(
                "Offer of {} for {} ({}): {} against {}-{} for {} \"{}\"",
                MONEY.format(offerAmount),
                candidate.getName(),
                applicationId,
                outcome,
                MONEY.format(low),
                MONEY.format(high),
                wage.getSocCode(),
                wage.getOccTitle());
        return new OfferResult(decision, moved, match.fellBack());
    }

    @Transactional(readOnly = true)
    public Optional<OfferDecision> findForApplication(UUID applicationId) {
        return decisions.findByApplicationId(applicationId);
    }

    /**
     * Templated, deliberately. This sentence is the audit trail for a decision that has already been
     * made arithmetically; generating it would make the record depend on a model.
     */
    private static String rationale(
            int offerAmount, OccupationWage wage, int low, int high, boolean within, boolean fellBack) {
        String scope = fellBack
                ? "the typical national range across all occupations"
                : "the typical national range for %s".formatted(wage.getOccTitle());
        String band = "%s–%s".formatted(MONEY.format(low), MONEY.format(high));

        if (within) {
            return "Offer of %s falls within %s (%s). Accepted."
                    .formatted(MONEY.format(offerAmount), scope, band);
        }
        String direction = offerAmount < low ? "below" : "above";
        return "Offer of %s falls %s %s (%s). Declined."
                .formatted(MONEY.format(offerAmount), direction, scope, band);
    }
}
