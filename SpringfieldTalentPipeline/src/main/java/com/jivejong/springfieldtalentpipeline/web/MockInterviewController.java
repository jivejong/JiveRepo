package com.jivejong.springfieldtalentpipeline.web;

import com.jivejong.springfieldtalentpipeline.ai.MockInterviewService;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Reading a stored interview transcript. Never generates - generation hangs off the application
 * resource, since that is what an interview belongs to.
 */
@RestController
@RequestMapping("/api/mock-interviews")
public class MockInterviewController {

    private final MockInterviewService interviews;

    public MockInterviewController(MockInterviewService interviews) {
        this.interviews = interviews;
    }

    /** Token usage is null here: reading a stored transcript costs nothing. */
    @GetMapping("/{sessionId}")
    public JobApplicationController.InterviewResponse get(@PathVariable UUID sessionId) {
        return JobApplicationController.InterviewResponse.of(
                interviews.getWithTurns(sessionId), null);
    }
}
