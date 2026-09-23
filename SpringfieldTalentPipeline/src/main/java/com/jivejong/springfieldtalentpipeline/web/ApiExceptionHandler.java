package com.jivejong.springfieldtalentpipeline.web;

import com.jivejong.springfieldtalentpipeline.ai.GeminiException;
import com.jivejong.springfieldtalentpipeline.pipeline.DuplicateApplicationException;
import com.jivejong.springfieldtalentpipeline.pipeline.InvalidTransitionException;
import com.jivejong.springfieldtalentpipeline.pipeline.PipelineStage;
import java.util.List;
import java.util.NoSuchElementException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** Maps domain failures onto status codes. */
@RestControllerAdvice
public class ApiExceptionHandler {

    /**
     * Body of a rejected transition. Carries the reachable stages so a client can render a useful
     * error - "you cannot do that, but here is what you can do" - rather than a bare failure. See
     * docs/PIPELINE_STATE_MACHINE.md.
     */
    public record InvalidTransitionResponse(
            String error,
            PipelineStage currentStage,
            PipelineStage requestedStage,
            List<PipelineStage> allowedNextStages) {}

    public record ErrorResponse(String error) {}

    @ExceptionHandler(InvalidTransitionException.class)
    public ResponseEntity<InvalidTransitionResponse> handleInvalidTransition(
            InvalidTransitionException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new InvalidTransitionResponse(
                        e.getMessage(),
                        e.getCurrentStage(),
                        e.getRequestedStage(),
                        e.getAllowedStages().stream().sorted().toList()));
    }

    @ExceptionHandler(DuplicateApplicationException.class)
    public ResponseEntity<ErrorResponse> handleDuplicateApplication(DuplicateApplicationException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(new ErrorResponse(e.getMessage()));
    }

    @ExceptionHandler(NoSuchElementException.class)
    public ResponseEntity<ErrorResponse> handleNotFound(NoSuchElementException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(new ErrorResponse(e.getMessage()));
    }

    /** An upstream model failure is a 502 - the fault is Gemini's or the network's, not the caller's. */
    @ExceptionHandler(GeminiException.class)
    public ResponseEntity<ErrorResponse> handleGeminiFailure(GeminiException e) {
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(new ErrorResponse(e.getMessage()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ErrorResponse> handleBadRequest(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(new ErrorResponse(e.getMessage()));
    }
}
