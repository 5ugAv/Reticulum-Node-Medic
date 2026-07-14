// Heltec Wireless Tracker -> USB NMEA passthrough, so a Raspberry Pi (which has
// no GPS of its own) gets a fix via gpsd. The Tracker's UC6580 GNSS talks to the
// ESP32-S3 over UART1; this sketch powers the GNSS, configures it for the best
// possible fix, then bridges that UART to the USB CDC serial the Pi sees. gpsd on
// the Pi reads the NMEA stream, and the tool's monitor.geo.read_gps() (gpspipe)
// picks up the fix.
//
// This is a DEDICATED GPS role — it is not RNode firmware. Flash a Tracker you
// are setting aside as the medic's location source.
//
// PINOUT — from Heltec's official Wireless Tracker GNSS example. VERIFY against
// your own Heltec_Wireless_Tracker_RNode build set / hardware before relying on
// it: a wrong pin just means no NMEA appears, and it's a one-line fix here.
#define VGNSS_CTRL 3      // drive HIGH to power the UC6580 (Heltec Vext-GNSS)
#define GNSS_RX_PIN 33    // ESP32 RX  <- GNSS TX
#define GNSS_TX_PIN 34    // ESP32 TX  -> GNSS RX (also lets us configure the module)
#define GNSS_BAUD 115200  // UC6580 default

// ---- UC6580 tracking configuration --------------------------------------
// The UC6580 is a DUAL-FREQUENCY (L1 + L5/L2), multi-constellation receiver
// (Unicore Firebird II). Out of the box it may run a reduced mode; this init
// turns on every band + system for the tightest fix. Command + hex mask are the
// field-proven Meshtastic values (Unicore protocol spec 1.4.2.5 CFGSYS).
//
//   $CFGSYS,h35155  ->  GPS L1 & L5 + BeiDou B1I & B2a + GLONASS L1
//                       + Galileo E1 & E5a + SBAS + QZSS
//
// This set is well-suited to Australia: QZSS is a regional augmentation system
// for the Asia-Oceania region, and BeiDou has strong Asia-Pacific coverage.
// CFGSYS resets the receiver, so we pause after it. Unlike Meshtastic we do NOT
// disable GSV/GSA — gpsd uses them for fix quality (DOP) and satellites-in-view,
// and the USB CDC link has bandwidth to spare. Edit GNSS_CFGSYS to retune the
// constellation/band mask for a different region.
#define GNSS_CFGSYS "$CFGSYS,h35155"

// USB identity — so a human (and udev) can tell this board from the medic's
// RNode at a glance. Port detection still keys off the NMEA stream, not this.
#define USB_PRODUCT "Reticulum-Medic-GPS"
#define USB_MANUFACTURER "ReticulumNodeMedic"

#if ARDUINO_USB_CDC_ON_BOOT
#include "USB.h"
#endif

static void gnssSend(const char *cmd) {
  Serial1.print(cmd);
  Serial1.print("\r\n");        // Unicore config lines accept no checksum
}

static void configureGnss() {
  // Give the receiver a moment to boot after power-up before configuring.
  delay(300);
  gnssSend(GNSS_CFGSYS);
  // CFGSYS restarts the receiver — hold off streaming until it is back.
  delay(1200);
}

void setup() {
#if ARDUINO_USB_CDC_ON_BOOT
  USB.productName(USB_PRODUCT);
  USB.manufacturerName(USB_MANUFACTURER);
  USB.begin();
#endif
  Serial.begin(115200);                 // USB CDC to the Pi (baud is ignored on CDC)

  pinMode(VGNSS_CTRL, OUTPUT);
  digitalWrite(VGNSS_CTRL, HIGH);       // power the GNSS receiver
  delay(100);

  Serial1.begin(GNSS_BAUD, SERIAL_8N1, GNSS_RX_PIN, GNSS_TX_PIN);
  configureGnss();                      // enable dual-band, all constellations
}

void loop() {
  // GNSS -> host: the NMEA gpsd consumes.
  while (Serial1.available()) {
    Serial.write(Serial1.read());
  }
  // host -> GNSS: let gpsd send configuration/UBX/CAS commands to the module.
  while (Serial.available()) {
    Serial1.write(Serial.read());
  }
}
