package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.springframework.statemachine.listener.StateMachineListenerAdapter;
import org.springframework.statemachine.transition.Transition;

/**
 * Captures transitions the machine actually performed, so the audit log is written from what the
 * machine did rather than what a call site believed it asked for - see
 * docs/PIPELINE_STATE_MACHINE.md.
 *
 * <p>One instance per machine, and machines are created per request, so this is not shared state.
 * It records rather than persists: the service writes the row, because only the service knows the
 * recruiter's note, and because nothing should be committed until the outcome is confirmed.
 */
class StageTransitionRecorder extends StateMachineListenerAdapter<PipelineStage, PipelineEvent> {

    record Recorded(PipelineStage from, PipelineStage to) {}

    private final List<Recorded> recorded = new ArrayList<>();

    @Override
    public void transitionEnded(Transition<PipelineStage, PipelineEvent> transition) {
        if (transition == null || transition.getSource() == null || transition.getTarget() == null) {
            return;
        }
        recorded.add(new Recorded(transition.getSource().getId(), transition.getTarget().getId()));
    }

    /** The single transition this request performed, if exactly one was performed. */
    Optional<Recorded> single() {
        return recorded.size() == 1 ? Optional.of(recorded.get(0)) : Optional.empty();
    }

    List<Recorded> all() {
        return List.copyOf(recorded);
    }
}
