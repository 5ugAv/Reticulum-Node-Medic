"""Add the Node Medic BIRTH CRY to the stock RNode firmware (V4 RGB build).

    python3 apply_birth_cry.py path/to/RNode_Firmware.ino

Writes ``BirthCry.h`` next to the sketch and hooks the sketch itself:

  * ``#include "BirthCry.h"`` right after ``#include "Utilities.h"`` (BirthCry
    uses Utilities' ``npset``/pixel globals, so order matters);
  * ``birth_cry_maybe();`` just before ``validate_status();`` at the end of
    ``setup()`` — after all hardware init, before normal operation, so when the
    cry ends the firmware's own LED language takes over (white standby
    breathing while the radio waits for a host / Reticulum connection).

The choreography is BYTE-IDENTICAL to firmware/rtnode-2400/BirthCry.h (the
operator-tuned ~13 s: 9 s ember grow -> 1 s rainbow ignition into white ->
accelerating flutter-burst -> pop -> melt). Both firmwares share the same
npset() with the same NP_M scaling on the V4, so it renders the same light.
Plays ONCE per new build (NVS build stamp); later boots stay quiet.

Idempotent: re-running refreshes BirthCry.h and skips existing hooks.
"""

from __future__ import annotations

import argparse
import os
import sys

INCLUDE_ANCHOR = '#include "Utilities.h"'
CALL_ANCHOR = "// Validate board health, EEPROM and config"
INCLUDE_LINE = '#include "BirthCry.h"'
CALL_LINES = (
    "  // RNM: first-flash birth cry (~13 s, once per new build) — then the\n"
    "  // normal LED language takes over (white standby breathing while the\n"
    "  // radio waits for its host / Reticulum connection).\n"
    "  birth_cry_maybe();\n\n")

BIRTH_CRY_H = r'''// Node Medic — Birth Cry for the stock RNode firmware (RGB V4 build).
// Same operator-tuned choreography as the RTNode-2400 BirthCry.h (~13 s:
// 9 s ember grow -> 1 s rainbow ignition into white -> accelerating
// flutter-burst -> pop -> melt to black). Plays ONCE per newly-flashed
// build (NVS build-stamp gate); every later power-cycle boots quietly and
// the firmware's own LED language (white standby breathe, RX blue, TX
// amber) takes over the moment the cry ends.
//
// Uses npset() from Utilities.h — include this AFTER it. Preferences (NVS)
// is ESP32-only; other MCUs compile to no-ops.

#ifndef RNM_BIRTHCRY_H
#define RNM_BIRTHCRY_H

#include <math.h>

#if defined(HAS_NP) && HAS_NP == true && MCU_VARIANT == MCU_ESP32

#include <Preferences.h>

// Minimal HSV -> RGB (h 0..360, s/v 0..1) for the rainbow sweep.
inline void rnm_np_hsv(float h, float s, float v) {
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

// The cry (~13 s, blocking — runs at the end of setup(), before normal
// operation). Choreography identical to the RTNode-2400 version.
inline void birth_cry() {
    // 1: SLOW GROW — nine seconds crawling up through the dim range.
    uint32_t t0 = millis();
    uint32_t last = t0;
    const uint32_t GROW_MS = 9000;
    float hue = 0.0f;
    while (millis() - t0 < GROW_MS) {
        uint32_t now = millis();
        float dt = (float)(now - last); last = now;
        float p  = (now - t0) / (float)GROW_MS;
        hue = fmodf(hue + dt * (0.04f + 0.11f * p), 360.0f);
        float bubble = 0.72f + 0.28f * sinf(now * 0.021f)
                                     * sinf(now * 0.0073f);
        float b = 0.008f * expf(2.70f * p);                 // 0.8% -> ~12%
        rnm_np_hsv(hue, 1.0f, b * bubble);
        delay(12);
    }
    // 2: SKYROCKET — ignition, hue spin exploding, burning out into white.
    t0 = millis(); last = t0;
    const uint32_t ROCKET_MS = 1000;
    while (millis() - t0 < ROCKET_MS) {
        uint32_t now = millis();
        float dt = (float)(now - last); last = now;
        float p = (now - t0) / (float)ROCKET_MS;
        hue = fmodf(hue + dt * (0.15f + 0.45f * p), 360.0f);
        float b = 0.12f * expf(2.12f * p);                  // 12% -> 100%
        float sat = 1.0f - p * p;                           // burn out to white
        rnm_np_hsv(hue, sat, b);
        delay(10);
    }
    // 3: FLUTTER-BURST — the hatching: accelerating white crackle...
    float period = 340.0f;
    while (period > 18.0f) {
        npset(0, 0, 0);        delay((uint32_t)(period * 0.45f));
        npset(255, 255, 255);  delay((uint32_t)(period * 0.55f));
        period *= 0.88f;
    }
    // ...ending in one held pop that melts smoothly to black.
    npset(255, 255, 255);
    delay(260);
    for (int f = 100; f >= 0; f -= 4) {
        uint8_t v = (uint8_t)((255 * f) / 100);
        npset(v, v, v);
        delay(10);
    }
    npset(0, 0, 0);
}

// Once per NEW BUILD: the build stamp changes on every rebuild, and NVS
// survives reboots — a fresh flash cries once, later boots stay quiet.
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

#else   // no NeoPixel / not ESP32 — all no-ops

inline void birth_cry() {}
inline void birth_cry_maybe() {}

#endif  // HAS_NP && ESP32
#endif  // RNM_BIRTHCRY_H
'''


def apply(ino_path: str) -> str:
    """Patch the sketch at *ino_path* (writes BirthCry.h beside it). Returns a
    human summary. Raises ValueError when an anchor is missing."""
    sketch_dir = os.path.dirname(os.path.abspath(ino_path))
    with open(os.path.join(sketch_dir, "BirthCry.h"), "w") as f:
        f.write(BIRTH_CRY_H)

    with open(ino_path) as f:
        src = f.read()
    changed = []
    if INCLUDE_LINE not in src:
        if INCLUDE_ANCHOR not in src:
            raise ValueError(f"anchor missing: {INCLUDE_ANCHOR}")
        src = src.replace(INCLUDE_ANCHOR,
                          INCLUDE_ANCHOR + "\n" + INCLUDE_LINE, 1)
        changed.append("include")
    if "birth_cry_maybe();" not in src:
        if CALL_ANCHOR not in src:
            raise ValueError(f"anchor missing: {CALL_ANCHOR}")
        src = src.replace("  " + CALL_ANCHOR, CALL_LINES + "  " + CALL_ANCHOR, 1)
        changed.append("setup-hook")
    if changed:
        with open(ino_path, "w") as f:
            f.write(src)
    return ("BirthCry.h written; patched: " + ", ".join(changed)
            if changed else "BirthCry.h refreshed; hooks already present.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ino", help="path to RNode_Firmware.ino")
    args = ap.parse_args()
    try:
        print(apply(args.ino))
    except (OSError, ValueError) as e:
        print(f"birth-cry patch failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
