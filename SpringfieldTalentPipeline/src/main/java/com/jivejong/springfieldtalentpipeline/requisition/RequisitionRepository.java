package com.jivejong.springfieldtalentpipeline.requisition;

import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface RequisitionRepository extends JpaRepository<Requisition, UUID> {}
