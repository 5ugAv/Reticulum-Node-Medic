"""Node profile data model for the Reticulum Node Medic.

Foundation module: pure dataclasses and enums describing a Reticulum mesh
node under inspection. No I/O, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class NodeHardware(Enum):
    PI_3A_PLUS = "Raspberry Pi 3A+"
    PI_ZERO_2W = "Raspberry Pi Zero 2W"
    PI_5 = "Raspberry Pi 5"
    HELTEC_V4 = "Heltec LoRa32 V4"
    TBEAM_SUPREME = "LilyGO T-Beam Supreme"
    UNKNOWN = "Unknown"


class ConnectionMethod(Enum):
    USB_SERIAL = "USB-C serial"
    DIRECT_SERIAL = "Direct serial cable"
    SSH = "SSH over network"
    NONE = "Not connected"


class NodeRole(Enum):
    TRANSPORT = "Transport node"            # RTNode-2400 (microReticulum, no LXMF)
    PROPAGATION = "LXMF propagation node"   # Pi + RNode (runs rnsd + lxmd)
    GATEWAY = "Gateway node"
    MESHTASTIC_BRIDGE = "Meshtastic bridge node"
    UNKNOWN = "Unknown"


@dataclass
class RadioConfig:
    """LoRa radio parameters. Defaults are the Australian deployment
    defaults (915 MHz LIPD Class Licence band); all overridable."""

    frequency_mhz: float = 915.125
    bandwidth_khz: float = 125.0
    spreading_factor: int = 9
    coding_rate: int = 5
    tx_power_dbm: int = 17
    serial_port: str = "/dev/ttyUSB0"
    firmware_version: Optional[str] = None
    firmware_hash_set: bool = False


@dataclass
class NodeProfile:
    """Everything the tool knows about a single node during a session."""

    hardware: NodeHardware = NodeHardware.UNKNOWN
    role: NodeRole = NodeRole.UNKNOWN
    hostname: Optional[str] = None
    reticulum_identity_hash: Optional[str] = None
    connection: ConnectionMethod = ConnectionMethod.NONE
    connection_port: Optional[str] = None
    ssh_user: str = "pi"
    radio: RadioConfig = field(default_factory=RadioConfig)
    #: A board is physically attached (a serial port exists), regardless of
    #: whether it has been flashed yet — a BLANK board is present but not
    #: provisioned. Distinct from ``has_rnode`` so the build can flash a blank
    #: board instead of skipping it.
    rnode_present: bool = False
    #: The board carries valid RNode firmware (``--info`` reports it).
    has_rnode: bool = False
    #: Which RNode board to flash a blank attached board as (rnode_boards key).
    rnode_board_key: str = "heltec32_v4"
    #: LoRa band (MHz) to provision a blank board in.
    rnode_band_mhz: int = 915
    os_version: Optional[str] = None
    reticulum_version: Optional[str] = None
    lxmf_version: Optional[str] = None
    has_solar_controller: bool = False
    has_battery_bank: bool = False
    has_cooling_fan: bool = False
    has_rtc_module: bool = False
    has_meshtastic_bridge: bool = False
    has_meshchat_client: bool = False
    has_sideband_client: bool = False
    has_columba_client: bool = False
    has_meshtastic_client: bool = False
    session_id: str = field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    session_start: datetime = field(default_factory=datetime.now)
    operator_notes: str = ""
    build_steps_completed: List = field(default_factory=list)
    issues_found: List = field(default_factory=list)
    fixes_applied: List = field(default_factory=list)
