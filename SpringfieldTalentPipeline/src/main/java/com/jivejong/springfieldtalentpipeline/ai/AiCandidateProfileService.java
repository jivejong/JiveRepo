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
import org.springframework.dao.DataIntegrityViolationException;
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

    private final AiCandidateProfileStore profiles;
    private final JobApplicationRepository applications;
    private final CandidateRepository candidates;
    private final RequisitionRepository requisitions;
    private final CandidateProfileGenerator generator;

    public AiCandidateProfileService(
            AiCandidateProfileStore profiles,
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
            AiCandidateProfile profile, Origin origin, GeminiClient.TokenUsage usage) {

        public boolean wasCached() {
            return origin == Origin.CACHED;
        }
    }

    /**
     * Cache-aside, and safe to call twice for the same application.
     *
     * <p>Deliberately not {@code @Transactional}: the Gemini call in the middle takes seconds, and
     * wrapping the whole method would pin a database connection for its duration. Reads and the
     * write each get their own short transaction instead.
     *
     * <p>Concurrent callers are expected rather than exceptional - React StrictMode double-invokes
     * effects in development, so the UI genuinely does fire this twice for one application. Two
     * layers handle it: {@link AiCandidateProfileStore#persist} re-reads inside its own transaction
     * so a late arrival updates instead of inserting, and a constraint violation that still gets
     * through is recovered below by returning whatever the winner stored.
     */
    public ProfileResult generateOrGet(UUID applicationId, boolean refresh) {
        JobApplication application = applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));
        Requisition requisition = requisitions
                .findById(application.getRequisitionId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No requisition " + application.getRequisitionId()));

        Optional<AiCandidateProfile> existing = profiles.find(applicationId);

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

        // Fetches the phrase collection in the same query. The prompt reads it, and with no
        // transaction open around the Gemini call there is no session left to load it lazily - the
        // same reason MockInterviewService uses this method rather than findById.
        Candidate candidate = candidates
                .findWithPhrasesById(application.getCandidateId())
                .orElseThrow(() -> new NoSuchElementException(
                        "No candidate " + application.getCandidateId()));

        log.info(
                "Generating AI profile for {} against requisition '{}' ({})",
                candidate.getName(),
                requisition.getTitle(),
                origin);
        GeminiClient.StructuredResult<CandidateProfileGenerator.ProfileGeneration> result =
                generator.generate(candidate, requisition);
        CandidateProfileGenerator.ProfileGeneration generated = result.value();

        if (generated.fitScore() == null) {
            throw new GeminiException("Gemini returned a profile with no fitScore");
        }

        try {
            AiCandidateProfile saved = profiles.persist(
                    applicationId,
                    generated.bio(),
                    clampToScoreRange(generated.fitScore()),
                    generated.fitRationale(),
                    result.modelUsed(),
                    Instant.now());
            return new ProfileResult(saved, origin, result.usage());
        } catch (DataIntegrityViolationException e) {
            // Another request won the race and inserted first. Its profile is just as valid as the
            // one this call generated, so return it rather than surfacing a constraint violation as
            // a 500. The tokens this call spent are already gone either way.
            return profiles
                    .find(applicationId)
                    .map(winner -> {
                        log.info(
                                "Concurrent AI profile generation for application {}; returning the "
                                        + "profile that was stored first",
                                applicationId);
                        return new ProfileResult(winner, Origin.CACHED, null);
                    })
                    .orElseThrow(() -> e);
        }
    }

    @Transactional(readOnly = true)
    public Optional<AiCandidateProfile> find(UUID applicationId) {
        return profiles.find(applicationId);
    }

    /**
     * The schema already constrains this to 0-100; clamping is belt-and-braces against a model that
     * ignores the bound, so a stray value cannot violate the column's documented range.
     */
    private static int clampToScoreRange(int fitScore) {
        return Math.max(0, Math.min(100, fitScore));
    }
}
