package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface StageTransitionRepository extends JpaRepository<StageTransition, UUID> {

    List<StageTransition> findByApplicationIdOrderByTransitionedAtAsc(UUID applicationId);
}
