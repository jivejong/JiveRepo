package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.springframework.statemachine.StateMachine;
import org.springframework.statemachine.transition.Transition;

/**
 * Reads the allowed moves out of a configured state machine.
 *
 * <p>This exists so the 409 response can tell a client which stages <em>are</em> reachable without
 * anyone writing that list down a second time. The answer is computed from
 * {@link PipelineStateMachineConfig}'s own transitions, so the error message and the enforcement
 * can never disagree.
 */
public final class PipelineTransitionRules {

    private PipelineTransitionRules() {}

    /**
     * Stages reachable from {@code from}, mapped to the event that gets there. Ordered by the
     * stage enum's declaration order so the response reads Screening/Rejected/Withdrawn rather than
     * in whatever order the machine happens to hold them.
     */
    public static Map<PipelineStage, PipelineEvent> allowedFrom(
            StateMachine<PipelineStage, PipelineEvent> machine, PipelineStage from) {
        Map<PipelineStage, PipelineEvent> allowed = new LinkedHashMap<>();
        machine.getTransitions().stream()
                .filter(transition -> transition.getSource() != null
                        && transition.getSource().getId() == from
                        && transition.getTarget() != null
                        && transition.getTrigger() != null)
                .sorted((a, b) -> a.getTarget().getId().compareTo(b.getTarget().getId()))
                .forEach(transition ->
                        allowed.put(transition.getTarget().getId(), eventOf(transition)));
        return allowed;
    }

    public static Set<PipelineStage> allowedStagesFrom(
            StateMachine<PipelineStage, PipelineEvent> machine, PipelineStage from) {
        return allowedFrom(machine, from).keySet();
    }

    private static PipelineEvent eventOf(Transition<PipelineStage, PipelineEvent> transition) {
        return transition.getTrigger().getEvent();
    }
}
