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

#if MCU_VARIANT == MCU_ESP32
#include <Preferences.h>        // NVS — ESP32 only
#elif MCU_VARIANT == MCU_NRF52
#include <Adafruit_LittleFS.h>  // the same store the EEPROM emulation uses
#include <InternalFileSystem.h>
#endif

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
    // 1: SLOW GROW — five seconds crawling up through the dim range. Starts
    //    at the pixel's dimmest visible ember (~0.8%) and grows STEADILY (a
    //    gentle exponential, which the eye reads as constant growth) to
    //    "just showing itself" (~12%). Hue drifts slow, quickening slightly.
    uint32_t t0 = millis();
    uint32_t last = t0;
    const uint32_t GROW_MS = 9000;   // the long dawn — most of the song is the approach
    float hue = 0.0f;
    while (millis() - t0 < GROW_MS) {
        uint32_t now = millis();
        float dt = (float)(now - last); last = now;
        float p  = (now - t0) / (float)GROW_MS;             // 0..1
        hue = fmodf(hue + dt * (0.04f + 0.11f * p), 360.0f);
        // two beat-frequency sines = organic "bubbling" shimmer
        float bubble = 0.72f + 0.28f * sinf(now * 0.021f)
                                     * sinf(now * 0.0073f);
        float b = 0.008f * expf(2.70f * p);                 // 0.8% -> ~12%
        np_hsv(hue, 1.0f, b * bubble);
        delay(12);
    }
    // 2: SKYROCKET — the moment it shows itself, it ignites: ~700 ms from 12%
    //    to full, hue spin exploding, and the colour burns out into pure
    //    white on the way up. No dwell at the top — straight to the blinks.
    t0 = millis(); last = t0;
    const uint32_t ROCKET_MS = 1000;
    while (millis() - t0 < ROCKET_MS) {
        uint32_t now = millis();
        float dt = (float)(now - last); last = now;
        float p = (now - t0) / (float)ROCKET_MS;            // 0..1
        hue = fmodf(hue + dt * (0.15f + 0.45f * p), 360.0f);
        float b = 0.12f * expf(2.12f * p);                  // 12% -> 100%
        float sat = 1.0f - p * p;                           // burn out to white
        np_hsv(hue, sat, b);
        delay(10);
    }
    // 3: FLUTTER-BURST — the hatching. White flicker starting as deliberate
    //    blinks and ACCELERATING (each cycle 20% faster) into a rapid burst —
    //    the impression of something popping through, being born. Ends on one
    //    held flash that melts smoothly to black.
    float period = 340.0f;                       // ms — first, deliberate flutter
    while (period > 18.0f) {
        npset(0, 0, 0);        delay((uint32_t)(period * 0.45f));
        npset(255, 255, 255);  delay((uint32_t)(period * 0.55f));
        period *= 0.88f;                          // gentler accelerando = LONG crackle
    }
    // the final pop: hold bright, then the smooth melt to black.
    npset(255, 255, 255);
    delay(260);
    for (int f = 100; f >= 0; f -= 4) {           // ~260 ms melt
        uint8_t v = (uint8_t)((255 * f) / 100);
        npset(v, v, v);
        delay(10);
    }
    npset(0, 0, 0);
}

// Play the cry exactly once per NEW BUILD: the build stamp (__DATE__ __TIME__)
// changes on every rebuild, and NVS survives an app-only reflash — so a fresh
// flash cries once, and every later power-cycle boots quietly.
inline void birth_cry_maybe() {
    const char* stamp = __DATE__ " " __TIME__;
#if MCU_VARIANT == MCU_ESP32
    static Preferences _bc_prefs;
    _bc_prefs.begin("rnm", false);
    String seen = _bc_prefs.getString("birthcry", "");
    if (seen != stamp) {
        birth_cry();
        _bc_prefs.putString("birthcry", stamp);
    }
    _bc_prefs.end();
#elif MCU_VARIANT == MCU_NRF52
    // NVS is ESP32-only; here the stamp lives in a LittleFS file beside the
    // EEPROM store. __DATE__ " " __TIME__ is a fixed 20 characters, so an
    // in-place overwrite (FILE_O_WRITE does not truncate) is exact.
    Adafruit_LittleFS_Namespace::File f(InternalFS);
    char seen[32] = {0};
    if (f.open("/birthcry", Adafruit_LittleFS_Namespace::FILE_O_READ)) {
        f.read(seen, sizeof(seen) - 1);
        f.close();
    }
    if (strncmp(seen, stamp, sizeof(seen) - 1) != 0) {
        birth_cry();
        if (f.open("/birthcry", Adafruit_LittleFS_Namespace::FILE_O_WRITE)) {
            f.seek(0);
            f.write((const uint8_t*)stamp, strlen(stamp));
            f.close();
        }
    }
#endif
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
