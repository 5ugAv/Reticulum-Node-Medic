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
// normal operation, so nothing else needs the CPU yet). Operator-tuned
// choreography (2026-07-30 v2): the rainbow starts bubbling DIMLY with a lazy
// hue drift, then brightness AND colour-change speed ramp up together —
// accelerating to a fast vivid spin — peaking into bright white, then two
// deliberate blinks at the pace of a human working a hole punch.
inline void birth_cry() {
    // 1: bubbling rainbow — brightness and hue-spin accelerate in step.
    //    Hue is INTEGRATED (rate ramps 40°/s -> ~590°/s) so the acceleration
    //    is smooth, not a jump.
    uint32_t t0 = millis();
    uint32_t last = t0;
    const uint32_t RAINBOW_MS = 2400;
    float hue = 0.0f;
    while (millis() - t0 < RAINBOW_MS) {
        uint32_t now = millis();
        float dt = (float)(now - last); last = now;
        float p  = (now - t0) / (float)RAINBOW_MS;          // 0..1 ramp
        hue = fmodf(hue + dt * (0.04f + 0.55f * p * p), 360.0f);
        // two beat-frequency sines = organic "bubbling" shimmer
        float bubble = 0.72f + 0.28f * sinf(now * 0.021f)
                                     * sinf(now * 0.0073f);
        np_hsv(hue, 1.0f, (0.05f + 0.80f * p * p) * bubble);
        delay(12);
    }
    // 2: spin is at full speed — desaturate into bright white at the peak.
    t0 = millis(); last = t0;
    const uint32_t SWELL_MS = 500;
    while (millis() - t0 < SWELL_MS) {
        uint32_t now = millis();
        float dt = (float)(now - last); last = now;
        float p = (now - t0) / (float)SWELL_MS;
        hue = fmodf(hue + dt * 0.59f, 360.0f);              // keep max spin
        np_hsv(hue, 1.0f - p, 0.85f + 0.15f * p);
        delay(12);
    }
    // 3: two blinks, hole-punch cadence — a beat of dark, a solid punch of
    //    white, again — deliberate and mechanical, not a flicker.
    for (int i = 0; i < 2; i++) {
        npset(0, 0, 0);          delay(330);
        npset(255, 255, 255);    delay(340);
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
