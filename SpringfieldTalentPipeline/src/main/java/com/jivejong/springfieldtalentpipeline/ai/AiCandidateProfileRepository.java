package com.jivejong.springfieldtalentpipeline.ai;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AiCandidateProfileRepository extends JpaRepository<AiCandidateProfile, UUID> {

    Optional<AiCandidateProfile> findByApplicationId(UUID applicationId);
}
