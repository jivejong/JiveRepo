package com.jivejong.springfieldtalentpipeline.candidate;

import java.time.Instant;
import java.util.List;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Persists a single character.
 *
 * <p>Separate bean, not a private method on {@link CandidateSyncService}, so each record commits in
 * its own transaction: a record that fails to map rolls back only itself. A self-invoked
 * {@code @Transactional} method would be bypassed by the proxy and silently join the caller's
 * transaction, which would let one bad record poison a whole batch.
 */
@Component
public class CandidateUpserter {

    public enum Outcome {
        CREATED,
        UPDATED
    }

    private final CandidateRepository repository;

    public CandidateUpserter(CandidateRepository repository) {
        this.repository = repository;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Outcome upsert(SimpsonsCharacter source, Instant syncedAt) {
        if (source.id() == null) {
            throw new IllegalArgumentException("character has no id");
        }
        if (source.name() == null || source.name().isBlank()) {
            throw new IllegalArgumentException("character " + source.id() + " has no name");
        }

        return repository
                .findByExternalId(source.id())
                .map(existing -> {
                    apply(source, existing, syncedAt);
                    repository.save(existing);
                    return Outcome.UPDATED;
                })
                .orElseGet(() -> {
                    Candidate created = new Candidate(source.id(), source.name());
                    created.setImportedAt(syncedAt);
                    apply(source, created, syncedAt);
                    repository.save(created);
                    return Outcome.CREATED;
                });
    }

    private void apply(SimpsonsCharacter source, Candidate target, Instant syncedAt) {
        target.setName(source.name());
        // Absent values stay null rather than becoming 0 or "" - docs/SIMPSONS_API.md.
        target.setAge(source.age());
        target.setGender(blankToNull(source.gender()));
        target.setOccupation(blankToNull(source.occupation()));
        target.setCharacterStatus(CharacterStatus.fromApiValue(source.status()));
        target.setPortraitPath(blankToNull(source.portraitPath()));
        target.setPhrases(source.phrases() == null ? List.of() : source.phrases());
        target.setLastSyncedAt(syncedAt);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
