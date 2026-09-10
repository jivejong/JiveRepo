package com.jivejong.springfieldtalentpipeline.pipeline;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.statemachine.StateMachine;
import org.springframework.statemachine.StateMachineEventResult;
import org.springframework.statemachine.config.StateMachineFactory;
import org.springframework.statemachine.support.DefaultStateMachineContext;
import reactor.core.publisher.Mono;

/**
 * Checks the configured machine against the transition table in docs/PIPELINE_STATE_MACHINE.md.
 *
 * <p>Context holds only the state machine config - no database involved.
 */
@SpringBootTest(classes = PipelineStateMachineConfig.class)
class PipelineStateMachineConfigTest {

    @Autowired
    private StateMachineFactory<PipelineStage, PipelineEvent> factory;

    /** Exactly the "Allowed transitions" table from docs/PIPELINE_STATE_MACHINE.md. */
    static List<Arguments> documentedTransitionTable() {
        return List.of(
                Arguments.of(
                        PipelineStage.SOURCED,
                        Set.of(
                                PipelineStage.SCREENING,
                                PipelineStage.REJECTED,
                                PipelineStage.WITHDRAWN)),
                Arguments.of(
                        PipelineStage.SCREENING,
                        Set.of(
                                PipelineStage.INTERVIEWING,
                                PipelineStage.REJECTED,
                                PipelineStage.WITHDRAWN)),
                Arguments.of(
                        PipelineStage.INTERVIEWING,
                        Set.of(PipelineStage.OFFER, PipelineStage.REJECTED, PipelineStage.WITHDRAWN)),
                Arguments.of(
                        PipelineStage.OFFER,
                        Set.of(PipelineStage.HIRED, PipelineStage.REJECTED, PipelineStage.WITHDRAWN)),
                Arguments.of(PipelineStage.HIRED, Set.of()),
                Arguments.of(PipelineStage.REJECTED, Set.of()),
                Arguments.of(PipelineStage.WITHDRAWN, Set.of()));
    }

    @ParameterizedTest(name = "{0} allows {1}")
    @MethodSource("documentedTransitionTable")
    void machineMatchesTheDocumentedTable(PipelineStage from, Set<PipelineStage> expected) {
        StateMachine<PipelineStage, PipelineEvent> machine = factory.getStateMachine();
        assertThat(PipelineTransitionRules.allowedStagesFrom(machine, from))
                .containsExactlyInAnyOrderElementsOf(expected);
    }

    @Test
    void hiredIsReachableOnlyFromOffer() {
        StateMachine<PipelineStage, PipelineEvent> machine = factory.getStateMachine();
        for (PipelineStage stage : PipelineStage.values()) {
            boolean canReachHired =
                    PipelineTransitionRules.allowedStagesFrom(machine, stage).contains(PipelineStage.HIRED);
            assertThat(canReachHired).isEqualTo(stage == PipelineStage.OFFER);
        }
    }

    @Test
    void thereIsNoPathBackwards() {
        // A backward move would make the audit trail meaningless - docs/PIPELINE_STATE_MACHINE.md.
        StateMachine<PipelineStage, PipelineEvent> machine = factory.getStateMachine();
        assertThat(PipelineTransitionRules.allowedStagesFrom(machine, PipelineStage.INTERVIEWING))
                .doesNotContain(PipelineStage.SCREENING, PipelineStage.SOURCED);
        assertThat(PipelineTransitionRules.allowedStagesFrom(machine, PipelineStage.OFFER))
                .doesNotContain(PipelineStage.INTERVIEWING);
    }

    @Test
    void advanceFromSourcedLandsOnScreeningAndIsAccepted() {
        StateMachine<PipelineStage, PipelineEvent> machine = machineAt(PipelineStage.SOURCED);
        assertThat(send(machine, PipelineEvent.ADVANCE))
                .isEqualTo(StateMachineEventResult.ResultType.ACCEPTED);
        assertThat(machine.getState().getId()).isEqualTo(PipelineStage.SCREENING);
    }

    @Test
    void advancingOutOfATerminalStageIsDenied() {
        StateMachine<PipelineStage, PipelineEvent> machine = machineAt(PipelineStage.HIRED);
        assertThat(send(machine, PipelineEvent.ADVANCE))
                .isEqualTo(StateMachineEventResult.ResultType.DENIED);
        assertThat(machine.getState().getId()).isEqualTo(PipelineStage.HIRED);
    }

    @Test
    void rejectingFromAnyActiveStageIsAccepted() {
        for (PipelineStage stage : PipelineStage.values()) {
            if (!stage.isActive()) {
                continue;
            }
            StateMachine<PipelineStage, PipelineEvent> machine = machineAt(stage);
            assertThat(send(machine, PipelineEvent.REJECT))
                    .as("REJECT from %s", stage)
                    .isEqualTo(StateMachineEventResult.ResultType.ACCEPTED);
            assertThat(machine.getState().getId()).isEqualTo(PipelineStage.REJECTED);
        }
    }

    private StateMachine<PipelineStage, PipelineEvent> machineAt(PipelineStage stage) {
        StateMachine<PipelineStage, PipelineEvent> machine = factory.getStateMachine();
        machine.stopReactively().block();
        machine.getStateMachineAccessor()
                .doWithAllRegions(access -> access.resetStateMachineReactively(
                                new DefaultStateMachineContext<PipelineStage, PipelineEvent>(
                                        stage, null, null, null))
                        .block());
        machine.startReactively().block();
        return machine;
    }

    private static StateMachineEventResult.ResultType send(
            StateMachine<PipelineStage, PipelineEvent> machine, PipelineEvent event) {
        List<StateMachineEventResult<PipelineStage, PipelineEvent>> results = machine
                .sendEventCollect(Mono.just(MessageBuilder.withPayload(event).build()))
                .block();
        return results == null || results.isEmpty()
                ? StateMachineEventResult.ResultType.DENIED
                : results.get(0).getResultType();
    }
}
