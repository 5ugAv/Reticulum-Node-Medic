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
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from monitor.time_service import is_pi_node
from ui.clock_line import clock_line
from ui.i18n import tr  # i18n: wrapped — node detail section headers/labels/buttons
from ui.widgets.hex_status import HexStatus
from monitor.formatting import beacon_lines, format_age, seen_and_echo


def _para(text, color="text_secondary", size="13sp"):
    """A label that GROWS with its text — for sentences that wrap. A one-line
    _line box clipped the "card drawn …" note and the clock line to their
    first line on the glass (operator photo, 2026-09-24 01:2x)."""
    lbl = Label(text=text, halign="left", valign="top",
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]), size_hint_y=None)
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
             texture_size=lambda i, ts: setattr(i, "height", ts[1]))
    return lbl


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


def _reading(record, name):
    """A live reading from a NodeRecord OR from the dashboard dict of one.

    THE MEDIC'S SCREEN WENT BLACK OVER THIS (2026-08-11). This screen was
    written against the shape ``NodeRecord.to_dashboard()`` produces — flat keys
    like ``signal_dbm`` and ``battery_pct`` — and then wired to receive the
    RECORD itself. On a record, ``signal_dbm`` is a METHOD, so
    ``getattr(record, "signal_dbm", None)`` handed back a bound method, and the
    next line compared it to a number:

        TypeError: '>' not supported between instances of 'method' and 'int'

    The operator tapped a node in VITALS and the app died. It does not restart
    itself; the screen simply stays black, with nothing on it to say why.

    The battery reading was the same bug wearing a quieter coat: ``battery_pct``
    is not on the record at all (it is ``_battery_pct()``), so every node has
    been reporting "Battery: not reported" since the line was written —
    including nodes that were sending their charge perfectly well.

    So: dict key, then attribute, then the private accessor — and CALL it if it
    is callable, because the difference between a number and a method that
    returns one is the difference between a screen and a black rectangle.
    """
    if isinstance(record, dict):
        return record.get(name)
    value = getattr(record, name, None)
    if value is None:
        value = getattr(record, "_" + name, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    return value


#: status word -> colour name, shared with the VITALS row via ui.theme (#60)
_TONE = {"ok": "green", "warn": "amber", "alert": "red", "unknown": "text_secondary"}

class NodeDetailScreen(BoxLayout):
    def __init__(self, record, now, on_poll=None,
                 on_forget=None, on_walk=None, on_probe=None,
                 watch_line=None, activity_text=None, by_hour=None,
                 insights=None, on_rebirth=None, board_attached=False,
                 capabilities=None, clock_entry=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        self.record = record
        self._on_poll = on_poll
        self._on_forget = on_forget
        self._on_walk = on_walk
        self._on_probe = on_probe
        # A rebirth is an esptool erase over USB, so it needs the board IN HAND.
        # board_attached defaults False on purpose: a caller that cannot tell
        # must not have a repair button appear that quietly does nothing.
        self._on_rebirth = on_rebirth
        self._board_attached = bool(board_attached)

        # header: hex status + name + location
        head = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=dp(56), spacing=dp(10))
        # ONE source for the page and its VITALS row — the dashboard view,
        # where a stranger's row is grey and its announced name stands in for
        # a hash (readiness ledger #59).
        try:
            dash = record.to_dashboard(now)
        except Exception:                                          # noqa: BLE001
            dash = {"status": record.status(now),
                    "name": record.name or record.dst_hash[:12]}
        self._dash = dash
        head.add_widget(HexStatus(status=dash["status"],
                                  size_hint_x=None, width=dp(48)))
        title = BoxLayout(orientation="vertical")
        title.add_widget(_line(dash["name"] or record.dst_hash[:12], bold=True,
                               size="20sp"))
        # A stranger is not an RTNode: node_type defaults to "rtnode2400" for
        # every record, and the operator's own phone wore it (2026-10-02).
        # words, not the type code ('rtnode2400', 'pi_propagation') — #63
        kinds = {"rtnode2400": tr("RTNode-2400"), "pi_propagation": tr("Raspberry Pi propagation node"),
                 "rnode": tr("RNode radio")}
        kind = (kinds.get(record.node_type, record.node_type)
                if record.provenance != "neighbour"
                else tr("heard announcing — device unknown"))
        title.add_widget(_line(record.location or kind,
                               color="text_secondary", size="13sp"))
        head.add_widget(title)
        self.add_widget(head)

        seen = record.last_seen_hours(now)
        # minutes under the hour: "0.0h ago" on a node heard two minutes ago
        # read as a clock that had stopped (glass, 2026-10-05)
        from monitor.formatting import format_age_fine
        when = (tr("never") if seen is None
                else tr("{age} ago").format(age=format_age_fine(seen)))
        # TWO ROADS, TWO NUMBERS (2026-09-24): when the freshest sighting came
        # over Wi-Fi, say so, and say when the mesh last heard it — "0.0h ago"
        # alone hid a roof node whose mesh ping had just gone unanswered.
        src = getattr(getattr(record, "seen", None), "source", None)
        mesh = record.mesh_seen_hours(now) if hasattr(record, "mesh_seen_hours") else None
        if src == "http" and mesh is None:
            text = tr("Last heard: {when} over Wi-Fi · never over the mesh").format(when=when)
        elif src == "http" and seen is not None and mesh - seen > 0.05:
            text = tr("Last heard: {when} over Wi-Fi · over the mesh {mesh} ago").format(
                when=when, mesh=format_age_fine(mesh))
        else:
            text = tr("Last heard: {when}").format(when=when)
        self.add_widget(_para(text, color="text_secondary", size="15sp"))
        # Same muted annotation as the VITALS row, one line, from the SAME
        # composer (formatting.seen_and_echo), which enforces the rules in one
        # tested place: shown only while the replay is FRESHER than the node's
        # last DIRECT word. Spelled out here because this screen has the room:
        # an echo is the mesh repeating the node's last announce, not the node
        # — the very evidence that kept a powered-off board green on
        # 2026-08-21. Untranslated like the beacon_lines figures above it; it
        # never touches the hexagon, drawn from record.status(now) before this.
        _, echo_tag = seen_and_echo({
            "last_seen_hours": seen,
            "last_echo_hours": record.last_echo_hours(now),
            "last_direct_hours": record.last_direct_hours(now),
        })
        if echo_tag is not None:
            # _para, not _line: two lines of text in a one-line box lost
            # "the node speaking" off the bottom (operator photo, 2026-10-02).
            self.add_widget(_para(
                f"· {echo_tag} — a relay repeating its last announce, "
                "not the node speaking", color="text_secondary", size="12.5sp"))
        # THE SNAPSHOT IS STAMPED (briefing Task 10): a powered-off node
        # showed uptime 28s and WiFi up as if live — these figures are the
        # node's LAST REPORT, rendered at a moment in time, and the card says
        # both. Amber when the node is not currently answering, so stale
        # cannot dress as live.
        import time as _t
        # ANSWERING is about freshness, not health: a node heard two minutes
        # ago with a low battery is answering, unhappily — the page said "NOT
        # answering" six lines above "is answering, but not happily" (2026-10-03)
        _seen_h = record.last_seen_hours(now)
        answering = _seen_h is not None and _seen_h <= theme.NOT_HEARD_ALERT_HOURS
        self.add_widget(_para(
            tr("Figures below are the node's last report — card drawn "
               "{clock}.").format(clock=_t.strftime("%H:%M")) +
            ("" if answering else tr(" The node is NOT answering right now.")),
            color=("text_secondary" if answering else "amber"), size="12.5sp"))

        batt = _reading(record, "battery_pct")
        self.add_widget(_line(
            tr("Battery: {status}").format(
                status=f"{batt}%" if batt is not None else tr("not reported")),
            color=("text_secondary" if batt is None else
                   _TONE[theme.battery_status(batt)])))      # the row's scale (#60)

        # SIGNAL, and WHEN it was measured. A dBm figure with no timestamp is a
        # trap on a node that has since moved, gone quiet or lost its antenna —
        # it reads as current and is not (operator asked for transmission
        # strength on this screen, 2026-08-10).
        sig = _reading(record, "signal_dbm")
        if sig is not None:
            self.add_widget(_line(
                tr("Wi-Fi signal when last heard: {dbm} dBm").format(dbm=sig),
                color=_TONE[theme.signal_status(sig)]))     # the row's scale (#60)
        else:
            self.add_widget(_line(tr("Signal: not measured"),
                                  color="text_secondary"))

        if is_pi_node(record.node_type):
            # THE NODE'S CLOCK (docs/HEALTH_REPLY_UNICAST.md, "Time over the
            # mesh", 2026-09-23): a Pi node with no RTC boots with no time;
            # the medic feeds it one over LoRa. One line, from the medic's
            # ledger, and only what the node ACKED — a TIME sent and never
            # answered reads "not yet confirmed". Pi nodes only: an RTNode
            # has no OS clock to keep. The registry's Pi type is
            # "pi_propagation" (kin_roster.type_for_cert) — `== "pi"` never
            # matched a real record (review, 2026-09-23).
            self.add_widget(_para(clock_line(clock_entry), color="text_secondary",
                                  size="13sp"))

        # SELF-REPORTED POSITION (v3 beacon, 2026-08-27): the node's OWN live
        # GNSS claim, distinct from the birth-certificate stamp. Shown only
        # when the node actually said it — a GPS-fitted node hunting sky says
        # nothing, and nothing is shown. A fuzzed position is announced as
        # deliberately imprecise, never dressed as exact.
        b = getattr(record, "latest_beacon", None)
        if b is not None and getattr(b, "has_position", False):
            pos = "%.6f, %.6f" % (b.lat, b.lng)
            extra = []
            if b.position_sats:
                extra.append(tr("{n} satellites").format(n=b.position_sats))
            if b.position_fuzzed:
                extra.append(tr("deliberately imprecise"))
            tail = ("  ·  " + " · ".join(extra)) if extra else ""
            self.add_widget(_line(
                tr("Position (node's own GPS): {pos}").format(pos=pos) + tail,
                color="green" if not b.position_fuzzed else "text_secondary"))

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
                why = (caps.get("why") or {}).get(key)
                word = (tr("working") if st is True
                        else tr("not answering the medic's probe — nothing heard "
                                "over the mesh since") if st is False and why == "probe_unanswered"
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
        # DATE THE REPORT. "LoRa: up" below is what the node said in its last
        # health report; twelve days later it sat under a Connections line
        # saying the radio was down, two ages shown as one truth (#222).
        _age_h = record.last_seen_hours(now)
        if _age_h is not None and record.latest_beacon is not None:
            from monitor.formatting import format_duration
            col.add_widget(_line(
                "  " + tr("As the node last reported it, {ago} ago:").format(
                    ago=format_duration(int(_age_h * 3600))),
                size="13sp", color="text_secondary"))
        for ln in beacon_lines(record):
            col.add_widget(_line("  " + ln, size="14sp"))

        # What this MEDIC knows it built — from the birth certificate, not
        # the node's own beacon. An RNode-firmware node reports nothing about
        # itself, so without this its page had no board at all; an RTNode's
        # beacon names the board but not that it is an RTNode (operator,
        # 2026-09-21: "we know what board this is").
        birth = self._birth_lines(record)
        if birth:
            # an adopted node was never built here (readiness ledger #61)
            if getattr(self, "_cert_adopted", False):
                header = (tr("Adopted by this medic (over the air)")
                          if getattr(self, "_cert_ota", False)
                          else tr("Adopted by this medic"))
            else:
                header = tr("Built by this medic")
            col.add_widget(_line(header, bold=True, size="17sp"))
            for ln in birth:
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
        adv = advise(self._dash["status"], board_attached=self._board_attached,
                     name=record.name or "", hours_quiet=None,
                     # The SAME evidence the header above uses for "Last
                     # heard" — or the advice contradicts the page it sits on.
                     heard_ever=record.last_seen_hours(now) is not None)
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
        self._show_late_reply()

        actions = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(52), spacing=dp(8))
        ping = Button(text=tr("Ping node now"), font_size=theme.font_sp("18sp"),
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        ping.bind(on_release=lambda *_: self._ping())
        actions.add_widget(ping)
        # ONE LOCATION BUTTON, not two (operator, 2026-09-28). "Location &
        # map" and "Navigate" read as the same question asked twice, and the
        # operator's reasoning for which to keep is the right one: Navigate
        # never navigated. It put MAPS on the node's recorded pin — no route,
        # no directions — and the location panel already opens a map with that
        # pin on it AND lets you move it. The poorer of the two is gone.
        #
        # The panel is also the only way to give a deployed node a position or
        # to stop it publishing one; a rebirth to withdraw a position would
        # mean climbing to a roof.
        loc = Button(text=tr("Location"), font_size=theme.font_sp("18sp"),
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        loc.bind(on_release=lambda *_: self._map_sharing())
        actions.add_widget(loc)
        if self._on_walk is not None:
            # BOUNDARY WALK (spec 2026-08-13, built 2026-09-15): walk away
            # from this node with the medic; MAPS pings it every 20 s and
            # flashes at the found boundary. Its own row — the actions row
            # above once squeezed two buttons into "mapelete" (2026-08-14).
            walk_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                 height=dp(52), spacing=dp(8))
            walk = Button(text=tr("Range test — how far this node reaches"),
                          font_size=theme.font_sp("16sp"), bold=True,
                          background_normal="",
                          background_color=theme.hex_to_rgba(
                              theme.COLORS["accent"]),
                          color=theme.hex_to_rgba(theme.COLORS["background"]))
            walk.bind(on_release=lambda *_: self._on_walk(self.record))
            walk_row.add_widget(walk)
            note = self._last_walk_note()
            if note:
                # The last walk's verdict lives here, not only in a popup
                # that vanished (2026-09-21): the row grows to hold it.
                col = BoxLayout(orientation="vertical", size_hint_y=None,
                                height=dp(52) + dp(24), spacing=dp(2))
                col.add_widget(walk_row)
                # HEIGHT FOLLOWS THE TEXT (seen on the glass 2026-10-05: pinned
                # at 22 dp a two-line verdict showed only its second line,
                # "answer -104 dBm"). The column grows with it.
                lbl = Label(text=note, font_size=theme.font_sp("13sp"),
                            size_hint_y=None, halign="left", valign="top",
                            color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
                lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                         texture_size=lambda i, ts, c=col: (
                             setattr(i, "height", ts[1] + dp(4)),
                             setattr(c, "height", dp(52) + dp(2) + ts[1] + dp(4))))
                col.add_widget(lbl)
                walk_row = col
            self._walk_row = walk_row

        if self._on_probe is not None and not str(
                getattr(self.record, "node_type", "") or "").startswith("pi"):
            # PROBE'S DOOR (readiness ledger #144, keeper's call 2026-10-05).
            # docs/FRONT_PAGE_BRIEF.md says PROBE is reached by tapping a node
            # in VITALS — and until now this page had no such button, so after
            # the tour PROBE had no door at all. A Pi node gets none: PROBE
            # reads a BOARD over USB, and a Pi node is reached over the network.
            probe_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                  height=dp(52), spacing=dp(8))
            probe = Button(text=tr("Probe this node — plug it into the medic"),
                           font_size=theme.font_sp("16sp"), bold=True,
                           background_normal="",
                           background_color=theme.hex_to_rgba(
                               theme.COLORS["accent"]),
                           color=theme.hex_to_rgba(theme.COLORS["background"]))
            probe.bind(on_release=lambda *_: self._on_probe(self.record))
            probe_row.add_widget(probe)
            self._probe_row = probe_row

        # NO HEALTH-REPORTER UPDATE HERE AT ALL (operator, 2026-09-29). It was
        # a button; then, briefly, an automatic push. Both are gone, and the
        # reason is that the problem they solved has no future: "any boards that
        # were birthed before the health updater will be reflashed... and
        # anybody who uses the Node Medic once it's been released won't have to
        # worry about that issue either." Birth installs the current reporter,
        # so every node this medic will ever meet already has it. Carrying a
        # migration for a population of zero is a page that does work nobody
        # asked for, over SSH, on a screen opened to read a battery level.
        #
        # workflows/pi_reporter_push.py stays — it is exercised by the birth
        # time-trust tests and it is the proven repair if a reporter is ever
        # found stale — it is simply not wired to any screen.

        if self._on_forget is not None:
            # DELETE, behind the danger confirm (operator request, 2026-08-13)
            # — and on its OWN ROW since 2026-08-14 (briefing Task 11): packed
            # beside "Location & map" at 1280x720 the two overlapped into
            # "mapelete", and a destructive button flush against a routine one
            # is a mis-tap waiting for a big finger. Red, worded as what it
            # destroys — the medic's whole memory of the node, not the node.
            danger_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                   height=dp(52), spacing=dp(8),
                                   padding=[0, dp(10), 0, 0])
            forget = Button(text=tr("Delete this node…"),
                            font_size=theme.font_sp("18sp"),
                            background_normal="",
                            background_color=theme.hex_to_rgba(
                                theme.COLORS["red"]),
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
            forget.bind(on_release=lambda *_: self._confirm_forget())
            danger_row.add_widget(Widget())          # pushed right, half-width
            danger_row.add_widget(forget)
            self._danger_row = danger_row
        # Only when the board is REALLY here and there is somewhere to send it.
        # A rebirth is a USB erase; a button for a node across town would
        # be a repair path that looks one tap from working and isn't.
        if (self._advice is not None and self._advice.offer_rebirth
                and self._on_rebirth is not None):
            rb = Button(text=tr("Rebuild this node"),
                        font_size=theme.font_sp("18sp"), background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
            rb.bind(on_release=lambda *_: self._rebirth())
            actions.add_widget(rb)
        self.add_widget(actions)
        if getattr(self, "_walk_row", None) is not None:
            self.add_widget(self._walk_row)
            self._walk_row = None
        if getattr(self, "_probe_row", None) is not None:
            self.add_widget(self._probe_row)
            self._probe_row = None
        if getattr(self, "_danger_row", None) is not None:
            self.add_widget(self._danger_row)
            self._danger_row = None

    def _ping(self):
        if self._on_poll:
            self.ping_status.text = tr("Probing over the mesh…")
            self.ping_status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
            self._on_poll(self.record.dst_hash, self._set_ping_status)

    def _show_late_reply(self):
        """A unicast health reply that landed after its poll's popup closed
        (docs/HEALTH_REPLY_UNICAST.md): the record kept the sentence."""
        note = getattr(self.record, "late_reply_note", None)
        if note:
            self._set_ping_status(note, True)

    def _set_ping_status(self, text, ok=None):
        self.ping_status.text = text
        color = "green" if ok is True else "amber" if ok is False else "text_secondary"
        self.ping_status.color = theme.hex_to_rgba(theme.COLORS[color])

    def _birth_lines(self, record):
        """Board, firmware role and birth date from the certificate, or []."""
        try:
            from ui.cert_store import load_certs, cert_for_node
            from provisioning.board_coverage import classify_cert
            cert = cert_for_node(load_certs(),
                                 dst_hash=getattr(record, "dst_hash", "") or "",
                                 name=getattr(record, "name", "") or "")
        except Exception:                                          # noqa: BLE001
            return []
        if not cert:
            return []
        self._cert_adopted = bool(cert.get("adopted"))
        self._cert_ota = bool(cert.get("over_the_air"))
        fact = classify_cert(cert)
        out = []
        # THE BOARD, from the certificate's codes — never its English prose
        # inside a translated frame (2026-09-22). A certificate written
        # since then carries radio_rule; with no board_key it means the
        # board was never named. Older certificates only have the sentence.
        new_style = "radio_rule" in cert
        if cert.get("board_key") or not new_style:
            if fact.board:
                out.append(tr("Board: {board}").format(board=fact.board))
            if cert.get("board_source") == "operator":
                out.append(tr("Board named by you from the catalogue — the "
                              "radio was never on Node Medic"))
        else:
            out.append(tr("Board: not named when it was built — the radio was "
                          "never on Node Medic"))
        kinds = {"rnode": tr("RNode"), "rtnode2400": tr("RTNode-2400"),
                 "pi_rnode": tr("Raspberry Pi + RNode")}
        kind = fact.name or kinds.get(fact.kind)   # the build's own name wins
        if kind and fact.firmware:
            out.append(tr("Firmware: {kind} {version}").format(
                kind=kind, version=fact.firmware))
        elif kind:
            out.append(tr("Firmware: {kind}").format(kind=kind))
        elif fact.firmware:
            # A version-blind board with an older certificate: 1.85 is what
            # both RNode and RTNode report there, and the cert did not say.
            out.append(tr("Firmware: {version} — RNode or RTNode, the "
                          "certificate does not say").format(
                              version=fact.firmware))
        if fact.born:
            out.append((tr("Adopted: {born}") if cert.get("adopted")
                        else tr("Built: {born}")).format(born=fact.born))
        # THE PORT, composed from install_radio_rule's own record of the
        # udev rule it wrote and read back (from 2026-09-22). An older
        # certificate's bare "/dev/ttyUSB0" is the NodeProfile default, a
        # port nothing ever opened — not shown, not a fact.
        rule = cert.get("radio_rule") or {}
        if rule.get("by") == "serial" and rule.get("serial"):
            source = {
                "node": tr("read from the radio on the node"),
                "flash": tr("read by Node Medic when it flashed the radio"),
                "medic_usb": tr("read by Node Medic from the radio on its "
                                "own USB"),
            }.get(str(rule.get("source") or ""), str(rule.get("source") or ""))
            out.append(tr("Radio found by its serial number "
                          "{serial} ({source})").format(
                              serial=rule["serial"], source=source))
        elif rule.get("by") == "vendor":
            out.append(tr("Radio found by its USB maker — no serial number "
                          "was read"))
        return out

    def _last_walk_note(self):
        """One line from the newest boundary walk against this node."""
        try:
            from monitor.walk_diagnostics import latest_diagnosis
            d = latest_diagnosis(self.record.dst_hash or "",
                                 name=self.record.name or "")
        except Exception:                                          # noqa: BLE001
            return ""
        if not d:
            return ""
        words = {"cliff": tr("cliff — something in the way"),
                 "slope": tr("slope — out of budget"),
                 "open": tr("no edge found"),
                 "no_signal": tr("no signal readings")}
        last = d.get("last") or {}
        if last:
            return tr("Last walk: {verdict} at {m} m, last answer {rssi} dBm").format(
                verdict=words.get(d.get("verdict"), d.get("verdict", "")),
                m=last.get("m", 0), rssi=int(round(last.get("rssi_dbm") or 0)))
        return tr("Last walk: {verdict}").format(
            verdict=words.get(d.get("verdict"), d.get("verdict", "")))

    def _confirm_forget(self):
        """The medic forgets a node only past the red confirm. What is deleted
        is the MEDIC'S record — rows, history, certificates, roster — never
        anything on the node itself; the name becomes free for a new birth."""
        from ui.confirm import confirm_danger
        name = (getattr(self, "_dash", {}).get("name")
                or self.record.name or self.record.dst_hash[:8])
        confirm_danger(
            tr("Delete everything Node Medic knows about {name}?\n\n"
               "Its rows, history, certificates and roster entry all go — "
               "this cannot be undone, and the name becomes free for a new "
               "node. The node itself is not touched; if it is still alive "
               "and announcing, it will reappear as a neighbour.").format(
                   name=name),
            tr("Delete {name}").format(name=name),
            lambda: self._on_forget(self.record),
            proceed_text=tr("Delete it all"))

    def _map_sharing(self):
        """Open the location / map-sharing panel for this node.

        Guarded: a missing widget module must not take down the page an
        operator opened to read a battery level."""
        try:
            from ui.widgets.map_sharing import MapSharingPopup
            MapSharingPopup(self.record).open()
        except Exception as exc:                                   # noqa: BLE001
            self._set_ping_status(
                tr("Couldn't open location settings: {why}").format(why=exc),
                False)

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
