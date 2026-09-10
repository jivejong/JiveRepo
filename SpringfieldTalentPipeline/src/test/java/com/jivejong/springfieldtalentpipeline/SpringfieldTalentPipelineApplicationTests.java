package com.jivejong.springfieldtalentpipeline;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Full context load. Requires the local Postgres instance described in the README to be running —
 * that is deliberate: the Phase 0 checkpoint is "app starts and connects to Postgres", so a test
 * that passed against an in-memory stand-in would not be verifying the thing that matters.
 */
@SpringBootTest
class SpringfieldTalentPipelineApplicationTests {

    @Test
    void contextLoads() {
        // Fails if the datasource, JPA or state machine autoconfiguration cannot start.
    }
}
