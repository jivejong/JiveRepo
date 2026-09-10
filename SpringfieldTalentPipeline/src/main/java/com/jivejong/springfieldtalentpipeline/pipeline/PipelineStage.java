package com.jivejong.springfieldtalentpipeline.pipeline;

/**
 * Stages an application moves through. See docs/PIPELINE_STATE_MACHINE.md.
 *
 * <p>Note what is deliberately <em>absent</em>: any table of which stage may follow which. That
 * lives solely in {@link PipelineStateMachineConfig}, so there is exactly one declaration of the
 * rules. Asking this enum "what may follow SCREENING?" would create a second copy that could drift
 * from the machine actually enforcing them - {@link PipelineTransitionRules} derives the answer
 * from the machine's own configuration instead.
 */
public enum PipelineStage {
    SOURCED,
    SCREENING,
    INTERVIEWING,
    OFFER,
    HIRED,
    REJECTED,
    WITHDRAWN;

    /** True for the stages an application can leave: everything except Hired/Rejected/Withdrawn. */
    public boolean isActive() {
        return this != HIRED && this != REJECTED && this != WITHDRAWN;
    }
}
