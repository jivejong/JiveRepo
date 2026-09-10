package com.jivejong.springfieldtalentpipeline.ai;

/** Thrown when a Groq generation cannot be completed or parsed. Surfaces as 502. */
public class GroqException extends RuntimeException {

    public GroqException(String message) {
        super(message);
    }

    public GroqException(String message, Throwable cause) {
        super(message, cause);
    }
}
