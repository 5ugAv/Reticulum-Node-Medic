# Getting a Reticulum messenger onto a phone

The Node Medic is the mesh's propagation node, not the messenger. The messaging
happens on a **phone**, running an app the medic hands over. This is how that
works, what to do when the app the medic carries will not install, and the one
answer people ask for most.

---

## The short answer about iPhones

**There is no iPhone or iPad version. Not of Columba, not of Sideband.**

Checked against both projects on 2026-08-16:

- **Columba** describes itself as a *"Native Android messaging app"*. It is
  written in Kotlin. Across its ten most recent releases the only file types
  published are `.apk`, `.aab` and `.sha256` — all Android. There has never been
  an `.ipa`.
- **Sideband** publishes `.apk`, `.appimage`, `.dmg`, `.whl` and `.zip`. No
  `.ipa`, in any of its ten most recent releases.

For Sideband this is **a stated policy, not a gap waiting to be filled.** From
its own README:

> Sideband will never be released on app store platforms that does not support
> complete control of the APK signing directly from the developer.

> The Sideband application will *never* be distributed with an Apple-controlled
> digital signature, as this will allow Apple to simply disable Sideband from
> running on your system if they decide to do so, or are forced to by
> authorities or other circumstances.

Apple permits app distribution only through the App Store, signed by Apple. That
is precisely the arrangement the author refuses — because a mesh that Apple can
switch off is not the thing being built. **So do not wait for an iOS version.
Deciding not to ship one is the point.**

### What an Apple owner can actually do

- **A Mac can run Sideband.** There is a `.dmg` in every recent release. A
  laptop on the mesh is a real option, just not a pocket one. Note macOS may
  need an exception for it, for the same signing reason as above.
- **Use a cheap Android handset as the mesh device.** It does not need a SIM or
  a phone plan — the mesh is the network. A second-hand Android is the normal
  answer here.
- **Talk to the node instead of through a phone.** For checking a node rather
  than messaging, the medic itself is the tool.

Say this to people plainly when handing out apps. Someone who walks away
believing an iPhone build is coming will be waiting a long time.

---

## Which Android build the medic carries, and why

A release can ship many APKs. Columba **v2.0.9 shipped 28 assets in a single
release**. They are not interchangeable, and the differences matter:

| In the filename | What it means |
|---|---|
| `universal` | Runs on **any** CPU. Biggest file. The safe default. |
| `arm64-v8a` | Almost every phone made since ~2017. |
| `armeabi-v7a` | Older 32-bit phones. |
| `x86_64` | Emulators and a few unusual tablets. Rare on real phones. |
| `official` vs `EXPERIMENTAL` | Which internal Reticulum implementation it uses. Take `official`. |
| `no-sentry` | Built **without crash reporting**. Sends nothing to a remote server. |
| `.aab` | A Play Store bundle. **Cannot be installed directly — ignore it.** |

The medic picks, in order: a real `.apk`, a release build over a pre-release
one, `universal` over an arch-split, a build without crash telemetry, then the
largest of whatever is left. Each step only narrows if something survives it, so
a release that offers no choice still yields its one APK.

For Columba v2.0.9 that resolves to
`columba-2.0.9-official-rns-py-universal-no-sentry.apk` (~103 MB).

`no-sentry` is deliberate. This tool exists for places with no infrastructure,
and is handed out by an operator who should not be traceable to the node. An app
that phones home by default contradicts both. The rule lives in
`workflows/phone_apps.py` as `_APK_PREFER_QUIET` and is one constant to change
if you disagree.

---

## If the app will not install on someone's phone

The carried build is the one that works on the widest range of hardware, but
"widest" is not "all". Work down this list.

**1. Check it is not the phone refusing, rather than the file.**
Android blocks installs from outside the Play Store until told otherwise. The
phone will say something like *"For your security, your phone is not allowed to
install unknown apps from this source"*. That is a setting, not a broken
download — allow it for the browser or file manager doing the installing.

**2. Check there is room.** These files are 100 MB and up, and the installed app
needs roughly the same again.

**3. Get a build matched to that phone.** Find the phone's CPU first — Settings
▸ About phone, or any "device info" app. Then:

- Go to the project's releases page:
  - Columba — `https://github.com/torlando-tech/columba/releases`
  - Sideband — `https://github.com/markqvist/Sideband/releases`
- Open the **latest** release and expand its **Assets** list.
- Pick the file whose name matches, using the table above:
  - a 64-bit phone (nearly all since ~2017) → `arm64-v8a`
  - an older or very cheap phone → `armeabi-v7a`
  - unsure → `universal`, which is what the medic already gave you
- Prefer `official` over `EXPERIMENTAL`, and `no-sentry` if it is offered.
- **Never download the `.aab`.** It will not install.

**4. If the newest release will not run, try the one before it.** Releases are
listed newest first; a version that dropped support for older Android will
usually work at an earlier tag.

**5. Verify what you downloaded, if the release offers a way.** Columba
publishes a `.sha256` beside each APK. Sideband signs its releases and states
its certificate hashes in its README — and warns explicitly not to install a
copy from anywhere else if those hashes do not match. **This matters more than
usual for a messaging app on a private mesh.**

### This step needs the internet

Everything above requires a connection, on some device — which the phone with
the problem may not have. Do it **before** going somewhere without one. If you
expect to hand apps to several people, carry more than the default build:
download the arch-split APKs too and keep them beside the carried one.

---

## What the medic does and does not do

- It **carries** APKs so a phone can be given one with no internet present.
- It **serves** the chosen APK over Wi-Fi behind a QR code the phone scans.
- It **does not** install anything on the phone; the person does that.
- It **cannot** refresh what it carries without a connection. Top up before you
  leave — a cache discovered stale in the field is a cache that is not there.
