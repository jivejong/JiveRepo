package com.jivejong.springfieldtalentpipeline.candidate;

/** Thrown when a page of characters cannot be retrieved or parsed. */
public class SimpsonsApiException extends RuntimeException {

    public SimpsonsApiException(String message, Throwable cause) {
        super(message, cause);
    }

    public SimpsonsApiException(String message) {
        super(message);
    }
}
