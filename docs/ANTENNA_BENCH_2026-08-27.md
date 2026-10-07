# Antenna A/B bench — a T114, indoors at the medic, 2026-08-27
# Method: CMD_STAT_CHTM poll over USB serial (/tmp/antenna_read.py), read-only.
# Lesson baked in: higher floor != worse antenna — a good antenna hears more of
# everything; ranking needs the known-signal SNR leg.

| run | antenna | position | noise floor (median) | notes |
|-----|---------|----------|----------------------|-------|
| A   | stock stubby (came with T114) | upright in case | -112 dBm | 11:47, quiet-below-clear anchor |
| B1  | 40cm white whip + pigtail     | flat on ground  | -106 dBm | 11:54, rock steady |
| B2  | 40cm white whip + pigtail     | upright, hand-held | -104 dBm | 12:00, +2dB over flat = polarisation/ground effect |
| C1  | folding antenna, FOLDED (element horizontal) | hand-held | -109 dBm | 12:04; first-sample chload 6.44% artifact repeats across runs — discard sample 0 |
| C2  | folding antenna, UNFOLDED | upright, hand-held | -105 dBm | 12:06; unfolding = +4dB hearing; ~equal to the 40cm whip |
| D   | 17cm whip | upright, hand-held | -118 dBm | 12:10; QUIETEST = most-deaf; 17cm ~ 433MHz quarter-wave -> suspected wrong-band antenna. Airtime/chload 6.44% first-sample artifact again. |

## Conclusions (ambient leg)
- 14 dB spread best-to-worst ear: 40cm whip (-104) > unfolded folder (-105) > folded folder (-109) > stock stubby (-112) > 17cm whip (-118, suspected 433MHz).
- Higher floor = better ear, NOT worse antenna. The medic whips haze BECAUSE they hear well.
- Folding a folder costs ~4dB; flat-on-ground costs ~2dB vs upright.
- Suspected wrong-band antennas show as anomalously QUIET (>=6dB below peers) — TRIAGE should flag that explicitly.
- Method notes: discard sample 0 (stale 6.44% artifact); TNC mode answers ~1 in 3 polls, retry.
- NEXT LEG (ranking proof): known-signal SNR — medic TX fixed poll, score each antenna by margin above its own floor.
| E   | 8cm AliExpress whip | upright, hand-held | -103 dBm | 12:14; BEST ear — tuned 915 quarter-wave beats length. Sample 11: -74dBm burst w/ interference flag = live catch. |

## Final ambient ranking: E 8cm (-103) > B 40cm (-104) > C unfolded (-105) > C folded (-109) > A stubby (-112) > D 17cm (-118, 433-band, QUARANTINE).

## VSWR leg — AAI RF vector impedance analyzer @ 915.125 MHz (operator bench, 2026-08-27)
(NB: two earlier shots were at 1195.125 MHz — wrong entry, discarded. Always verify the test frequency first.)

| antenna | VSWR | R (ohm) | X (ohm) | S11 (dB) | verdict |
|---------|------|---------|---------|----------|---------|
| stock T114 stubby | 7.843 | 125.2 | +178.1 | -2.23 | POOR: ~60% power reflected (~-4dB TX); matches its deaf -112 floor |
| 40cm white whip (+its 915MHz pigtail) | 1.817 | 80.76 | -23.18 | -10.74 | GOOD: ~8% reflected (~0.4dB); agrees with best-class -104 ear |
| folding antenna, FOLDED | 1.383 | 63.63 | -12.35 | -15.86 | BEST MATCH so far, while folded! Proves VSWR is blind to a fold: match fine, pattern/polarisation ruined (the -4dB ear loss). TRIAGE needs BOTH tests. |
| folding antenna, STRAIGHT | 1.255 | 62.71 | -1.18 | -18.91 | X~0 = dead on resonance; ~1.3% reflected. Folded-vs-straight VSWR nearly identical (1.38 vs 1.26) = PROOF VSWR is blind to a fold. |
| 17cm short whip, marked "915" | 1.244 | 61.90 | -2.58 | -19.26 | RETRACTION: NOT 433-band — perfectly matched at 915 (17cm = 915 half-wave, base-matched). MYSTERY: best match + deafest ear (-118). Suspect LOSSY matching/dummy element — VSWR cannot see loss. Sweep 400-1400 to discriminate: narrow dip = real, flat-low = sponge. |
| 8cm AliExpress whip | 2.958 | 81.44 | +65.50 | -6.11 | mediocre match (~24% refl, ~1.2dB) yet BEST ear (-103): radiation efficiency dominates. Prediction "<2" WRONG. |

## COMBINED CONCLUSION: ear-vs-voice rankings nearly INVERT at the extremes (best match = deafest ear).
## VSWR spread costs <=1.2dB (excl. stubby 7.8 ~4dB); ear spread = 15dB -> efficiency/pattern dominate.
## TRIAGE doctrine: (1) VSWR can lie (matched sponge); (2) ear test sees folds/orientation/efficiency;
## (3) gross mismatch still burns power + stresses PA; (4) known-signal SNR is the one true ranking.
## Fleet picks: 8cm Ali or 40cm whip for nodes; folder good if STRAIGHT; stubby+17cm quarantined.
| LilyGo T-Echo-Plus twin of the 17cm (marked "0915") | 5.206 | 75.61 | +110.4 | -3.38 | TWINS DO NOT MATCH: same shell, wildly different electricals (1.24 vs 5.21). Inconsistent manufacture — appearance/labels prove nothing. T-Echo Plus shipped wearing this: owes an antenna swap. Quarantine the model line. |
| LilyGo twin EAR TEST on T114 | — | — | — | — | floor -107: 11dB BETTER ear than the original (-118) despite worse VSWR (5.2 vs 1.24). VERDICT SEALED: the original 17cm is a LOSSY SPONGE (perfect match = absorbed power). Also caught a -44dBm near burst + ~2.5% chload. |

## FINAL VERDICTS: 8cm (-103/3.0) + 40cm (-104/1.8) = fleet picks; folder (-105/1.26) if straight;
## LilyGo twin (-107/5.2) mediocre, swap off T-Echo Plus; stubby (-112/7.8) replace everywhere;
## original 17cm (-118/1.24) CONFIRMED SPONGE — destroy/label so it never reaches a node.
| thick BREE whip (medic Jonesey original) EAR on T114 | — | — | — | — | floor -103: TIES 8cm for BEST ear of all 7. Jonesey VINDICATED — the haze was honest reporting, not a fault. Medic now wears the 8cm Ali; VSWR of the thick whip to follow. |
| thick BBREE whip (ex-medic) | 2.337 | 74.96 | -41.39 | -7.94 | ~16% refl (~0.8dB); with its -103 ear = top-class antenna, decent-not-perfect match. Campaign COMPLETE: 7 antennas, both instruments. |

## OPERATOR FINAL PICK (2026-08-27): 1st = thick BBREE whip, 2nd = 8cm Ali stubby.
## Deferred: big grey N-type fiberglass base antenna — needs an N->SMA adapter; test when a suitable connection is in hand.
