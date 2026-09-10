package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RecruiterFeedbackRepository extends JpaRepository<RecruiterFeedback, UUID> {

    List<RecruiterFeedback> findByApplicationIdOrderByCreatedAtDesc(UUID applicationId);
}
