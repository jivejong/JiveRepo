package com.jivejong.springfieldtalentpipeline.ai;

/** Thrown when a Gemini generation cannot be completed or parsed. Surfaces as 502. */
public class GeminiException extends RuntimeException {

    public GeminiException(String message) {
        super(message);
    }

    public GeminiException(String message, Throwable cause) {
        super(message, cause);
    }
}
