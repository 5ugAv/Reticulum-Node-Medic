"""CHAT — the medic's own messenger (docs/CHAT.md, 2026-09-29).

Three views in one widget, so the on-screen keyboard (which pans the
ScreenManager, not a popup) always has a field it can reach:

* LIST   — every conversation, newest first, unread in the accent colour;
* THREAD — one peer: bubbles (theirs left, ours right), each of ours with its
           delivery state in plain words; the compose row at the foot;
* NEW    — pick a name the mesh has announced, or paste an address.

The screen reads the MessageStore and polls its ``version`` once a second
while open; it never touches RNS. The phone hand-off (Columba/Sideband APKs)
that used to BE this card is one button away — "Phone apps".
"""
from __future__ import annotations

import time
from datetime import datetime

from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from monitor import lxmf_chat as lc
from monitor.lxmf_chat import preview as _preview
from ui import theme
from ui.i18n import tr  # i18n: wrapped — chat labels, buttons, hints
from ui.onscreen_keyboard import bind_field
from ui.text_fit import grow_to_text

LIST, THREAD, NEW = "list", "thread", "new"
#: Press-and-hold this long on a conversation row to get "Delete this chat".
HOLD_S = 0.6


class _HoldRow(Button):
    """A list row that fires ``on_hold`` after HOLD_S of a steady press, and
    then swallows the release so a hold is never also a tap."""

    def __init__(self, on_hold, **kw):
        super().__init__(**kw)
        self._on_hold = on_hold
        self._ev = None
        self._held = False

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._held = False
            self._ev = Clock.schedule_once(lambda dt: self._fire(touch), HOLD_S)
        return super().on_touch_down(touch)

    def _cancel(self):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def on_touch_move(self, touch):
        if self._ev is not None and not self.collide_point(*touch.pos):
            self._cancel()                       # slid off: not a hold
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        self._cancel()
        if self._held:
            self._held = False
            self.state = "normal"
            return True                          # the hold already acted
        return super().on_touch_up(touch)

    def _fire(self, touch):
        self._ev = None
        self._held = True
        self._on_hold()


def _lbl(text, size="15sp", color="text_primary", bold=False, halign="left"):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign=halign, valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    return grow_to_text(lbl)


def _btn(text, color="surface", ink="text_primary", h=48, size="15sp"):
    return Button(text=text, size_hint_y=None, height=dp(h), bold=True,
                  font_size=theme.font_sp(size), background_normal="",
                  background_down="",
                  background_color=theme.hex_to_rgba(theme.COLORS[color]),
                  color=theme.hex_to_rgba(theme.COLORS[ink]))


def when(ts: float, now: float = None) -> str:
    """'14:05' today, '3 Sep 14:05' otherwise — the same words a phone uses."""
    now = time.time() if now is None else now
    d, n = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    return d.strftime("%H:%M") if d.date() == n.date() else d.strftime("%-d %b %H:%M")


def _state_words(rec: dict) -> str:
    """The words beside an outgoing message's time. A message nobody answered
    for is held by THIS medic's own propagation node until the person is back
    on the mesh — said in the fewest words that are still true (operator,
    2026-10-04: "waiting for the user to come online, something simpler").
    The holding node's name stays on the record (``via``) for the day there
    is more than one to choose from."""
    return tr(lc.STATE_WORDS.get(rec.get("state"), ""))


class _Bubble(BoxLayout):
    """One message. Ours: accent ground, dark ink, right. Theirs: surface, left."""

    def __init__(self, rec: dict, **kw):
        super().__init__(orientation="vertical", size_hint=(None, None),
                         padding=(dp(12), dp(8)), spacing=dp(2), **kw)
        ours = rec.get("dir") == lc.OUT
        ground = theme.COLORS["accent" if ours else "surface"]
        ink = "background" if ours else "text_primary"
        with self.canvas.before:
            Color(*theme.hex_to_rgba(ground))
            self._bg = RoundedRectangle(radius=[dp(12)] * 4)
        self.bind(pos=self._paint, size=self._paint)
        body = _lbl(lc.readable(rec.get("text", "")), size="15.5sp", color=ink)
        meta = when(rec.get("ts", 0))
        if ours:
            meta += "  ·  " + _state_words(rec)
        foot = _lbl(meta, size="11sp", color=ink)
        foot.opacity = 0.75
        self.add_widget(body)
        self.add_widget(foot)
        self.bind(minimum_height=self.setter("height"))

    def _paint(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size


class ChatScreen(BoxLayout):
    def __init__(self, store, service_getter, open_phone_apps=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        self._store = store
        self._svc = service_getter
        self._open_phone_apps = open_phone_apps
        self._view = LIST
        self._peer = None
        self._seen_version = -1
        self._poll = None

        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                         spacing=dp(8))
        self._title = _lbl(tr("Chat"), size="22sp", bold=True)
        head.add_widget(self._title)
        # One road to the phone apps, not two: the corner "Phone apps" key
        # and the full-width row at the foot were the same thing on the same
        # screen (operator, 2026-10-01). The row stays.
        self.add_widget(head)

        self._addr = _lbl("", size="11.5sp", color="text_secondary")
        self.add_widget(self._addr)

        self._scroll = ScrollView(size_hint=(1, 1))
        self._body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self._body.bind(minimum_height=self._body.setter("height"))
        self._scroll.add_widget(self._body)
        self.add_widget(self._scroll)

        self._foot = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self._foot.bind(minimum_height=self._foot.setter("height"))
        self.add_widget(self._foot)

    # -- lifecycle -----------------------------------------------------------

    def enter(self):
        self._seen_version = -1
        self._render()
        if self._poll is None:
            self._poll = Clock.schedule_interval(self._maybe_refresh, 1.0)

    def leave(self):
        if self._poll is not None:
            self._poll.cancel()
            self._poll = None

    def handle_back(self) -> bool:
        """One step back inside the screen (thread/new → list); False at the root."""
        if self._view != LIST:
            self._show(LIST)
            return True
        return False

    def _maybe_refresh(self, *_):
        if self._store.poll() != self._seen_version:
            self._render()

    def _show(self, view, peer=None):
        self._view = view
        if peer is not None:
            self._peer = peer
        self._render()

    # -- rendering -----------------------------------------------------------

    def _render(self):
        self._seen_version = self._store.version
        svc = self._svc() if self._svc else None
        self._svc_now = svc
        addr = getattr(svc, "address", "") if svc else ""
        if addr:
            self._addr.text = tr("Your address") + ":  " + addr
        elif svc is not None and getattr(svc, "last_error", ""):
            self._addr.text = tr("Chat isn't up — the medic hasn't reached its own radio yet.")
        else:
            self._addr.text = tr("Chat is starting…")
        self._body.clear_widgets()
        self._foot.clear_widgets()
        if self._view == THREAD and self._peer:
            self._render_thread()
        elif self._view == NEW:
            self._render_new()
        else:
            self._render_list()

    def _render_list(self):
        unread = self._store.unread_total()
        self._title.text = tr("Chat") + (f"  ({unread})" if unread else "")
        convs = self._store.conversations()
        if not convs:
            self._body.add_widget(_lbl(tr(
                "No conversations yet. Tap New message, or wait — anyone who "
                "writes to this address appears here."),
                size="14sp", color="text_secondary"))
        for c in convs:
            row = _HoldRow(lambda p=c.peer: self._ask_delete(p), text="",
                           size_hint_y=None, height=dp(64), bold=True,
                           font_size=theme.font_sp("15sp"), background_normal="",
                           background_down="",
                           background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                           color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            row.halign, row.valign = "left", "middle"
            row.markup = True
            unread = f"  [color={theme.COLORS['accent']}]●{c.unread}[/color]" if c.unread else ""
            # name AND hash (NomadNet's rule): two phones can both say "Marnie"
            tag = "" if c.name == lc.short_hash(c.peer) else f"  {lc.short_hash(c.peer)}"
            esc = lc.escape_markup
            row.text = (f"[b]{esc(c.name)}[/b]"
                        f"[color={theme.COLORS['text_secondary']}]{esc(tag)}[/color]{unread}   "
                        f"[color={theme.COLORS['text_secondary']}]{when(c.last_ts)}[/color]\n"
                        f"[color={theme.COLORS['text_secondary']}]"
                        f"{esc(_preview(c.last_text))}[/color]")
            row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
            row.bind(on_release=lambda _b, p=c.peer: self._open_thread(p))
            self._body.add_widget(row)
        svc = self._svc() if self._svc else None
        checked = getattr(svc, "last_sync_at", 0.0) if svc else 0.0
        self._foot.add_widget(_lbl(
            tr("Propagation node checked {when}").format(when=when(checked)) if checked
            else tr("Propagation node not checked yet"), size="11.5sp",
            color="text_secondary"))
        new = _btn(tr("New message"), color="green", ink="background", h=52)
        new.bind(on_release=lambda *_: self._show(NEW))
        self._foot.add_widget(new)
        # The phone hand-off, in words, full width. A 13 sp "Phone apps" in
        # the corner read as "the APKs are gone" on a quick look (operator,
        # 2026-09-30). Both stay: the medic's own chat AND the phone's.
        apk = _btn(tr("Put Columba or Sideband on a phone  →"), h=48, size="14sp")
        apk.bind(on_release=lambda *_: self._open_phone_apps and self._open_phone_apps())
        self._foot.add_widget(apk)

    def _ask_delete(self, peer):
        from ui.confirm import confirm_leave
        name = self._store.peer_name(peer)
        confirm_leave(
            tr("Delete the chat with {name}? The messages go from this medic. "
               "{name} stays on the mesh and can write again.").format(name=name),
            tr("Delete this chat"),
            on_leave=lambda *_: self._delete(peer),
            stay_text=tr("Keep it"), leave_text=tr("Delete"), leave_color="red")

    def _delete(self, peer):
        self._store.delete_conversation(peer)
        if self._peer == peer:
            self._peer = None
        self._show(LIST)

    def _open_thread(self, peer):
        self._store.mark_read(peer)
        self._show(THREAD, peer=peer)

    def _render_thread(self):
        # Read means seen: a message that arrives while this thread is open
        # was staying unread (badge "1" over the very words, 2026-10-01).
        self._store.mark_read(self._peer)
        self._title.text = self._store.peer_name(self._peer)
        back = _btn(tr("← All chats"), h=40, size="13sp")
        back.bind(on_release=lambda *_: self._show(LIST))
        self._body.add_widget(back)
        self._body.add_widget(_lbl(self._peer, size="11sp", color="text_secondary"))
        self._body.add_widget(_lbl(self._route_line(), size="12.5sp", color="text_secondary"))
        for rec in self._store.thread(self._peer):
            ours = rec.get("dir") == lc.OUT
            wrap = AnchorLayout(anchor_x="right" if ours else "left",
                                size_hint_y=None)
            b = _Bubble(rec)
            wrap.bind(width=lambda i, w, bb=b: setattr(bb, "width", w * 0.82))
            b.bind(height=lambda bb, h, ww=wrap: setattr(ww, "height", h))
            wrap.add_widget(b)
            self._body.add_widget(wrap)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        # ONE compose field for the life of the screen. It was rebuilt empty
        # on every store change — a reply or a delivery tick wiped the
        # half-typed sentence and left the keyboard writing into a detached
        # widget (readiness sweep, 2026-10-03).
        if getattr(self, "_compose", None) is None:
            self._compose = TextInput(hint_text=tr("Write a message…"), multiline=False,
                                      size_hint_y=None, height=dp(52), font_size="20sp")
            bind_field(self._compose)
            self._compose.bind(on_text_validate=lambda *_: self._send())
        if self._compose.parent is not None:
            self._compose.parent.remove_widget(self._compose)
        row.add_widget(self._compose)
        send = _btn(tr("Send"), color="green", ink="background", h=52)
        send.size_hint_x = None
        send.width = dp(96)
        send.bind(on_release=lambda *_: self._send())
        svc = getattr(self, "_svc_now", None)
        running = bool(getattr(svc, "running", False)) if svc is not None else False
        send.disabled = not running
        self._compose.disabled = not running
        if not running:
            self._compose.hint_text = tr("Chat isn't up yet — see the line at the top")
        row.add_widget(send)
        self._foot.add_widget(row)
        Clock.schedule_once(lambda *_: setattr(self._scroll, "scroll_y", 0), 0)

    def _route_line(self) -> str:
        """Where a message to this peer goes RIGHT NOW, and when they were
        last heard — so a dead address shows before you type into it."""
        svc = self._svc() if self._svc else None
        seen = 0.0
        for p in self._store.peers():
            if p["hash"] == self._peer:
                seen = p.get("seen", 0.0) or 0.0
        heard = (tr("last heard {when}").format(when=when(seen)) if seen
                 else tr("never heard on this mesh"))
        r = svc.peer_route(self._peer) if svc is not None else {"hops": None, "known": False}
        if r.get("hops") is not None:
            via = r.get("interface") or ""
            hops = r["hops"]
            route = (tr("{n} hop via {iface}") if hops == 1 else tr("{n} hops via {iface}")
                     ).format(n=hops, iface=via) if via else tr("{n} hops away").format(n=hops)
        elif r.get("known"):
            route = tr("no path right now — held here until they're back online")
        else:
            route = tr("not reachable yet — nothing on the mesh has announced this address")
        return f"{route}  ·  {heard}"

    def _send(self):
        text = (self._compose.text or "").strip()
        svc = self._svc() if self._svc else None
        if not text or svc is None:
            return
        if svc.send(self._peer, text) is not None:
            self._compose.text = ""
        else:
            # say WHY nothing happened, in the field itself
            self._compose.hint_text = (tr("Chat isn't up yet — see the line at the top")
                                       if not getattr(svc, "running", False)
                                       else tr("That isn't a 32-character address"))

    def _render_new(self):
        self._title.text = tr("New message")
        back = _btn(tr("← All chats"), h=40, size="13sp")
        back.bind(on_release=lambda *_: self._show(LIST))
        self._body.add_widget(back)
        self._body.add_widget(_lbl(tr("To: paste a 32-character address, or pick a name below"),
                                   size="13.5sp", color="text_secondary"))
        if getattr(self, "_to", None) is None:          # kept across refreshes, like compose
            self._to = TextInput(hint_text=tr("32-character address"), multiline=False,
                                 size_hint_y=None, height=dp(48), font_size="22sp")
            bind_field(self._to)
        if self._to.parent is not None:
            self._to.parent.remove_widget(self._to)
        self._body.add_widget(self._to)
        go = _btn(tr("Open"), color="green", ink="background", h=48)
        go.bind(on_release=lambda *_: self._open_typed())
        self._body.add_widget(go)
        self._body.add_widget(Widget(size_hint_y=None, height=dp(6)))
        self._body.add_widget(_lbl(tr("Heard on the mesh"), size="15sp", bold=True,
                                   color="accent"))
        peers = self._store.peers()
        if not peers:
            self._body.add_widget(_lbl(tr(
                "Nobody heard yet — a phone running Sideband or Columba announces "
                "itself once it's on the mesh."), size="13.5sp", color="text_secondary"))
        for p in peers:
            b = _btn("", h=54)
            b.halign, b.valign, b.markup = "left", "middle", True
            b.text = (f"[b]{lc.escape_markup(p['name'] or lc.short_hash(p['hash']))}[/b]   "
                      f"[color={theme.COLORS['text_secondary']}]{lc.escape_markup(p['hash'])}[/color]")
            b.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
            b.bind(on_release=lambda _b, h=p["hash"]: self._open_thread(h))
            self._body.add_widget(b)

    def _open_typed(self):
        from monitor.operator_alert import normalize_address, valid_address
        a = normalize_address(self._to.text or "")
        if valid_address(a):
            # Not added to the peer list: a typed address was not heard on the mesh,
            # and the first one typed (a wrong one) sat under "Heard on the
            # mesh" as if it had been (2026-10-01).
            self._open_thread(a)
        else:
            self._to.hint_text = tr("That isn't a 32-character address")
            self._to.text = ""
