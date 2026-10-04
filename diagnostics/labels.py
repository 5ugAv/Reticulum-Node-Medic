"""Plain words for PROBE's check identifiers.

The diagnostic modules name their checks the way code does
(``serial_responsive``, ``eeprom_valid``, ``flow_control_atmega``) and PROBE
printed those identifiers as the check list a keeper reads while a board is
being examined (readiness ledger #87). This table says what each check LOOKS
AT, in the words the rest of the tool uses — "mesh service (rnsd)", "message
service (lxmd)", "Wi-Fi backbone". A check that is not listed here still gets
a readable fallback; the test keeps the table complete as modules grow.
"""

from __future__ import annotations

#: Severity → the one word PROBE shows beside an issue (was ``[critical]``).
#: PROBE wraps each in tr() as a literal so the catalogs carry them; this
#: table is the reference the test checks the screen against.
SEVERITY_WORDS = {"critical": "Critical", "warning": "Warning", "info": "Note"}

LABELS = {
    # client_connectivity
    "lxmd_after_rnsd": "Message service (lxmd) starts after the mesh service",
    "lxmd_peer_limit": "Propagation node peer limit",
    "lxmd_statistics_timeout": "Message service (lxmd) answers for statistics",
    "lxmd_store_full": "Message store has room",
    "lxmf_delivery_running": "Message delivery running",
    "lxmf_storage_dir": "Message store folder",
    "meshchat_tcp": "MeshChat TCP port",
    "meshtastic_board_connected": "Meshtastic board connected",
    "meshtastic_bridge_running": "Meshtastic bridge running",
    "tcp_interface_listening": "TCP interface listening",
    # gnss
    "gnss_data_flowing": "GPS data flowing",
    "gnss_enough_satellites": "Enough satellites in view",
    "gnss_has_fix": "GPS has a fix",
    # network_mesh
    "announce_heard_l3": "Announces heard from the mesh",
    "announces_sending": "Own announces going out",
    "channel_congestion": "Channel congestion",
    "mesh_ping_l2": "Mesh ping answered",
    "path_table_populated": "Path table has routes",
    "peers_heard": "Peers heard",
    "reticulum_identity": "Reticulum identity",
    # power_hardware
    "available_memory": "Free memory",
    "battery_level": "Battery level",
    "cooling_fan": "Cooling fan",
    "cpu_temperature": "CPU temperature",
    "filesystem_integrity": "Filesystem integrity",
    "sd_card_health": "SD card health",
    "uptime": "Uptime",
    # radio_firmware
    "antenna_rssi": "Antenna signal (RSSI)",
    "bandwidth": "Bandwidth",
    "coding_rate": "Coding rate",
    "eeprom_valid": "Board settings (EEPROM) valid",
    "firmware_blessing": "Firmware signature",
    "firmware_hash_valid": "Firmware hash valid",
    "firmware_present": "Firmware present",
    "firmware_version_current": "Firmware version current",
    "flow_control_atmega": "Flow control (ATmega boards)",
    "frequency": "Frequency",
    "heltec_baud": "Heltec serial speed",
    "heltec_hw_revision": "Heltec hardware revision",
    "heltec_v4_dual_antenna": "Heltec V4 antenna switch",
    "modemmanager_interference": "ModemManager leaving the port alone",
    "radio_in_service": "Radio in service",
    "radio_parked_frequency": "Radio parked frequency",
    "serial_responsive": "Serial port answers",
    "spreading_factor": "Spreading factor",
    "tx_power": "Transmit power",
    # reticulum_software
    "announce_interval": "Announce interval",
    "config_dir_permissions": "Config folder permissions",
    "config_line_endings": "Config file line endings",
    "config_present": "Config file present",
    "identity_permissions": "Identity file permissions",
    "lxmd_enabled": "Message service (lxmd) enabled at boot",
    "lxmd_running": "Message service (lxmd) running",
    "lxmd_service_flag": "Message service (lxmd) service flag",
    "radio_interface_up": "Radio interface up",
    "reticulum_installed": "Reticulum installed",
    "rnode_interface_configured": "RNode interface configured",
    "rnsd_enabled": "Mesh service (rnsd) enabled at boot",
    "rnsd_running": "Mesh service (rnsd) running",
    "rnsd_startup_race": "Mesh service (rnsd) start-up order",
    "serial_acl": "Serial port access list",
    "serial_port_exists": "Serial port exists",
    "serial_port_permission": "Serial port permission",
    "shared_instance_cascade": "Shared instance cascade",
    "shared_instance_port_conflict": "Shared instance port conflict",
    "transport_mode_enabled": "Transport mode enabled",
    "warm_boot_param_mismatch": "Radio settings kept across a warm boot",
    # rtnode_2400
    "beacon_received": "Health beacon received",
    "board_identified": "Board identified",
    "boot_fatal": "Boot errors",
    "boot_log_captured": "Boot log captured",
    "heap_fault": "Memory fault",
    "heap_low": "Memory low",
    "local_tcp_server": "Local TCP server",
    "lora_link": "LoRa link",
    "psram_v3_note": "PSRAM (V3 note)",
    "tcp_backbone": "Wi-Fi backbone link",
    "watchdog_armed": "Watchdog armed",
    "wifi_link": "Wi-Fi link",
    # system_health
    "clock_drift": "Clock drift",
    "disk_space": "Disk space",
    "ext4_journal_corruption": "Filesystem journal",
    "hardware_watchdog": "Hardware watchdog",
    "log2ram_active": "Logs kept in RAM (log2ram)",
    "log_rotation": "Log rotation",
    "ntp_sync": "Time sync (NTP)",
    "pip_version": "Python package tool (pip) version",
    "python_version": "Python version",
    "rnsd_unit_path": "Mesh service (rnsd) unit path",
    "sd_card_suspicious": "SD card looks suspicious",
    "swap_on_sd": "Swap on the SD card",
    "timezone_set": "Timezone set",
    "undervoltage": "Under-voltage",
    "zombie_processes": "Zombie processes",
}


def label_for(check_name: str) -> str:
    """The plain-words label for a check; an unlisted name is still readable
    (``some_new_check`` → ``Some new check``) rather than a raw identifier."""
    name = check_name or ""
    if name in LABELS:
        return LABELS[name]
    return name.replace("_", " ").strip().capitalize()

