package com.jivejong.springfieldtalentpipeline.candidate;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

/**
 * One character as returned by The Simpsons API.
 *
 * <p>The API also returns {@code birthdate}, which docs/DATA_MODEL.md does not model; unknown
 * properties are ignored by Boot's default Jackson setup, so it is simply left out here.
 */
public record SimpsonsCharacter(
        Integer id,
        Integer age,
        String gender,
        String name,
        String occupation,
        @JsonProperty("portrait_path") String portraitPath,
        List<String> phrases,
        String status) {}
