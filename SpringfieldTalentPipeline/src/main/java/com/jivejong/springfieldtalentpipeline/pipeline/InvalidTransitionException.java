package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.Set;

/**
 * An attempted stage move the state machine does not allow. Surfaces as 409 carrying the current
 * stage and the stages that *are* reachable, so a client can show a useful error rather than a bare
 * failure - see docs/PIPELINE_STATE_MACHINE.md.
 */
public class InvalidTransitionException extends RuntimeException {

    private final PipelineStage currentStage;
    private final PipelineStage requestedStage;
    private final Set<PipelineStage> allowedStages;

    public InvalidTransitionException(
            PipelineStage currentStage,
            PipelineStage requestedStage,
            Set<PipelineStage> allowedStages) {
        super(buildMessage(currentStage, requestedStage, allowedStages));
        this.currentStage = currentStage;
        this.requestedStage = requestedStage;
        this.allowedStages = allowedStages;
    }

    private static String buildMessage(
            PipelineStage current, PipelineStage requested, Set<PipelineStage> allowed) {
        if (allowed.isEmpty()) {
            return "%s is a terminal stage; no transition to %s (or anything else) is possible"
                    .formatted(current, requested);
        }
        return "Cannot move from %s to %s. Allowed: %s".formatted(current, requested, allowed);
    }

    public PipelineStage getCurrentStage() {
        return currentStage;
    }

    public PipelineStage getRequestedStage() {
        return requestedStage;
    }

    public Set<PipelineStage> getAllowedStages() {
        return allowedStages;
    }
}
