package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.UUID;

/**
 * One active application per candidate/requisition pair. Enforced at the service layer rather than
 * as a DB constraint, deliberately: a unique index would also block re-applying after a rejection
 * or withdrawal, which docs/DATA_MODEL.md explicitly wants to stay possible.
 */
public class DuplicateApplicationException extends RuntimeException {

    private final UUID existingApplicationId;

    public DuplicateApplicationException(UUID existingApplicationId, PipelineStage stage) {
        super("An active application already exists (id=%s, stage=%s)"
                .formatted(existingApplicationId, stage));
        this.existingApplicationId = existingApplicationId;
    }

    public UUID getExistingApplicationId() {
        return existingApplicationId;
    }
}
