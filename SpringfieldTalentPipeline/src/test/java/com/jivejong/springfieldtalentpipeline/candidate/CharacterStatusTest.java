package com.jivejong.springfieldtalentpipeline.candidate;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class CharacterStatusTest {

    /** Every value the live API actually returns, as surveyed across all 1,182 characters. */
    @ParameterizedTest
    @CsvSource({
        "Alive,ALIVE",
        "Deceased,DECEASED",
        "Unknown,UNKNOWN",
        "Fictional,FICTIONAL",
        "Noncanon,NONCANON",
        "Noncanon Deceased,NONCANON_DECEASED",
        "Destroyed Icon,DESTROYED_ICON"
    })
    void mapsEveryStatusTheLiveApiReturns(String apiValue, CharacterStatus expected) {
        assertThat(CharacterStatus.fromApiValue(apiValue)).isEqualTo(expected);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"   ", "Schrodinger", "Dead"})
    void fallsBackToUnknownRatherThanFailingTheRecord(String raw) {
        // "Dead" is here on purpose: it is what docs/DATA_MODEL.md predicted, and the API never
        // sends it. Anything unrecognised has to degrade, not throw, or one new upstream value
        // would break the whole sync.
        assertThat(CharacterStatus.fromApiValue(raw)).isEqualTo(CharacterStatus.UNKNOWN);
    }

    @Test
    void isCaseAndWhitespaceInsensitive() {
        assertThat(CharacterStatus.fromApiValue("  deCEAsed  ")).isEqualTo(CharacterStatus.DECEASED);
    }
}
