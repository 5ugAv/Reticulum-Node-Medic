"""Certificate viewer — opened by tapping a node in VITALS or on the SCAN map.

Re-opens a node's STORED birth certificate (ui.cert_store): its network params,
addresses, location and field notes, plus the same scannable QR the birth flow
produced, so the operator can get it off the medic later. Notes stay editable
here — add a field observation on a service visit and it saves back onto the
stored cert (and the QR refreshes to carry it).
"""

from __future__ import annotations

import re
from datetime import datetime

from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import theme
from ui.onscreen_keyboard import bind_field
from ui.qr import birth_cert_payload, qr_matrix
from ui.screens.birth_screen import QRCodeWidget
from ui.cert_store import update_notes
from monitor.geo import maps_url


def cert_latlon(cert):
    """Pull (lat, lon) out of a stored certificate. Location is saved as a string
    like "-37.790000, 144.960000 (map)"; also accept raw lat/lon keys. None if the
    cert has no usable coordinate."""
    loc = cert.get("location")
    if loc:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", str(loc))
        if m:
            try:
                lat, lon = float(m.group(1)), float(m.group(2))
            except ValueError:
                lat = lon = None
            if lat is not None and -90 <= lat <= 90 and -180 <= lon <= 180 \
                    and (lat, lon) != (0.0, 0.0):
                return (lat, lon)
    try:
        lat, lon = float(cert["lat"]), float(cert["lon"])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (lat, lon)
    except (KeyError, TypeError, ValueError):
        pass
    return None

#: Certificate keys shown first (in this order) with friendly labels; anything
#: else stored on the cert is listed after, raw. Internal keys (_id, _saved_at,
#: notes) are handled separately.
_PRETTY = [
    ("node_name", "Name"), ("type", "Type"), ("hostname", "Hostname"),
    ("reticulum_address", "Reticulum address"), ("ssh_address", "SSH"),
    ("location", "Location"), ("ssid", "SSID"), ("psk", "Wi-Fi key"),  # value MASKED at render, see _MASKED
    ("freq", "Frequency"), ("bw", "Bandwidth"), ("sf", "Spreading factor"),
    ("cr", "Coding rate"), ("txp", "TX power"), ("session_id", "Build session"),
]
_HIDDEN = {"notes"}

#: Keys whose VALUE is never drawn, only their presence. A stored Wi-Fi
#: password is a live credential; the viewer is a screen people photograph.
_MASKED = {"psk"}


def _mask(key, value):
    """The value to draw for *key* — masked if it is a credential.

    Presence still shows, so a cert that unexpectedly carries a password is
    visible as a problem without the password itself being on screen.
    """
    return "\u2022" * 8 if key in _MASKED and value not in (None, "") else value


def _line(text, color="text_primary", size="15sp", bold=False, h=24):
    # *size* is the DESIGN size; theme.font_sp maps it onto the readable type
    # scale (ui/theme.py) and theme.line_dp grows the row in the SAME edit so
    # bigger text can never be clipped by a row that stayed 24 dp tall. The rows
    # here sit in a ScrollView, so a taller row costs scroll length, not content.
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]),
                size_hint_y=None, height=dp(max(h, theme.line_dp(size))))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class CertViewScreen(BoxLayout):
    """Shows one stored certificate. ``on_saved`` (optional) is called after the
    operator edits+saves notes, so a caller can refresh any list it holds."""

    def __init__(self, cert, on_saved=None, on_show_location=None, on_triage=None,
                 on_edit_location=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        self._cert = dict(cert or {})
        self._on_saved = on_saved
        self._on_show_location = on_show_location
        self._on_triage = on_triage
        self._on_edit_location = on_edit_location
        self._latlon = cert_latlon(self._cert)

        name = self._cert.get("node_name") or self._cert.get("hostname") or "(unnamed node)"
        loc = self._cert.get("location", "")
        self.add_widget(_line(name, bold=True, size="22sp", h=34))
        if loc:
            self.add_widget(_line(loc, color="text_secondary", size="13sp", h=20))
        saved_at = self._cert.get("_saved_at")
        if saved_at:
            when = datetime.fromtimestamp(saved_at).strftime("%Y-%m-%d %H:%M")
            self.add_widget(_line(f"Born / saved on this Node Medic — {when}",
                                  color="text_secondary", size="12sp", h=18))

        body = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.list.bind(minimum_height=self.list.setter("height"))

        # Triage — aim THIS node's antenna where it's being mounted. Lives here (on
        # the node's own card, reached from VITALS/SCAN) rather than on the BIRTH
        # screen, since it's an action on an existing node, not a new one.
        if self._on_triage is not None:
            tri = Button(text="ANTENNA — aim this node", size_hint_y=None,
                         height=dp(50), bold=True, font_size=theme.font_sp("16sp"),
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["amber"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            tri.bind(on_release=lambda *_: self._on_triage(self._cert))
            self.list.add_widget(tri)

        # Location actions — See on map (opens SCAN centred on this node) and
        # Navigate (a scannable directions QR, since the medic has no phone tethered).
        if self._latlon is not None:
            act = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(48), spacing=dp(8))
            see = Button(text="See on map", bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            see.bind(on_release=lambda *_: self._see_on_map())
            nav = Button(text="Navigate", bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            nav.bind(on_release=lambda *_: self._toggle_navigate())
            act.add_widget(see)
            act.add_widget(nav)
            self.list.add_widget(act)
            # a dedicated container the directions QR toggles into (kept in the
            # layout right under the buttons, so no fragile index math)
            self.nav_panel = BoxLayout(orientation="vertical", size_hint_y=None,
                                       height=0, spacing=dp(2))
            self.list.add_widget(self.nav_panel)
            self._nav_open = False

        # Edit / set the node's location on a map (fix a wrong pin, or add one to a
        # node that has none — e.g. a node adopted over the air).
        if self._on_edit_location is not None:
            edit = Button(text=("Edit location on map" if self._latlon is not None
                                else "Set location on map"),
                          size_hint_y=None, height=dp(48), bold=True,
                          background_normal="",
                          background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                          color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            edit.bind(on_release=lambda *_: self._on_edit_location(self._cert))
            self.list.add_widget(edit)

        self.list.add_widget(_line("Birth certificate", bold=True, size="17sp"))
        shown = set()
        for key, label in _PRETTY:
            if key in self._cert and self._cert[key] not in (None, ""):
                val = _mask(key, self._cert[key])
                self.list.add_widget(_line(f"    {label}: {val}", size="13sp"))
                shown.add(key)
        # anything else the build recorded, raw (skip internals + already-shown).
        # _mask applies here TOO: a credential must not escape just because it
        # arrived under a key nobody listed in _PRETTY.
        for k, v in self._cert.items():
            if k.startswith("_") or k in _HIDDEN or k in shown or v in (None, ""):
                continue
            self.list.add_widget(_line(f"    {k}: {_mask(k, v)}", size="13sp"))

        self._qr_widgets = []
        self._add_qr()

        # Notes — editable here so a field visit's observation saves back onto the
        # stored cert (and the QR above refreshes to carry it).
        self.list.add_widget(Widget(size_hint_y=None, height=dp(8)))
        self.list.add_widget(_line("Field notes", bold=True, size="16sp", color="accent"))
        self.notes_in = TextInput(text=self._cert.get("notes", ""),
                                  hint_text="Add a note (mounting, power, access)…",
                                  multiline=True, size_hint_y=None, height=dp(96),
                                  font_size=theme.font_sp("19sp"))
        bind_field(self.notes_in)
        self.list.add_widget(self.notes_in)
        save = Button(text="Save notes", size_hint_y=None, height=dp(46), bold=True,
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        save.bind(on_release=lambda *_: self._save_notes())
        self.list.add_widget(save)
        self.status = _line("", size="12.5sp", color="green", h=20)
        self.list.add_widget(self.status)

        body.add_widget(self.list)
        self.add_widget(body)

    def _see_on_map(self):
        if self._on_show_location and self._latlon is not None:
            name = self._cert.get("node_name") or self._cert.get("hostname") or ""
            self._on_show_location(self._latlon[0], self._latlon[1], name)

    def _toggle_navigate(self):
        """Reveal / hide a directions panel under the buttons: the raw coordinate
        plus a scannable QR of a turn-by-turn maps link — the field way to navigate
        to a node from a medic that isn't tethered to a phone (scan it with any
        phone that has data)."""
        self.nav_panel.clear_widgets()
        if self._nav_open:                           # was open -> collapse
            self.nav_panel.height = 0
            self._nav_open = False
            return
        lat, lon = self._latlon
        rows = [_line(f"    Coordinates: {lat:.6f}, {lon:.6f}", size="13sp"),
                _line("    Scan for turn-by-turn directions (needs a phone with data):",
                      size="12.5sp", color="text_secondary")]
        h = dp(44)
        matrix = qr_matrix(maps_url(lat, lon))
        if matrix:
            qr = QRCodeWidget(matrix)
            holder = AnchorLayout(anchor_x="center", size_hint_y=None,
                                  height=qr.height + dp(12))
            holder.add_widget(qr)
            rows.append(holder)
            h += qr.height + dp(12)
        else:
            miss = _line("    (install 'segno' on the medic for a directions QR)",
                         size="12sp", color="text_secondary")
            rows.append(miss)
            h += dp(24)
        for w in rows:
            self.nav_panel.add_widget(w)
        self.nav_panel.height = h
        self._nav_open = True

    def _add_qr(self):
        """(Re)draw the scannable QR of the current cert, replacing any earlier one."""
        for w in self._qr_widgets:
            if w.parent:
                self.list.remove_widget(w)
        self._qr_widgets = []
        matrix = qr_matrix(birth_cert_payload(self._cert))
        if not matrix:
            w = _line("    (install 'segno' on the medic for a scannable QR)",
                      color="text_secondary", size="12sp")
            self.list.add_widget(w)
            self._qr_widgets = [w]
            return
        lbl = _line("Scan to save this certificate:", bold=True, size="15sp")
        self.list.add_widget(lbl)
        qr = QRCodeWidget(matrix)
        holder = AnchorLayout(anchor_x="center", size_hint_y=None,
                              height=qr.height + dp(12))
        holder.add_widget(qr)
        self.list.add_widget(holder)
        self._qr_widgets = [lbl, holder]

    def _save_notes(self):
        notes = self.notes_in.text.strip()
        self._cert["notes"] = notes
        cid = self._cert.get("_id")
        ok = update_notes(cid, notes) if cid else False
        self.status.text = ("Saved — the QR now includes the notes."
                            if ok else "Saved to view (couldn't write the file).")
        self._add_qr()
        if self._on_saved:
            self._on_saved(self._cert)
