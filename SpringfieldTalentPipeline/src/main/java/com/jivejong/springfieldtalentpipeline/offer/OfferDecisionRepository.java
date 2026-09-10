package com.jivejong.springfieldtalentpipeline.offer;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface OfferDecisionRepository extends JpaRepository<OfferDecision, UUID> {

    Optional<OfferDecision> findByApplicationId(UUID applicationId);

    List<OfferDecision> findByApplicationIdOrderByDecidedAtDesc(UUID applicationId);
}
