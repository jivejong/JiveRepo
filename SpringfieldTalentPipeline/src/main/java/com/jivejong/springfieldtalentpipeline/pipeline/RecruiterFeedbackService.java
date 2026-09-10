package com.jivejong.springfieldtalentpipeline.pipeline;

import java.time.Instant;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RecruiterFeedbackService {

    public static final int MIN_RATING = 1;
    public static final int MAX_RATING = 5;

    private final RecruiterFeedbackRepository feedback;
    private final JobApplicationRepository applications;

    public RecruiterFeedbackService(
            RecruiterFeedbackRepository feedback, JobApplicationRepository applications) {
        this.feedback = feedback;
        this.applications = applications;
    }

    @Transactional
    public RecruiterFeedback record(
            UUID applicationId, UUID sessionId, Integer rating, String comments) {
        if (!applications.existsById(applicationId)) {
            throw new NoSuchElementException("No application " + applicationId);
        }
        if (rating == null || rating < MIN_RATING || rating > MAX_RATING) {
            throw new IllegalArgumentException(
                    "rating is required and must be between %d and %d".formatted(MIN_RATING, MAX_RATING));
        }
        return feedback.save(
                new RecruiterFeedback(applicationId, sessionId, rating, comments, Instant.now()));
    }

    @Transactional(readOnly = true)
    public List<RecruiterFeedback> listForApplication(UUID applicationId) {
        return feedback.findByApplicationIdOrderByCreatedAtDesc(applicationId);
    }
}
