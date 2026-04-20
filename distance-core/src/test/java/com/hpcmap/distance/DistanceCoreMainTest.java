package com.hpcmap.distance;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

class DistanceCoreMainTest {

    @Test
    void helpDoesNotThrow() {
        assertDoesNotThrow(() -> DistanceCoreMain.main(new String[]{"--help"}));
    }
}
