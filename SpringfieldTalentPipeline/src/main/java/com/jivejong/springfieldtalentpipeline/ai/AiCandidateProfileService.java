package com.jivejong.springfieldtalentpipeline.ai;

import com.jivejong.springfieldtalentpipeline.candidate.Candidate;
import com.jivejong.springfieldtalentpipeline.candidate.CandidateRepository;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplication;
import com.jivejong.springfieldtalentpipeline.pipeline.JobApplicationRepository;
import com.jivejong.springfieldtalentpipeline.requisition.Requisition;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionRepository;
import java.time.Instant;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Cache-aside around the profile generation.
 *
 * <p>A stored profile is reused unless the caller explicitly asks for a refresh, or the
 * requisition's target keywords have changed since it was generated - a profile scored against a
 * role description that no longer applies is worse than no cache at all. See docs/DATA_MODEL.md.
 */
@Service
public class AiCandidateProfileService {

    private static final Logger log = LoggerFactory.getLogger(AiCandidateProfileService.class);

    private final AiCandidateProfileRepository profiles;
    private final JobApplicationRepository applications;
    private final CandidateRepository candidates;
    private final RequisitionRepository requisitions;
    private final CandidateProfileGenerator generator;

    public AiCandidateProfileService(
            AiCandidateProfileRepository profiles,
            JobApplicationRepository applications,
            CandidateRepository candidates,
            RequisitionRepository requisitions,
            CandidateProfileGenerator generator) {
        this.profiles = profiles;
        this.applications = applications;
        this.candidates = candidates;
        this.requisitions = requisitions;
        this.generator = generator;
    }

    /** Why a request did or did not hit the model - surfaced so the cache is observable. */
    public enum Origin {
        CACHED,
        GENERATED_FIRST_TIME,
        REGENERATED_KEYWORDS_CHANGED,
        REGENERATED_ON_REQUEST
    }

    public record ProfileResult(
            AiCandidateProfile profile, Origin origin, GroqClient.TokenUsage usage) {

        public boolean wasCached() {
            return origin == Origin.CACHED;
        }
    }

    @Transactional
    public ProfileResult generateOrGet(UUID applicationId, boolean refresh) {
        JobApplication application = applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));
        Requisition requisition = requisitions
                .findById(application.getRequisitionId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No requisition " + application.getRequisitionId()));

        Optional<AiCandidateProfile> existing = profiles.findByApplicationId(applicationId);

        if (existing.isPresent() && !refresh) {
            AiCandidateProfile cached = existing.get();
            if (!cached.isStaleFor(requisition.getKeywordsUpdatedAt())) {
                log.debug("Serving cached AI profile for application {}", applicationId);
                return new ProfileResult(cached, Origin.CACHED, null);
            }
        }

        Origin origin;
        if (existing.isEmpty()) {
            origin = Origin.GENERATED_FIRST_TIME;
        } else if (refresh) {
            origin = Origin.REGENERATED_ON_REQUEST;
        } else {
            origin = Origin.REGENERATED_KEYWORDS_CHANGED;
        }

        Candidate candidate = candidates
                .findById(application.getCandidateId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No candidate " + application.getCandidateId()));

        log.info(
                "Generating AI profile for {} against requisition '{}' ({})",
                candidate.getName(),
                requisition.getTitle(),
                origin);
        GroqClient.StructuredResult<CandidateProfileGenerator.ProfileGeneration> result =
                generator.generate(candidate, requisition);
        CandidateProfileGenerator.ProfileGeneration generated = result.value();

        if (generated.fitScore() == null) {
            throw new GroqException("Groq returned a profile with no fitScore");
        }

        AiCandidateProfile profile = existing.orElseGet(() -> new AiCandidateProfile(applicationId));
        profile.apply(
                generated.bio(),
                clampToScoreRange(generated.fitScore()),
                generated.fitRationale(),
                result.modelUsed(),
                Instant.now());
        return new ProfileResult(profiles.save(profile), origin, result.usage());
    }

    @Transactional(readOnly = true)
    public Optional<AiCandidateProfile> find(UUID applicationId) {
        return profiles.findByApplicationId(applicationId);
    }

    /**
     * The schema already constrains this to 0-100; clamping is belt-and-braces against a model that
     * ignores the bound, so a stray value cannot violate the column's documented range.
     */
    private static int clampToScoreRange(int fitScore) {
        return Math.max(0, Math.min(100, fitScore));
    }
}
