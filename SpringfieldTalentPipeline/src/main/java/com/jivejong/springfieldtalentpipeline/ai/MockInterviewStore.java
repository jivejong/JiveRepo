package com.jivejong.springfieldtalentpipeline.ai;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Persistence for mock interviews, kept separate from {@link MockInterviewService} so the Groq call
 * happens outside a transaction while the writes still get one.
 *
 * <p>{@link #saveFailed} commits in its own transaction ({@code REQUIRES_NEW}) because it is called
 * on the way to throwing: joining the caller's transaction would mean the failure record rolls back
 * along with the request, which defeats the point of recording it.
 */
@Component
public class MockInterviewStore {

    private final MockInterviewSessionRepository sessions;

    public MockInterviewStore(MockInterviewSessionRepository sessions) {
        this.sessions = sessions;
    }

    @Transactional
    public MockInterviewSession saveGenerated(
            UUID applicationId,
            String overallAssessment,
            Integer overallRating,
            String modelUsed,
            Instant generatedAt,
            List<MockInterviewGenerator.InterviewTurn> turns) {
        MockInterviewSession session = MockInterviewSession.generated(
                applicationId, overallAssessment, overallRating, modelUsed, generatedAt);
        int questionNumber = 1;
        for (MockInterviewGenerator.InterviewTurn turn : turns) {
            session.addTurn(questionNumber++, turn.question(), turn.answer());
        }
        return sessions.save(session);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public MockInterviewSession saveFailed(UUID applicationId, String reason, String modelUsed) {
        return sessions.save(
                MockInterviewSession.failed(applicationId, reason, modelUsed, Instant.now()));
    }

    @Transactional(readOnly = true)
    public List<MockInterviewSession> listForApplication(UUID applicationId) {
        return sessions.findByApplicationIdOrderByGeneratedAtDesc(applicationId);
    }

    @Transactional(readOnly = true)
    public Optional<MockInterviewSession> findWithTurns(UUID sessionId) {
        return sessions.findWithTurnsById(sessionId);
    }
}
