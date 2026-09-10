package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface JobApplicationRepository extends JpaRepository<JobApplication, UUID> {

    List<JobApplication> findByCandidateIdAndRequisitionId(UUID candidateId, UUID requisitionId);

    List<JobApplication> findByRequisitionId(UUID requisitionId);
}
