"""Node detail screen — opened by tapping a node in Monitor.

Shows the node's live health (from its latest decoded beacon), its field notes
and commissioning log, and a "Ping node now" action that triggers an on-demand
poll and clears a red/orange warning to green on a clean reply.
"""

from __future__ import annotations

from datetime import datetime

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import tr  # i18n: wrapped — node detail section headers/labels/buttons
from ui.widgets.hex_status import HexStatus
from monitor.formatting import beacon_lines, format_age


def _line(text, color="text_primary", size="15sp", bold=False):
    # *size* is the DESIGN size; theme.font_sp maps it onto the readable type
    # scale (ui/theme.py). The row height has to follow it in the SAME edit: at
    # the old flat dp(24) the 20sp title already needed exactly 24 dp, so any
    # enlargement would have clipped it.
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]),
                size_hint_y=None, height=dp(max(24, theme.line_dp(size))))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


def _wrap(text, color="text_primary", size="14sp"):
    """A left-aligned label that wraps and grows to fit — for the longer activity /
    insight sentences inside the scrolling detail column."""
    lbl = Label(text=text, halign="left", valign="top", font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]), size_hint_y=None)
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    lbl.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1]))
    return lbl


class NodeDetailScreen(BoxLayout):
    def __init__(self, record, now, on_poll=None, on_navigate=None,
                 watch_line=None, activity_text=None, by_hour=None,
                 insights=None, on_rebirth=None, board_attached=False,
                 capabilities=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        self.record = record
        self._on_poll = on_poll
        self._on_navigate = on_navigate
        # A rebirth is an esptool erase over USB, so it needs the board IN HAND.
        # board_attached defaults False on purpose: a caller that cannot tell
        # must not have a repair button appear that quietly does nothing.
        self._on_rebirth = on_rebirth
        self._board_attached = bool(board_attached)

        # header: hex status + name + location
        head = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=dp(56), spacing=dp(10))
        head.add_widget(HexStatus(status=record.status(now),
                                  size_hint_x=None, width=dp(48)))
        title = BoxLayout(orientation="vertical")
        title.add_widget(_line(record.name or record.dst_hash[:12], bold=True,
                               size="20sp"))
        title.add_widget(_line(record.location or record.node_type,
                               color="text_secondary", size="13sp"))
        head.add_widget(title)
        self.add_widget(head)

        seen = record.last_seen_hours(now)
        self.add_widget(_line(
            tr("Last heard: {when}").format(
                when=tr("never") if seen is None
                else tr("{age} ago").format(age=format_age(seen))),
            color="text_secondary"))

        batt = getattr(record, "battery_pct", None)
        self.add_widget(_line(
            tr("Battery: {status}").format(
                status=f"{batt}%" if batt is not None else tr("not reported")),
            color=("text_secondary" if batt is None else
                   "green" if batt > 50 else "amber" if batt > 20 else "red")))

        # SIGNAL, and WHEN it was measured. A dBm figure with no timestamp is a
        # trap on a node that has since moved, gone quiet or lost its antenna —
        # it reads as current and is not (operator asked for transmission
        # strength on this screen, 2026-08-10).
        sig = getattr(record, "signal_dbm", None)
        if sig is not None:
            self.add_widget(_line(
                tr("Signal when last heard: {dbm} dBm").format(dbm=sig),
                color=("green" if sig > -90 else "amber" if sig > -110 else "red")))
        else:
            self.add_widget(_line(tr("Signal: not measured"),
                                  color="text_secondary"))

        # CONNECTIONS, IN WORDS, AND ONLY WHAT THE NODE SAID.
        #
        # The chips in VITALS are a glance; this is the place that has room to
        # be explicit. The three states are kept apart on purpose — "not
        # reported" is not a quiet way of saying "off", it means the node has
        # never mentioned it and the medic is not going to guess from the
        # board's datasheet. That guess is exactly what showed Bluetooth on a
        # node whose adapter was switched off (SolarLove, 2026-08-10), and the
        # rule that came out of it: nothing is stated unless it is true, and
        # what is true is what the node said.
        # PASSED IN, not read off the record: the registry is what knows what
        # the node has said, and it is the only thing entitled to an opinion.
        caps = capabilities
        if isinstance(caps, dict):
            self.add_widget(_line(tr("Connections"), bold=True, size="17sp"))
            for key, label in (("lora", tr("LoRa radio")), ("wifi", tr("Wi-Fi")),
                               ("bluetooth", tr("Bluetooth")),
                               ("internet", tr("Internet"))):
                st = caps.get(key)
                word = (tr("working") if st is True
                        else tr("down — the node says so") if st is False
                        else tr("not reported by the node"))
                self.add_widget(_line(
                    "  " + label + ": " + word, size="14sp",
                    color=("green" if st is True
                           else "amber" if st is False else "text_secondary")))

        if watch_line:
            wl = Label(text=watch_line, halign="left", valign="top",
                       font_size=theme.font_sp("13sp"), size_hint_y=None,
                       color=theme.hex_to_rgba(theme.COLORS["amber"]))
            wl.bind(size=lambda i, v: setattr(i, "text_size", v))
            wl.bind(texture_size=lambda i, v: setattr(i, "height", v[1]))
            self.add_widget(wl)

        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        col.bind(minimum_height=col.setter("height"))

        col.add_widget(_line(tr("Health"), bold=True, size="17sp"))
        for ln in beacon_lines(record):
            col.add_widget(_line("  " + ln, size="14sp"))

        if activity_text:
            col.add_widget(_line(tr("Activity"), bold=True, size="17sp"))
            col.add_widget(_wrap("  " + activity_text, color="text_secondary"))
        if by_hour and any(by_hour):
            from ui.widgets.activity_chart import ActivityChart
            col.add_widget(ActivityChart(by_hour=by_hour))
            col.add_widget(_line("  " + tr("midnight · 6am · noon · 6pm   (times heard, local)"),
                                 size="11sp", color="text_secondary"))
        if insights:
            col.add_widget(_line(tr("Noticed"), bold=True, size="17sp"))
            for fl in insights:
                sev = fl.get("severity")
                col.add_widget(_wrap(
                    "  ! " + fl.get("text", ""), size="13.5sp",
                    color="red" if sev == "alert" else
                    "amber" if sev == "warn" else "text_primary"))

        # WHAT TO DO ABOUT IT. An operator arrives here because a dot went red,
        # and until now the page described the problem without once suggesting
        # a repair (operator, 2026-07-31). The cheaper checks come first — a
        # rebirth destroys the node's identity, and a solar node waiting for sun
        # is not a node that needs wiping.
        from ui.rebirth_advice import advise
        adv = advise(record.status(now), board_attached=self._board_attached,
                     name=record.name or "", hours_quiet=None)
        if adv is not None:
            col.add_widget(_line(tr("What to try"), bold=True, size="17sp"))
            col.add_widget(_wrap("  " + adv.headline, color="amber"))
            for i, s in enumerate(adv.steps, 1):
                col.add_widget(_wrap(f"  {i}. {s}", size="13.5sp"))
            if adv.rebirth_note:
                col.add_widget(_wrap("  " + adv.rebirth_note,
                                     color="text_secondary", size="13sp"))
        self._advice = adv

        nav = record.navigation()
        if nav:
            col.add_widget(_line(tr("Location (exact — repair visit)"), bold=True,
                                 size="17sp"))
            col.add_widget(_line("  " + nav["raw"], size="14sp"))
            col.add_widget(_line("  " + nav["google"], color="accent",
                                 size="12sp"))

        notes = getattr(record, "notes", None)
        if notes:
            col.add_widget(_line(tr("Field notes"), bold=True, size="17sp"))
            for note in notes:
                col.add_widget(_line("  • " + note, size="14sp"))

        events = getattr(record, "events", None)
        if events:
            col.add_widget(_line(tr("Commissioning log"), bold=True, size="17sp"))
            for ev in events:
                stamp = datetime.fromtimestamp(ev.at).strftime("%Y-%m-%d %H:%M")
                col.add_widget(_line(
                    f"  {stamp}  [{ev.kind}] {ev.summary} — {ev.operator}",
                    color="text_secondary", size="13sp"))

        body.add_widget(col)
        self.add_widget(body)

        # HEIGHT FOLLOWS THE TEXT. Pinned to one line, the two-line answers ran
        # straight under the button row below and could only be half read —
        # "Not answering right now — it may be down or out of range. The medic
        # keeps watching it" lost its second line behind "Ping node now"
        # (operator, 2026-08-10, on the first node ever birthed). This is the
        # third label in the project to be pinned at a fixed height and then
        # overflow; the rule is that any label carrying a SENTENCE sizes itself.
        self.ping_status = Label(text="", halign="left", valign="top",
                                 font_size=theme.font_sp("13sp"), size_hint_y=None,
                                 height=dp(max(26, theme.line_dp("13sp"))),
                                 color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        self.ping_status.bind(
            width=lambda i, w: setattr(i, "text_size", (w, None)),
            texture_size=lambda i, ts: setattr(
                i, "height", max(dp(max(26, theme.line_dp("13sp"))), ts[1] + dp(4))))
        self.add_widget(self.ping_status)

        actions = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(52), spacing=dp(8))
        ping = Button(text=tr("Ping node now"), font_size=theme.font_sp("18sp"),
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        ping.bind(on_release=lambda *_: self._ping())
        actions.add_widget(ping)
        if record.has_location():
            nav_btn = Button(text=tr("Navigate"), font_size=theme.font_sp("18sp"),
                             background_normal="",
                             background_color=theme.hex_to_rgba(
                                 theme.COLORS["green"]),
                             color=theme.hex_to_rgba(theme.COLORS["background"]))
            nav_btn.bind(on_release=lambda *_: self._navigate())
            actions.add_widget(nav_btn)
        # Only when the board is REALLY here and there is somewhere to send it.
        # A rebirth is a USB erase; a button for a node across town would
        # be a repair path that looks one tap from working and isn't.
        if (self._advice is not None and self._advice.offer_rebirth
                and self._on_rebirth is not None):
            rb = Button(text=tr("Rebirth this node"),
                        font_size=theme.font_sp("18sp"), background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
            rb.bind(on_release=lambda *_: self._rebirth())
            actions.add_widget(rb)
        self.add_widget(actions)

    def _ping(self):
        if self._on_poll:
            self.ping_status.text = tr("Probing over the mesh…")
            self.ping_status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
            self._on_poll(self.record.dst_hash, self._set_ping_status)

    def _set_ping_status(self, text, ok=None):
        self.ping_status.text = text
        color = "green" if ok is True else "amber" if ok is False else "text_secondary"
        self.ping_status.color = theme.hex_to_rgba(theme.COLORS[color])

    def _navigate(self):
        if self._on_navigate:
            self._on_navigate(self.record)

    def _rebirth(self):
        """Hand off to the existing wipe-and-rebuild flow (a805c43).

        Deliberately NOT destructive here — this only navigates. The erase sits
        behind that flow's own confirm popup, which names the node it is about
        to destroy. Two confirms for one irreversible act is right; putting the
        erase on this button would put it one tap from a page an operator opens
        just to read a battery level.
        """
        if self._on_rebirth:
            self._on_rebirth(self.record)
