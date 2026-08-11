"""Location & map sharing for a node that already exists.

Reached from a node's own detail page. Two jobs, on one panel because they are
one question in the operator's head:

  * WHERE this node is — set or corrected on the existing confirm-location map
    (tap the pin, or type a landmark like a train station). This is how a node
    that was deployed before anybody thought about coordinates gets one, and
    how EVERYWHERE is told it stands at a public landmark rather than anywhere
    near a person.
  * WHETHER it says so — hidden or roughly-shared, changeable at any time,
    without rebirthing a node that may be on a roof.

WHAT THIS PANEL WILL NOT DO IS LIE ABOUT THE STATE OF THE WORLD. Pressing a
button here changes Node Medic's records. It does not change the node until the
medic reaches it and rewrites its Reticulum config, and it can never confirm
that a public map has drawn the pin. The status line says which of those has
actually happened (monitor.location_share.status_line), and "Apply to the node"
reports what the node itself said afterwards, not what was sent to it.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from monitor import location_share
from ui import theme
from ui.i18n import tr


def _lbl(text, size="14sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="top",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    return lbl


def _btn(text, bg, fg, on_press):
    b = Button(text=text, font_size=theme.font_sp("17sp"), bold=True,
               size_hint_y=None, height=dp(48), background_normal="",
               background_color=theme.hex_to_rgba(theme.COLORS[bg]),
               color=theme.hex_to_rgba(theme.COLORS[fg]))
    b.bind(on_release=lambda *_: on_press())
    return b


class MapSharingPopup(Popup):
    """``record`` is a :class:`monitor.registry.NodeRecord`.

    *registry* and *save* are injected so the panel can be driven in a test
    without a running app; by default it finds the app's live registry.
    """

    def __init__(self, record, registry=None, save=None, push=None, **kwargs):
        self.record = record
        self._registry = registry if registry is not None else self._app_registry()
        self._save = save
        self._push = push or location_share.push_to_node
        self._busy = False

        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12))
        self._status = _lbl("", "14sp", "text_secondary")
        self._what = _lbl("", "13sp", "text_secondary")

        body.add_widget(_lbl(tr("Where this node is"), "16sp", "text_primary",
                             bold=True, h=24))
        self._where = _lbl("", "14sp", "text_secondary", h=44)
        body.add_widget(self._where)
        body.add_widget(_btn(tr("Set location on the map…"), "surface",
                             "text_primary", self._pick_location))

        body.add_widget(_lbl(tr("The public map"), "16sp", "text_primary",
                             bold=True, h=24))
        body.add_widget(self._status)
        body.add_widget(self._what)
        # THE IRREVERSIBLE PART, stated on the panel and not only at birth. An
        # operator turning sharing off here is entitled to know that it stops
        # the next announce and unsays none of the last ones.
        body.add_widget(_lbl(location_share.cannot_be_recalled(), "13sp",
                             "warning_yellow"))

        choices = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(48), spacing=dp(8))
        choices.add_widget(_btn(tr("Keep it hidden"), "surface", "text_primary",
                                lambda: self._choose(location_share.HIDDEN)))
        choices.add_widget(_btn(tr("Show it, roughly"), "accent", "background",
                                lambda: self._choose(location_share.APPROX)))
        body.add_widget(choices)

        self._apply_btn = _btn(tr("Apply to the node now"), "green",
                               "background", self._apply)
        body.add_widget(self._apply_btn)
        body.add_widget(_btn(tr("Close"), "surface", "text_primary",
                             self.dismiss))

        super().__init__(title=tr("Location & map — {name}").format(
            name=record.name or record.dst_hash[:8]),
            content=body, size_hint=(0.9, 0.9), auto_dismiss=False, **kwargs)
        self._refresh()

    # -- state ------------------------------------------------------------

    @staticmethod
    def _app_registry():
        try:
            from kivy.app import App
            app = App.get_running_app()
            return getattr(getattr(app, "monitor_service", None), "registry",
                           None)
        except Exception:                                          # noqa: BLE001
            return None

    def _refresh(self):
        rec = self.record
        from monitor.geo import format_coord
        if rec.has_location():
            self._where.text = tr(
                "{lat}, {lon} — kept on Node Medic only, for whoever has to "
                "go and repair it.").format(lat=format_coord(rec.lat),
                                            lon=format_coord(rec.lon))
        else:
            self._where.text = tr("Not known. Set one before sharing — there "
                                  "is nothing to publish without it.")
        self._status.text = location_share.status_line(
            rec.share_location, rec.share_applied_at is not None,
            rec.lat, rec.lon)
        view = location_share.stranger_view(
            rec.share_location, rec.name, rec.lat, rec.lon,
            rec.name or rec.dst_hash)
        if view["shared"] and view["pin"]:
            flat, flon, radius = view["pin"]
            self._what.text = tr(
                "A stranger would see: {items}.\nThe point announced is "
                "{lat}, {lon} — up to {radius:.0f} m from the truth."
            ).format(items="; ".join(view["items"]),
                     lat=format_coord(flat), lon=format_coord(flon),
                     radius=radius) + "\n" + location_share.reach_note()
        else:
            self._what.text = tr("Nothing about this node's whereabouts leaves "
                                 "Node Medic.")

    # -- actions ----------------------------------------------------------

    def _pick_location(self):
        """The existing confirm-location map, reused whole — the same tap-the-pin,
        type-an-address, check-the-address gate a birth goes through, because a
        location set here ends up in exactly the same places."""
        from ui.widgets.confirm_location import ConfirmLocationPopup
        rec = self.record
        lat, lon = rec.lat, rec.lon
        if lat is None or lon is None:
            # Start the map SOMEWHERE the operator can navigate from. The
            # medic's own current fix is the honest starting point when it has
            # one — and it is only a starting point: nothing is stored unless
            # the operator confirms a pin.
            try:
                from monitor.geo import read_splitter_fix
                fix = read_splitter_fix()
            except Exception:                                      # noqa: BLE001
                fix = None
            lat, lon = (fix.lat, fix.lon) if fix else (0.0, 0.0)
        try:
            from monitor.geo import splitter_gps_reader
            reader = splitter_gps_reader()
        except Exception:                                          # noqa: BLE001
            reader = None
        ConfirmLocationPopup(lat, lon, node_name=rec.name,
                             on_confirm=self._location_confirmed,
                             gps_reader=reader).open()

    def _location_confirmed(self, lat, lon):
        rec = self.record
        rec.lat, rec.lon = float(lat), float(lon)
        # A NEW POSITION IS A NEW THING TO PUBLISH. Whatever was written to the
        # node is now about a different place, so it stops counting as applied
        # until it has been written again.
        rec.share_applied_at = None
        try:
            from monitor import kin_roster
            kin_roster.set_location(rec.dst_hash, rec.lat, rec.lon)
        except Exception:                                          # noqa: BLE001
            pass
        self._persist()
        self._refresh()

    def _choose(self, policy):
        rec = self.record
        if self._registry is not None:
            self._registry.set_share_location(rec.dst_hash, policy,
                                              now=self._now())
        else:
            rec.share_location = location_share.normalise(policy)
            rec.share_applied_at = None
        try:
            from monitor import kin_roster
            kin_roster.set_share_location(rec.dst_hash, policy)
        except Exception:                                          # noqa: BLE001
            pass
        self._persist()
        self._refresh()

    def _apply(self):
        """Write the decision into the node's own Reticulum config, over SSH.

        Everything about this is reported from what the node said afterwards.
        If the medic cannot reach it, that is the answer — not a failure to be
        smoothed over, because an unreachable node is one that is still doing
        whatever it was doing before.
        """
        if self._busy:
            return
        rec = self.record
        addr = self._node_address()
        if not addr:
            self._status.text = tr(
                "Node Medic has no address on file for this node, so it can't "
                "reach it to change anything. The decision is saved here and "
                "will need applying when the node is reachable.")
            return
        self._busy = True
        self._apply_btn.text = tr("Applying…")

        def work():
            try:
                from transport.connection import SSHConnection
                conn = SSHConnection(addr)
                ok, detail = self._push(
                    conn.run, policy=rec.share_location, name=rec.name,
                    lat=rec.lat, lon=rec.lon,
                    node_key=rec.name or rec.dst_hash)
            except Exception as exc:                               # noqa: BLE001
                ok, detail = False, tr("Couldn't reach the node: {why}").format(
                    why=exc)
            Clock.schedule_once(lambda _d: self._applied(ok, detail), 0)

        threading.Thread(target=work, daemon=True).start()

    def _applied(self, ok, detail):
        self._busy = False
        self._apply_btn.text = tr("Apply to the node now")
        if ok and self._registry is not None:
            self._registry.mark_share_applied(self.record.dst_hash, self._now())
        elif ok:
            self.record.share_applied_at = self._now()
        self._persist()
        self._refresh()
        # The node's own words go BELOW the status line, never instead of it.
        self._status.text = self._status.text + "\n" + str(detail)

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def _now():
        import time
        return time.time()

    def _node_address(self) -> str:
        try:
            from ui.cert_store import load_certs
            return location_share.node_address_from_certs(
                load_certs(), name=self.record.name,
                identity_hash=self.record.identity_hash or "")
        except Exception:                                          # noqa: BLE001
            return ""

    def _persist(self):
        if self._save is not None:
            self._save()
            return
        # STRAIGHT TO DISK, not at the next autosave. The registry autosaves on
        # a 5-minute timer, and a decision about publishing a location is
        # exactly the kind of thing that must survive the power being pulled in
        # the meantime — a "hidden" that did not persist is a node still
        # sharing (the 5-minute-autosave trap, mesh-listener saga).
        try:
            from kivy.app import App
            app = App.get_running_app()
            reg = getattr(getattr(app, "monitor_service", None), "registry",
                          None)
            path = getattr(app, "_REGISTRY_FILE", "")
            if reg is not None and path:
                reg.save(path)
        except Exception:                                          # noqa: BLE001
            pass
