package com.jivejong.springfieldtalentpipeline.pipeline;

/** Triggers for stage changes - see docs/PIPELINE_STATE_MACHINE.md. */
public enum PipelineEvent {
    /** Move one step forward along the happy path. */
    ADVANCE,
    REJECT,
    WITHDRAW
}
