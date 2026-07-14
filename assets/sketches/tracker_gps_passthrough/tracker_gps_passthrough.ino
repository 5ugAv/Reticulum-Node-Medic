// Heltec Wireless Tracker -> USB NMEA passthrough, so a Raspberry Pi (which has
// no GPS of its own) gets a fix via gpsd. The Tracker's UC6580 GNSS talks to the
// ESP32-S3 over UART1; this sketch powers the GNSS, then bridges that UART to the
// USB CDC serial the Pi sees. gpsd on the Pi reads the resulting NMEA stream, and
// the tool's monitor.geo.read_gps() (gpspipe) picks up the fix.
//
// This is a DEDICATED GPS role — it is not RNode firmware. Flash a Tracker you
// are setting aside as the medic's location source.
//
// PINOUT — from Heltec's official Wireless Tracker GNSS example. VERIFY against
// your own Heltec_Wireless_Tracker_RNode build set / hardware before relying on
// it: a wrong pin just means no NMEA appears, and it's a one-line fix here.
#define VGNSS_CTRL 3      // drive HIGH to power the UC6580 (Heltec Vext-GNSS)
#define GNSS_RX_PIN 33    // ESP32 RX  <- GNSS TX
#define GNSS_TX_PIN 34    // ESP32 TX  -> GNSS RX (lets gpsd configure the module)
#define GNSS_BAUD 115200  // UC6580 default

// USB identity — so a human (and udev) can tell this board from the medic's
// RNode at a glance. Port detection still keys off the NMEA stream, not this.
#define USB_PRODUCT "Reticulum-Medic-GPS"
#define USB_MANUFACTURER "ReticulumNodeMedic"

#if ARDUINO_USB_CDC_ON_BOOT
#include "USB.h"
#endif

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
