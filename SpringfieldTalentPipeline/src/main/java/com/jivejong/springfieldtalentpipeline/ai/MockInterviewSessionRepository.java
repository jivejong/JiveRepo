package com.jivejong.springfieldtalentpipeline.ai;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;

public interface MockInterviewSessionRepository extends JpaRepository<MockInterviewSession, UUID> {

    /**
     * Newest first - an application can have several, by design.
     *
     * <p>Fetches turns eagerly: callers summarise them (a turn count, at least) after the
     * transaction has closed, and {@code turns} is lazy, so without this the collection is detached
     * by the time anyone looks at it.
     */
    @EntityGraph(attributePaths = "turns")
    List<MockInterviewSession> findByApplicationIdOrderByGeneratedAtDesc(UUID applicationId);

    @EntityGraph(attributePaths = "turns")
    Optional<MockInterviewSession> findWithTurnsById(UUID id);
}
