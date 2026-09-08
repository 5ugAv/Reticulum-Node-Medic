"""The Ebyte EoRa-S3-900TB: ESP32-S3 + E22-900MM22S (a raw SX1262).

Ported 2026-09-08, BEFORE the board was ever plugged in, so everything here is
pinned against sources rather than a bench reading. What made that safe was
that three sources disagreed and two of them agreed with each other:

  * Meshtastic variant CDEBYTE_EoRa-S3 - names this exact hardware chain in
    its own comment (EoRa-S3-900TB <- E22-900MM22S <- SX1262)
  * Tech500/EoRa-PI-Foundation - a working sketch from someone who owns one
  * Ebyte's own product page - disagreed on DIO1/BUSY/LED and also called it a
    433 MHz board, so it was discarded as misread

The first two agree on every pin. These tests hold that agreement in place.
"""

from ui.board_detect import _USB_KIND, parse_flash_size, parse_psram
from workflows.rnode_boards import RNODE_BOARDS, custom_boards
from workflows import power_compat


def test_the_board_is_in_the_catalogue_and_buildable_here():
    b = RNODE_BOARDS["eora_s3"]
    assert b.platform == "ESP32-S3" and b.modem == "SX1262"
    assert b.flash_method == "arduino_cli"      # built here, not from upstream
    assert b.board_model == 0x47
    assert b in custom_boards(), "must appear in a picker, not fall between them"


def test_cdc_on_boot_is_pinned_in_the_fqbn():
    """THE defect that would ship a silent board.

    The generic esp32s3 FQBN leaves CDCOnBoot disabled, which maps the
    firmware's Serial to UART0 on GPIO 43/44. The board then flashes perfectly
    and says nothing at all over USB, so rnodeconf can never reach it. The
    proven XIAO S3 carries the same flag for the same reason.
    """
    fqbn = RNODE_BOARDS["eora_s3"].fqbn
    assert "CDCOnBoot=cdc" in fqbn, "without this the flashed board is mute on USB"
    assert "FlashSize=4M" in fqbn, "ESP32-S3FH4R2 is 4 MB; a wrong size boot-loops it"


def test_it_is_a_native_usb_board_not_a_bridged_one():
    """Keeps it out of ttyUSB shortlists, where only the CP2102-bridged
    Heltec V3 can legitimately appear. Evidence: the Meshtastic board JSON
    declares hwid 303a:1001 (Espressif native USB), and the board carries no
    bridge chip."""
    assert _USB_KIND["eora_s3"] == "native"


def test_the_radio_can_be_powered_by_something():
    """Every pickable board needs a power model, or the Pi-pairing check
    silently has nothing to say about it."""
    e = power_compat.BOARD_POWER["eora_s3"]
    assert e["peak_ma"] > 0
    assert e["src"] == "estimate", (
        "still an estimate - replace with a measured figure once one of these "
        "has been put on a meter")


def test_measured_traits_would_separate_it_from_the_other_s3_boards():
    """The identification story, as an executable claim.

    Unflashed, this board is indistinguishable from every other ESP32-S3 over
    USB - they all enumerate as 303a:1001. What separates it is what esptool
    MEASURES: the ESP32-S3FH4R2 carries 4 MB of in-package flash and 2 MB of
    PSRAM, where the XIAO reads 8 MB with no PSRAM and the Heltec V4 reads
    16 MB.

    This asserts the PARSING, not the board: the figures are a prediction
    until a real one is read, and ui.board_traits learns the truth on first
    birth rather than trusting anything written here.
    """
    esptool_out = (
        "Chip is ESP32-S3 (QFN56) (revision v0.2)\n"
        "Features: WiFi, BLE, Embedded PSRAM 2MB (AP_3v3)\n"
        "Detected flash size: 4MB\n")
    assert parse_flash_size(esptool_out) == "4MB"
    assert parse_psram(esptool_out) == "2MB"


def test_the_provisioning_identity_does_not_collide():
    """product 0xD3 / model 0xCF, checked against every other board here."""
    me = RNODE_BOARDS["eora_s3"].provision
    assert me["product"] == "d3" and me["model"] == "cf"
    clashes = [b.key for b in RNODE_BOARDS.values()
               if b.key != "eora_s3" and b.provision
               and b.provision.get("product") == me["product"]]
    assert not clashes, f"product byte 0xD3 already used by {clashes}"


# --- the display -----------------------------------------------------------
# Added 2026-09-08 after the panel came up. Display.h keeps its OWN chain of
# `#if BOARD_MODEL ==` blocks, independent of HAS_DISPLAY in Boards.h, and the
# board was in none of them. The port lives in these patch files, so a dropped
# or edited patch is a screenless board that still builds and still transmits.

def _port_patch(name):
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "assets/firmware-ports/eora_s3" / name
    assert p.exists(), f"{name} missing - the port is incomplete without it"
    return p.read_text()


def test_the_display_patch_starts_the_i2c_bus():
    """The fatal hunk. No Wire.begin branch means the panel is never addressed.

    Note this is the whole bug: the geometry hunk alone looks like it should
    work, and does nothing, because nothing ever drives the pins it names.
    """
    patch = _port_patch("display.patch")
    added = [l[1:] for l in patch.splitlines() if l.startswith("+")]
    assert "#elif BOARD_MODEL == BOARD_EORA_S3" in [l.strip() for l in added]
    begins = [l for l in added if "Wire.begin(SDA_OLED, SCL_OLED)" in l]
    assert begins, "no Wire.begin() branch - the I2C bus never starts"


def test_display_and_board_patches_agree_on_the_i2c_pins():
    """Two files name these pins under two different sets of macro names.

    Boards.h calls them I2C_SDA/I2C_SCL and Display.h calls them
    SDA_OLED/SCL_OLED. Nothing in the compiler connects the two, so they can
    drift apart silently and the screen just stops working.
    """
    def pins(patch, names):
        found = {}
        for line in patch.splitlines():
            if not line.startswith("+"):
                continue
            parts = line[1:].split()
            if len(parts) == 3 and parts[0] == "#define" and parts[1] in names:
                found[parts[1]] = int(parts[2])
        return found

    board = pins(_port_patch("boards.patch"), {"I2C_SDA", "I2C_SCL"})
    disp = pins(_port_patch("display.patch"), {"SDA_OLED", "SCL_OLED"})

    assert board == {"I2C_SDA": 18, "I2C_SCL": 17}, board
    assert disp == {"SDA_OLED": 18, "SCL_OLED": 17}, disp
    assert board["I2C_SDA"] == disp["SDA_OLED"]
    assert board["I2C_SCL"] == disp["SCL_OLED"]


# --- the crystal ------------------------------------------------------------
# The defect that made this board look completely healthy while emitting and
# hearing nothing: HAS_TCXO true, telling the SX1262 to drive a 1.8 V TCXO from
# DIO3 that this board does not have. Radio online, correct parameters,
# transmits accepted, every counter advancing, 0 bytes on air in either
# direction. Two sources say XTAL: Meshtastic's CDEBYTE_EoRa-S3 variant ("uses
# an XTAL, thus we do not need DIO3 as TCXO voltage reference" - while its
# sister EoRa-Hub DOES declare one) and Tech500/EoRa-PI-Foundation passing
# RadioLib "0.0, // No TCXO (EoRa Pi uses XTAL)".

def test_the_board_is_declared_xtal_not_tcxo():
    """HAS_TCXO must stay false. Flipping it back is a silently dead radio."""
    added = [l[1:] for l in _port_patch("boards.patch").splitlines()
             if l.startswith("+")]
    tcxo = [l for l in added if "HAS_TCXO" in l]
    assert len(tcxo) == 1, tcxo
    flag = tcxo[0].split("//")[0].strip().rstrip(",").strip()
    assert flag == "false", (
        "HAS_TCXO is %r - this board has a plain crystal. True gives a radio "
        "that reports online and puts nothing on air." % flag)


def test_no_tcxo_branch_is_reintroduced_in_the_radio_patch():
    """The tempting wrong fix: a sane-looking 1.8 V branch in enableTCXO().

    It makes the radio come online, which is why it survived a whole evening.
    With HAS_TCXO false the branch is dead code, so its presence means someone
    has been round the loop again.
    """
    assert "MODE_TCXO" not in _port_patch("radio.patch")


def test_dio2_stays_the_rf_switch():
    """Confirmed upstream, and NOT the fault however much it looked like one.

    Symmetric TX+RX failure pointed here first; the antenna switch was fine.
    """
    added = [l[1:] for l in _port_patch("boards.patch").splitlines()
             if l.startswith("+")]
    sw = [l for l in added if "DIO2_AS_RF_SWITCH" in l]
    assert sw and sw[0].split("//")[0].strip().rstrip(",").strip() == "true"


# --- the RTNode-2400 build ---------------------------------------------------
# Added 2026-09-08. RTNode-2400 shares the RNode_Firmware lineage, so the port
# is the same four gaps plus the crystal. Two settings here would each give a
# board that builds and flashes perfectly and then fails silently, so both are
# pinned: HAS_TCXO false, and 4MB/quad-PSRAM rather than the XIAO's 16MB/octal.

def test_the_board_has_an_rtnode_target():
    from workflows.rtnode_build import RTNODE_TARGETS, target_for_board_key
    t = RTNODE_TARGETS["eora_s3"]
    assert t.build_env == "ebyte_eora_s3_sx1262_boundary_local"
    assert t.mechanism == "pio"          # ESP32-S3, not nRF serial DFU
    assert t.verify == "beacon"          # proven over the air, not by USB
    # the catalogue key and the target key are the same word here, so the
    # crossing must resolve without needing an alias entry
    assert target_for_board_key("eora_s3") == "eora_s3"


def test_it_reaches_the_operator_as_a_card():
    """A native-USB S3 is a V4 *or* a XIAO *or* now this — identical Espressif
    USB identity, so only the operator can tell them apart. Missing from the
    pool means the board can never be chosen."""
    from ui.rtnode_choice import S3_NATIVE_CARDS, ALL_CARDS
    assert "eora_s3" in S3_NATIVE_CARDS
    assert "eora_s3" in ALL_CARDS


def test_the_rtnode_port_declares_a_crystal_too():
    """Same killer as the RNode port, in a second firmware.

    The XIAO S3 block a few screens above says HAS_TCXO true. Copying the
    nearest neighbour is exactly how this board goes silent while looking
    perfectly healthy.
    """
    patch = _port_patch("rtnode-boards.patch")
    added = [l[1:].strip() for l in patch.splitlines() if l.startswith("+")]
    tcxo = [l for l in added if l.startswith("#define HAS_TCXO")]
    assert tcxo == ["#define HAS_TCXO false"], tcxo


def test_the_rtnode_env_is_not_a_copy_of_the_xiao_one():
    """4MB + QUAD psram. The XIAO env is 16MB + octal, and octal PSRAM claims
    GPIO 33-37 — precisely where this board's DIO1, BUSY and LED live."""
    from pathlib import Path
    raw = (Path(__file__).resolve().parent.parent
           / "assets/firmware-ports/eora_s3/rtnode-platformio-env.ini").read_text()
    # SETTINGS ONLY. The comments in that file explain what the XIAO env does
    # wrong for this board, so they name "16MB" and "opi" — and an assertion
    # that reads the whole file matches the explanation instead of the config.
    # (Cost one red test, 2026-09-08; same shape as grepping a command line
    # that merely mentions the thing being searched for.)
    env = "\n".join(l for l in raw.splitlines()
                    if l.strip() and not l.strip().startswith(";"))
    assert "board_upload.flash_size = 4MB" in env
    assert "board_build.psram_type = qspi" in env
    assert "memory_type = qio_qspi" in env
    assert "partitions_4mb_ota.csv" in env
    assert "opi" not in env, "octal PSRAM would claim the radio's own pins"
    assert "16MB" not in env


def test_the_rtnode_port_closes_the_same_four_gaps():
    """setTxPower dispatch, product whitelist, model check, LED functions —
    each one silently breaks a different thing when missing."""
    utils = _port_patch("rtnode-utils.patch")
    added = "\n".join(l[1:] for l in utils.splitlines() if l.startswith("+"))
    assert "MODEL_CF) LoRa->setTxPower" in added, "else TX power stays 0 dBm"
    assert "PRODUCT_EORA_S3" in added, "else the firmware rejects its own EEPROM"
    assert "if (model == MODEL_CF) {" in added, "model-is-valid-for-this-board"
    assert "led_rx_on" in added


def test_the_beacon_vocabulary_names_this_board():
    """verify_beacon prints beacon.board_label on the success screen. Without
    an entry a perfectly good birth ends with "Board is beaconing:
    unknown(0x47)" — found while checking the walkthrough was ready, 2026-09-08.

    The real payload below was captured off the board.
    """
    from monitor.health_beacon import decode, BOARD_IDS
    assert BOARD_IDS.get(0x47) == "Ebyte EoRa-S3"
    b = decode(bytes.fromhex("020000002300cfba053b470007000000ff008080"))
    assert b.board_label == "Ebyte EoRa-S3"
    assert "unknown" not in b.board_label
