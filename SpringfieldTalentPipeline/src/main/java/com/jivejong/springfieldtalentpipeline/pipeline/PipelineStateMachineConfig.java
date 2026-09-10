package com.jivejong.springfieldtalentpipeline.pipeline;

import java.util.EnumSet;
import org.springframework.context.annotation.Configuration;
import org.springframework.statemachine.config.EnableStateMachineFactory;
import org.springframework.statemachine.config.StateMachineConfigurerAdapter;
import org.springframework.statemachine.config.builders.StateMachineConfigurationConfigurer;
import org.springframework.statemachine.config.builders.StateMachineStateConfigurer;
import org.springframework.statemachine.config.builders.StateMachineTransitionConfigurer;

/**
 * The one and only declaration of the pipeline's transition rules.
 *
 * <p>Spring Statemachine rejects any transition not configured here, which is exactly the
 * validation this needs - no custom guard logic for the basic linear-plus-terminal shape. Note
 * there is no backward path (no {@code INTERVIEWING -> SCREENING}): a mis-transition is corrected
 * by an admin action, not by allowing moves that would make the audit trail meaningless.
 */
@Configuration
@EnableStateMachineFactory
public class PipelineStateMachineConfig
        extends StateMachineConfigurerAdapter<PipelineStage, PipelineEvent> {

    @Override
    public void configure(StateMachineConfigurationConfigurer<PipelineStage, PipelineEvent> config)
            throws Exception {
        // Machines are created per request and reset to the application's stored stage, so there is
        // nothing to start automatically.
        config.withConfiguration().autoStartup(false);
    }

    @Override
    public void configure(StateMachineStateConfigurer<PipelineStage, PipelineEvent> states)
            throws Exception {
        states.withStates()
                .initial(PipelineStage.SOURCED)
                .states(EnumSet.allOf(PipelineStage.class))
                .end(PipelineStage.HIRED)
                .end(PipelineStage.REJECTED)
                .end(PipelineStage.WITHDRAWN);
    }

    @Override
    public void configure(StateMachineTransitionConfigurer<PipelineStage, PipelineEvent> transitions)
            throws Exception {
        transitions
                // The happy path, one step at a time. Hired is reachable only from Offer.
                .withExternal()
                .source(PipelineStage.SOURCED)
                .target(PipelineStage.SCREENING)
                .event(PipelineEvent.ADVANCE)
                .and()
                .withExternal()
                .source(PipelineStage.SCREENING)
                .target(PipelineStage.INTERVIEWING)
                .event(PipelineEvent.ADVANCE)
                .and()
                .withExternal()
                .source(PipelineStage.INTERVIEWING)
                .target(PipelineStage.OFFER)
                .event(PipelineEvent.ADVANCE)
                .and()
                .withExternal()
                .source(PipelineStage.OFFER)
                .target(PipelineStage.HIRED)
                .event(PipelineEvent.ADVANCE)
                // Rejection and withdrawal are reachable from any active stage.
                .and()
                .withExternal()
                .source(PipelineStage.SOURCED)
                .target(PipelineStage.REJECTED)
                .event(PipelineEvent.REJECT)
                .and()
                .withExternal()
                .source(PipelineStage.SCREENING)
                .target(PipelineStage.REJECTED)
                .event(PipelineEvent.REJECT)
                .and()
                .withExternal()
                .source(PipelineStage.INTERVIEWING)
                .target(PipelineStage.REJECTED)
                .event(PipelineEvent.REJECT)
                .and()
                .withExternal()
                .source(PipelineStage.OFFER)
                .target(PipelineStage.REJECTED)
                .event(PipelineEvent.REJECT)
                .and()
                .withExternal()
                .source(PipelineStage.SOURCED)
                .target(PipelineStage.WITHDRAWN)
                .event(PipelineEvent.WITHDRAW)
                .and()
                .withExternal()
                .source(PipelineStage.SCREENING)
                .target(PipelineStage.WITHDRAWN)
                .event(PipelineEvent.WITHDRAW)
                .and()
                .withExternal()
                .source(PipelineStage.INTERVIEWING)
                .target(PipelineStage.WITHDRAWN)
                .event(PipelineEvent.WITHDRAW)
                .and()
                .withExternal()
                .source(PipelineStage.OFFER)
                .target(PipelineStage.WITHDRAWN)
                .event(PipelineEvent.WITHDRAW);
    }
}
