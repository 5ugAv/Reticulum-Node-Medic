// RTNode-2400 — Birth Cry (first-boot-after-flash LED celebration)
//
// Operator-specified choreography (2026-07-30): a faint BUBBLING RAINBOW that
// increases brightness smoothly, swells into a bright WHITE pulse, then blinks
// white TWICE. Plays exactly ONCE per newly-flashed firmware (gated on the
// build stamp in NVS), so from across the bench "rainbow → white-white" means
// "the flash took and the board is alive". Subsequent normal boots stay quiet.
//
// Also here (same NeoPixel toolkit): a green DOUBLE-PULSE acknowledging an
// on-demand health check (the medic's 0x01 poll) — visible proof the node
// heard home base and answered.
//
// Boards without a NeoPixel (HAS_NP unset) compile all of this to no-ops.
// Uses npset() from Utilities.h; include this AFTER it.

#ifndef BIRTHCRY_H
#define BIRTHCRY_H

#include <math.h>

#if defined(HAS_NP) && HAS_NP == true

#include <Preferences.h>

// Minimal HSV -> RGB (h 0..360, s/v 0..1) for the rainbow sweep.
inline void np_hsv(float h, float s, float v) {
    float c = v * s;
    float x = c * (1.0f - fabsf(fmodf(h / 60.0f, 2.0f) - 1.0f));
    float m = v - c;
    float r = 0, g = 0, b = 0;
    if      (h <  60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else              { r = c; b = x; }
    npset((uint8_t)((r + m) * 255), (uint8_t)((g + m) * 255),
          (uint8_t)((b + m) * 255));
}

// The birth cry itself (~4 s, blocking — runs at the end of setup(), before
// normal operation, so nothing else needs the CPU yet).
inline void birth_cry() {
    // 1+2: faint bubbling rainbow, brightness rising smoothly (quadratic ease).
    uint32_t t0 = millis();
    const uint32_t RAINBOW_MS = 2600;
    while (millis() - t0 < RAINBOW_MS) {
        float p = (millis() - t0) / (float)RAINBOW_MS;      // 0..1 ramp
        float hue = fmodf(millis() * 0.10f, 360.0f);        // slow hue spin
        // two beat-frequency sines = organic "bubbling" shimmer
        float bubble = 0.72f + 0.28f * sinf(millis() * 0.021f)
                                     * sinf(millis() * 0.0073f);
        np_hsv(hue, 1.0f, (0.06f + 0.74f * p * p) * bubble);
        delay(12);
    }
    // 3: smooth swell into bright white (desaturate while brightness tops out).
    t0 = millis();
    const uint32_t SWELL_MS = 700;
    while (millis() - t0 < SWELL_MS) {
        float p = (millis() - t0) / (float)SWELL_MS;
        float hue = fmodf(millis() * 0.10f, 360.0f);
        np_hsv(hue, 1.0f - p, 0.8f + 0.2f * p);
        delay(12);
    }
    // 4: blink white twice, then hand the pixel back dark.
    for (int i = 0; i < 2; i++) {
        npset(0, 0, 0);          delay(170);
        npset(255, 255, 255);    delay(190);
    }
    npset(0, 0, 0);
}

// Play the cry exactly once per NEW BUILD: the build stamp (__DATE__ __TIME__)
// changes on every rebuild, and NVS survives an app-only reflash — so a fresh
// flash cries once, and every later power-cycle boots quietly.
inline void birth_cry_maybe() {
    static Preferences _bc_prefs;
    const char* stamp = __DATE__ " " __TIME__;
    _bc_prefs.begin("rnm", false);
    String seen = _bc_prefs.getString("birthcry", "");
    if (seen != stamp) {
        birth_cry();
        _bc_prefs.putString("birthcry", stamp);
    }
    _bc_prefs.end();
}

// Health-check acknowledgement: two crisp green pulses after answering the
// medic's 0x01 poll — "heard you, here's my health" visible at the node.
inline void health_ack_blink() {
    for (int i = 0; i < 2; i++) {
        npset(0, 0xFF, 0);   delay(140);
        npset(0, 0, 0);      delay(120);
    }
}

#else   // no NeoPixel on this board — all no-ops

inline void birth_cry() {}
inline void birth_cry_maybe() {}
inline void health_ack_blink() {}

#endif  // HAS_NP
#endif  // BIRTHCRY_H
