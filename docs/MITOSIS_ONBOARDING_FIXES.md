# Mitosis + onboarding usability fixes

From the 2026-08-26 three-persona walkthrough (Marnie / Dev / Tomas). Ranked by
how badly a real non-technical person is blocked. Checked off as landed.

## Functional / honesty (do regardless)
- [x] **1. Reboot bridge (BUG).** Clone only `enable`s the tool, never starts or
      reboots it, so the new medic sits at a blank console after "Clone
      finished". Add a final ladder step that reboots the new medic (also
      applies the pending EEPROM bake). *(workflows/clone.py)*
- [x] **2. Blank-screen reassurance.** The new medic's own screen stays dark/text
      until its setup — nothing says so. Add a line to POWER / CABLE / CLONE and
      rewrite the finish text ("switch it off and on once"). *(mitosis_screen.py)*
- [x] **7. Plain clone-failure messages.** Engineer-only red rows ("wheelhouse",
      "pip3", "rsync", "sudo refused") strand a solo user. Pair each with a
      plain "what to do". *(workflows/clone.py)*

## Security ceremony — enforce what the copy promises (latent landmines)
- [x] **6a. Minimum passphrase.** `_set_passphrase` accepts `1234`. Enforce a
      real minimum with an on-screen strength read. *(vault_factors / wizard)*
- [x] **6b. Reject L/Z pattern.** Hint forbids it; `encode_pattern` accepts it.
- [x] **6c. "Three options" over five cards.** Fix the LEVEL body count.
- [x] **6d. Summary buries "NOT encrypted".** Don't celebrate green ticks over
      the not-encrypted truth; lead with the real state.
- [x] **6e. Welcome Back silently skips security.** Confirm before skipping.

## Walkthrough redesign (heavier; shape with operator)
- [ ] **3. Pre-flight "what you need".** Open MITOSIS with a parts checklist
      (2nd Pi 5, SD card + reader, ethernet cable) + graceful back-out.
- [ ] **4. Name/label the physical parts + power-off-first.** Plain language and
      real photos on INSERT (card slot), WRITTEN (where the card goes, power off
      first), CABLE (plain cable name + socket), POWER (name the button).
      *(photos may not exist — flag which are needed)*
- [ ] **5. Findability.** MITOSIS is buried under BIRTH behind a muted button;
      give it a plain home-screen entry ("Make another Node Medic").
