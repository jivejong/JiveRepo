package com.jivejong.springfieldtalentpipeline.candidate;

import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The Simpsons API's {@code status} field.
 *
 * <p>Named {@code CharacterStatus} rather than {@code Status} so it is never confused with a
 * pipeline/application status - see docs/DATA_MODEL.md.
 *
 * <p><strong>Deviation from docs/DATA_MODEL.md:</strong> the spec anticipated three values
 * ({@code Alive}, {@code Dead}, {@code Unknown}). The live API actually returns seven, and none of
 * them is {@code Dead} - the value is {@code Deceased}. Implementing the spec literally would drop
 * roughly 17% of the character pool on the floor, so the enum covers what the API really sends.
 */
public enum CharacterStatus {
    ALIVE("Alive"),
    DECEASED("Deceased"),
    FICTIONAL("Fictional"),
    NONCANON("Noncanon"),
    NONCANON_DECEASED("Noncanon Deceased"),
    DESTROYED_ICON("Destroyed Icon"),
    UNKNOWN("Unknown");

    private static final Logger log = LoggerFactory.getLogger(CharacterStatus.class);

    private static final Map<String, CharacterStatus> BY_API_VALUE = Stream.of(values())
            .collect(Collectors.toMap(s -> s.apiValue.toLowerCase(), Function.identity()));

    private final String apiValue;

    CharacterStatus(String apiValue) {
        this.apiValue = apiValue;
    }

    public String apiValue() {
        return apiValue;
    }

    /**
     * Maps a raw API value onto the enum, falling back to {@link #UNKNOWN} for anything absent or
     * unrecognised. A value the API adds later therefore degrades one record's fidelity instead of
     * failing its import - but it is logged, so a new value does not pass silently.
     */
    public static CharacterStatus fromApiValue(String raw) {
        if (raw == null || raw.isBlank()) {
            return UNKNOWN;
        }
        CharacterStatus matched = BY_API_VALUE.get(raw.trim().toLowerCase());
        if (matched == null) {
            log.warn("Unrecognised character status '{}' from The Simpsons API; storing as UNKNOWN. "
                    + "Add it to CharacterStatus if it is now a real value.", raw);
            return UNKNOWN;
        }
        return matched;
    }
}
