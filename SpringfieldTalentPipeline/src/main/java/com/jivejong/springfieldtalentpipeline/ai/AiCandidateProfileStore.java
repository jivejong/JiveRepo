package com.jivejong.springfieldtalentpipeline.ai;

import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Persistence for AI profiles, separated from {@link AiCandidateProfileService} so the Groq call
 * happens outside any transaction rather than holding a database connection open for the several
 * seconds a generation takes.
 *
 * <p>Each method runs in its own new transaction. That matters for the write: when two concurrent
 * requests race to create the first profile for an application, the loser's insert violates
 * {@code uk_ai_profile_application} and its transaction is doomed. Recovery has to happen in a
 * <em>different</em> transaction, which is only possible because these are separate calls rather
 * than one long-lived one.
 */
@Component
public class AiCandidateProfileStore {

    private final AiCandidateProfileRepository profiles;

    public AiCandidateProfileStore(AiCandidateProfileRepository profiles) {
        this.profiles = profiles;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW, readOnly = true)
    public Optional<AiCandidateProfile> find(UUID applicationId) {
        return profiles.findByApplicationId(applicationId);
    }

    /**
     * Creates or updates the single profile for an application.
     *
     * <p>The read happens inside this transaction rather than being passed in from the caller's
     * earlier lookup. A concurrent request that inserted while this one was still talking to Groq
     * is therefore seen here, and this becomes an update instead of a colliding insert - which
     * closes most of the race on its own. The remaining window, where both transactions read before
     * either writes, is what the caller's constraint-violation recovery is for.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public AiCandidateProfile persist(
            UUID applicationId,
            String bio,
            int fitScore,
            String fitRationale,
            String modelUsed,
            Instant generatedAt) {
        AiCandidateProfile profile = profiles
                .findByApplicationId(applicationId)
                .orElseGet(() -> new AiCandidateProfile(applicationId));
        profile.apply(bio, fitScore, fitRationale, modelUsed, generatedAt);
        return profiles.saveAndFlush(profile);
    }
}
