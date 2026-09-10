package com.jivejong.springfieldtalentpipeline.pipeline;

import com.jivejong.springfieldtalentpipeline.candidate.CandidateRepository;
import com.jivejong.springfieldtalentpipeline.requisition.RequisitionRepository;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.statemachine.StateMachine;
import org.springframework.statemachine.StateMachineEventResult;
import org.springframework.statemachine.config.StateMachineFactory;
import org.springframework.statemachine.support.DefaultStateMachineContext;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import reactor.core.publisher.Mono;

/** Application creation and stage movement. The state machine is the only authority on moves. */
@Service
public class PipelineService {

    private static final Logger log = LoggerFactory.getLogger(PipelineService.class);

    private final StateMachineFactory<PipelineStage, PipelineEvent> stateMachineFactory;
    private final JobApplicationRepository applications;
    private final StageTransitionRepository transitions;
    private final CandidateRepository candidates;
    private final RequisitionRepository requisitions;

    public PipelineService(
            StateMachineFactory<PipelineStage, PipelineEvent> stateMachineFactory,
            JobApplicationRepository applications,
            StageTransitionRepository transitions,
            CandidateRepository candidates,
            RequisitionRepository requisitions) {
        this.stateMachineFactory = stateMachineFactory;
        this.applications = applications;
        this.transitions = transitions;
        this.candidates = candidates;
        this.requisitions = requisitions;
    }

    @Transactional
    public JobApplication createApplication(UUID candidateId, UUID requisitionId) {
        if (!candidates.existsById(candidateId)) {
            throw new NoSuchElementException("No candidate " + candidateId);
        }
        if (!requisitions.existsById(requisitionId)) {
            throw new NoSuchElementException("No requisition " + requisitionId);
        }
        applications.findByCandidateIdAndRequisitionId(candidateId, requisitionId).stream()
                .filter(existing -> existing.getCurrentStage().isActive())
                .findFirst()
                .ifPresent(existing -> {
                    throw new DuplicateApplicationException(
                            existing.getId(), existing.getCurrentStage());
                });

        Instant now = Instant.now();
        JobApplication application =
                applications.save(new JobApplication(candidateId, requisitionId, now));
        // Seed the audit log so the trail starts where the application did, with a null fromStage.
        transitions.save(new StageTransition(
                application.getId(), null, PipelineStage.SOURCED, now, "Application created"));
        return application;
    }

    /**
     * Moves an application to {@code requestedStage}.
     *
     * @throws InvalidTransitionException if the machine does not allow the move (surfaces as 409)
     */
    @Transactional
    public JobApplication transition(UUID applicationId, PipelineStage requestedStage, String note) {
        JobApplication application = applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));
        PipelineStage current = application.getCurrentStage();

        StageTransitionRecorder recorder = new StageTransitionRecorder();
        StateMachine<PipelineStage, PipelineEvent> machine = machineAt(current, recorder);
        Map<PipelineStage, PipelineEvent> allowed =
                PipelineTransitionRules.allowedFrom(machine, current);

        PipelineEvent event = allowed.get(requestedStage);
        if (event == null) {
            // Covers both terminal stages (nothing allowed) and skipping ahead, e.g. Sourced ->
            // Offer: ADVANCE would be accepted but would land on Screening, which is not what was
            // asked for. Consulting the machine's own transition table first means we never perform
            // a move the caller did not request.
            throw new InvalidTransitionException(
                    current, requestedStage, Set.copyOf(allowed.keySet()));
        }

        StateMachineEventResult.ResultType result = send(machine, event);
        PipelineStage landed = machine.getState().getId();
        if (result != StateMachineEventResult.ResultType.ACCEPTED || landed != requestedStage) {
            log.warn(
                    "State machine refused {} -> {} on application {} (result={}, landed={})",
                    current,
                    requestedStage,
                    applicationId,
                    result,
                    landed);
            throw new InvalidTransitionException(
                    current, requestedStage, Set.copyOf(allowed.keySet()));
        }

        Instant now = Instant.now();
        application.moveTo(landed, now);
        applications.save(application);

        StageTransitionRecorder.Recorded performed = recorder
                .single()
                .orElseGet(() -> new StageTransitionRecorder.Recorded(current, landed));
        transitions.save(new StageTransition(
                application.getId(), performed.from(), performed.to(), now, note));

        log.info("Application {} moved {} -> {}", applicationId, performed.from(), performed.to());
        return application;
    }

    /** Stages currently reachable, straight from the machine's configuration. */
    @Transactional(readOnly = true)
    public Set<PipelineStage> allowedNextStages(UUID applicationId) {
        JobApplication application = applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));
        StateMachine<PipelineStage, PipelineEvent> machine =
                machineAt(application.getCurrentStage(), null);
        return Set.copyOf(
                PipelineTransitionRules.allowedStagesFrom(machine, application.getCurrentStage()));
    }

    @Transactional(readOnly = true)
    public JobApplication get(UUID applicationId) {
        return applications
                .findById(applicationId)
                .orElseThrow(() -> new NoSuchElementException("No application " + applicationId));
    }

    @Transactional(readOnly = true)
    public List<StageTransition> history(UUID applicationId) {
        return transitions.findByApplicationIdOrderByTransitionedAtAsc(applicationId);
    }

    /** A machine rehydrated to an application's stored stage. */
    private StateMachine<PipelineStage, PipelineEvent> machineAt(
            PipelineStage stage, StageTransitionRecorder recorder) {
        StateMachine<PipelineStage, PipelineEvent> machine = stateMachineFactory.getStateMachine();
        machine.stopReactively().block();
        machine.getStateMachineAccessor()
                .doWithAllRegions(access -> access.resetStateMachineReactively(
                                new DefaultStateMachineContext<PipelineStage, PipelineEvent>(
                                        stage, null, null, null))
                        .block());
        if (recorder != null) {
            // Added after the reset so rehydration itself is not mistaken for a real transition.
            machine.addStateListener(recorder);
        }
        machine.startReactively().block();
        return machine;
    }

    private static StateMachineEventResult.ResultType send(
            StateMachine<PipelineStage, PipelineEvent> machine, PipelineEvent event) {
        List<StateMachineEventResult<PipelineStage, PipelineEvent>> results = machine
                .sendEventCollect(Mono.just(MessageBuilder.withPayload(event).build()))
                .block();
        if (results == null || results.isEmpty()) {
            return StateMachineEventResult.ResultType.DENIED;
        }
        return results.get(0).getResultType();
    }
}
