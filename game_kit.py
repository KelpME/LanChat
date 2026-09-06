#!/usr/bin/env python3
"""Games platform framework for KelpME.lanchat.

A game is an ADD-ON folder (manifest.json + game.py backend + web/ three.js
frontend) dropped into a games dir — bundled (ships in the plugin under
`games/`) or user (drop into the user games dir). This module is the
framework every game implements against, and owns all the plumbing a game
shouldn't have to write: discovery/registry, session lifecycle, host
authority, live join/drop/rejoin, input routing, snapshot/event fan-out,
persistence, and serving the game's web frontend.

Design (plans/GAMES-PLATFORM.md, approved 2026-09-06):
- A game SESSION is room-scoped (keyed by roomId + gameId), not 1:1.
- The ROOM OWNER's daemon runs the authoritative sim; players are thin
  clients (input in, snapshots/events out). Host-offline FREEZES the game
  (same freeze semantics as room management) — chat still works (mesh).
- No new ports / firewall surface: multiplayer rides the existing
  authenticated TLS socket as {"t":"game",...}; the local render feed is a
  loopback HTTP consumer (http_api pattern), never the peer.

Phase 1 (committed): framework core — Game backend interface, registry,
session lifecycle, host authority, join/drop/rejoin, persistence.
Phase 2 (this): wire transport + localhost feed — {"t":"game",...} wire
types over the existing TLS socket (send_game + handle_game_msg), and a
loopback HTTP feed + static web serving. Still headless-testable. The
reference game (pong_bricks) + host sim thread land in Phase 3.

Ownership: STATE.game_sessions (roomId -> session), STATE.games_lock.
At-rest crypto reuses the history module's AES-256-GCM helpers (degrades to
plaintext when `cryptography` is unavailable, same as history.json).

Module contract (matches rooms.py/history.py/attachments.py exactly):
- `init(state)` binds STATE once, called by server.py at import time.
- Calls into server-resident helpers (_emit, host_id, ...) use a deferred
  `import server` inside function bodies — late-bound, no import cycle,
  monkeypatch-safe.
- server.py re-exports this module's public names (test contract).
"""

import json
import os
import secrets
import threading
import time

# init(state) wiring: STATE is bound once by server.py at import time
# (game_kit.init(STATE)). See module docstring.
STATE = None


def init(state):
    """Bind this module's STATE to the daemon's shared State instance."""
    global STATE
    STATE = state


# --------------------------------------------------------------------------
# Paths & limits
# --------------------------------------------------------------------------

# Bundled games ship in the plugin under `games/` (resolved relative to the
# server.py's own directory — never a hardcoded absolute path).
def bundled_games_dir() -> str:
    import server  # deferred, late-bound
    here = os.path.dirname(os.path.abspath(server.__file__))
    return os.path.join(here, "games")


def user_games_dir() -> str:
    """User drop-in games dir under the daemon config dir (portable)."""
    cfg_dir = os.path.join(os.path.expanduser("~"), ".config", "omarchy")
    return os.path.join(cfg_dir, "lanchat-games")


def games_state_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "state", "lanchat", "games")


GAME_MAGIC = b"LANCHGAME1"  # distinct magic so stores never decode each other
MANIFEST_NAME = "manifest.json"
ENTRY_MODULE = "game.py"
WEB_DIR = "web"
MAX_GAME_NAME = 48


# --------------------------------------------------------------------------
# Game backend interface (what a game author implements)
# --------------------------------------------------------------------------

class Game:
    """Base class a game author subclasses in their game.py.

    A game author implements the SIM only. The framework (this module) owns
    session lifecycle, host authority, join/drop/rejoin, input routing,
    snapshot/event fan-out, persistence, and frontend serving.

    Class attrs (game metadata, read from the subclass, not the manifest —
    the manifest is the static discovery record; these are the live values):
      name        e.g. "pong_bricks"          (must match manifest)
      title       human title, e.g. "Pong × Brick Breaker"
      modes       list of mode ids, e.g. ["vs", "coop"]
      min_players max_players                 (None = unbounded)
      tick_rate   internal sim ticks/sec      (default 60)
      snapshot_rate  snapshots broadcast/sec  (default 20)

    The sim runs on the HOST's daemon. Methods that mutate sim state are
    called on the host only; snapshot() is called on every machine to render
    locally from the authoritative state the host broadcasts.
    """

    name = ""
    title = ""
    modes = []
    min_players = 1
    max_players = None
    tick_rate = 60
    snapshot_rate = 20

    # ---- lifecycle -------------------------------------------------------
    def init(self, ctx, seed, members):
        """Set up a fresh session. ctx carries roomId/gameId/mode/host/theme.
        members is the initial dict {fp: theme}. Return None or an initial
        state dict (also fine to build in snapshot())."""

    def load(self, data):
        """Restore from a persisted snapshot (rejoin). Called instead of init
        on a resume; must rebuild the sim to the exact saved state."""

    def add_player(self, fp, theme):
        """Live join: add a player mid-game (spawn fresh; existing state
        stands). Host only."""

    def remove_player(self, fp):
        """Drop: remove a player (state persists for rejoin). Host only."""

    def apply_input(self, fp, action, value):
        """Player control input (e.g. paddle x). Host only. Throttled by the
        caller (~30/s)."""

    def tick(self, dt):
        """One authoritative sim step of dt seconds. Host only (~tick_rate)."""

    def snapshot(self):
        """Authoritative state dict broadcast to all players (and rendered
        locally). Called on the host; players render from what the host
        broadcasts. MUST be JSON-serializable and small enough for the
        snapshot_rate."""

    def serialize(self):
        """Full persistent state (superset of snapshot — includes score,
        lives, phase) for abandon/rejoin. Host only. JSON-serializable."""

    # ---- helpers games can rely on ---------------------------------------
    @staticmethod
    def seed() -> int:
        return secrets.randbelow(2**31)


# --------------------------------------------------------------------------
# Registry: discover games from bundled + user dirs
# --------------------------------------------------------------------------

def discover_games() -> list:
    """Return a list of game metadata dicts from bundled + user games dirs.

    Each: {name, title, modes, minPlayers, maxPlayers, version, bundled(bool),
    path (dir)}. Invalid folders are skipped with a log line — a broken
    user game never bricks discovery.
    """
    found = {}
    _scan_dir(bundled_games_dir(), bundled=True, out=found)
    _scan_dir(user_games_dir(), bundled=False, out=found)
    return [v for _, v in sorted(found.items())]


def game_web_dir(game_name: str) -> str:
    """The static web root for a game bundle (its `www/` folder), or '' if the
    game has no web frontend. Resolved from the installed game folder."""
    meta = next((g for g in discover_games() if g["name"] == game_name), None)
    if not meta:
        return ""
    web = os.path.join(meta["path"], "www")
    return web if os.path.isdir(web) else ""


def game_window_url(game_name: str, room_id: str, game_id: str) -> str:
    """Build the browser launch URL for a game window served by THIS daemon's
    plain-HTTP loopback feed. Includes the token (the game's loopback transport
    needs it to auth) + roomId/gameId so the window knows its session. The QML
    opens this URL; it never needs the token itself."""
    import urllib.parse

    import http_api  # noqa: F401  (loopback port helper)
    import server  # deferred, late-bound
    lb = http_api._loopback_port()
    token = str(server.STATE.config.get("token", ""))
    q = urllib.parse.quote
    return ("http://127.0.0.1:%d/www/%s/index.html?token=%s&room=%s&game=%s"
            % (lb, q(game_name), q(token), q(room_id), q(game_id)))


def _scan_dir(base, bundled, out):
    if not base or not os.path.isdir(base):
        return
    for entry in sorted(os.listdir(base)):
        gdir = os.path.join(base, entry)
        if not os.path.isdir(gdir) or not os.path.isfile(os.path.join(gdir, MANIFEST_NAME)):
            continue
        try:
            with open(os.path.join(gdir, MANIFEST_NAME), "r", encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError) as e:
            _log("game-skip", game=entry, reason=f"manifest unreadable: {e}")
            continue
        name = str(m.get("name", entry))[:MAX_GAME_NAME]
        meta = {
            "name": name,
            "title": str(m.get("title", name)),
            "modes": [str(x) for x in (m.get("modes") or [])],
            "minPlayers": int(m.get("minPlayers", 1)),
            "maxPlayers": m.get("maxPlayers"),
            "version": str(m.get("version", "0.0.0")),
            "bundled": bool(bundled),
            "path": gdir,
        }
        # user drop-in wins over bundled of the same name
        out[name] = meta


def load_game(name: str):
    """Import and instantiate a game's backend class by folder name.

    Returns the Game subclass instance, or None if the game isn't found /
    its game.py fails to load (logged, never raises).
    """
    meta = next((g for g in discover_games() if g["name"] == name), None)
    if not meta:
        return None
    return load_game_from_dir(meta["path"], name)


def load_game_from_dir(gdir: str, name: str):
    module_path = os.path.join(gdir, ENTRY_MODULE)
    if not os.path.isfile(module_path):
        _log("game-skip", game=name, reason="missing game.py")
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("lanchat_game_" + name, module_path)
        if spec is None or spec.loader is None:
            _log("game-skip", game=name, reason="importlib spec unavailable")
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        _log("game-load-error", game=name, error=str(e))
        return None
    for attr in ("Game",):
        if hasattr(mod, attr):
            cls = getattr(mod, attr)
            if isinstance(cls, type) and issubclass(cls, Game):
                inst = cls()
                if not inst.name:
                    inst.name = name
                return inst
    _log("game-skip", game=name, reason="no Game subclass in game.py")
    return None


# --------------------------------------------------------------------------
# Session lifecycle (host-authoritative)
# --------------------------------------------------------------------------

def sessions() -> dict:
    """STATE.game_sessions: {roomId: {gameId: session}}. Lazy-init."""
    if not hasattr(STATE, "game_sessions"):
        STATE.game_sessions = {}
    if not hasattr(STATE, "games_lock"):
        import threading
        STATE.games_lock = threading.Lock()
    return STATE.game_sessions


def _lock():
    # ensure STATE.game_sessions + STATE.games_lock exist (lazy init also
    # used by sessions()); the sim thread calls _lock() before any session
    # exists, so it must never hit a missing attribute.
    if not hasattr(STATE, "game_sessions"):
        STATE.game_sessions = {}
    if not hasattr(STATE, "games_lock"):
        STATE.games_lock = threading.Lock()
    return STATE.games_lock


def create_session(room_id, game_name, mode, host_fp, theme, members):
    """Create a new game session in a room. Called on the HOST (room owner)
    daemon when an invite is accepted. Returns the session dict, or None if
    the game can't be loaded.

    session = {
      "gameId": <hex>, "roomId": room_id, "game": game_name, "mode": mode,
      "host": host_fp, "created": ms, "phase": "playing",
      "players": {fp: {"theme": {...}}},  # joined players
      "seed": int, "gameInst": <Game instance>,
    }
    """
    with _lock():
        s = sessions()
        room_games = s.setdefault(room_id, {})
        gameId = secrets.token_hex(8)
        game = load_game(game_name)
        if game is None:
            _log("game-skip", game=game_name, reason="unloadable at create")
            return None
        seed = game.seed()
        ctx = {"roomId": room_id, "gameId": gameId, "mode": mode, "host": host_fp}
        session = {
            "gameId": gameId, "roomId": room_id, "game": game_name, "mode": mode,
            "host": host_fp, "created": int(time.time() * 1000), "phase": "playing",
            "seed": seed, "players": {}, "gameInst": game,
        }
        game.init(ctx, seed, dict(members or {}))
        # the host is the first player
        session["players"][host_fp] = {"theme": theme or {}}
        game.add_player(host_fp, theme or {})
        room_games[gameId] = session
        _persist()
        return session


def get_session(room_id, game_id):
    return sessions().get(room_id, {}).get(game_id)


def player_join(room_id, game_id, fp, theme):
    """Live join a mid-game session (host side). Returns updated session."""
    s = get_session(room_id, game_id)
    if s is None:
        return None
    s.setdefault("players", {})[fp] = {"theme": theme or {}}
    try:
        s["gameInst"].add_player(fp, theme or {})
    except Exception as e:
        _log("game-error", game=game_id, op="add_player", error=str(e))
    _persist()
    return s


def player_drop(room_id, game_id, fp):
    """Drop a player mid-game (host side); state persists for rejoin."""
    s = get_session(room_id, game_id)
    if s is None:
        return None
    s.get("players", {}).pop(fp, None)
    try:
        s["gameInst"].remove_player(fp)
    except Exception as e:
        _log("game-error", game=game_id, op="remove_player", error=str(e))
    _persist()
    return s


def apply_input(room_id, game_id, fp, action, value):
    s = get_session(room_id, game_id)
    if s is None:
        return None
    try:
        s["gameInst"].apply_input(fp, action, value)
    except Exception as e:
        _log("game-error", game=game_id, op="apply_input", error=str(e))
    return s


def tick_session(room_id, game_id, dt):
    """Advance the authoritative sim one step (host sim thread)."""
    s = get_session(room_id, game_id)
    if s is None:
        return None
    try:
        s["gameInst"].tick(dt)
    except Exception as e:
        _log("game-error", game=game_id, op="tick", error=str(e))
    return s


def session_snapshot(room_id, game_id):
    """The authoritative state dict a host broadcasts / players render."""
    s = get_session(room_id, game_id)
    if s is None:
        return None
    return s["gameInst"].snapshot()


# --------------------------------------------------------------------------
# Persistence (AES-GCM at rest via the history module's helpers)
# --------------------------------------------------------------------------

def _crypto_ok() -> bool:
    import server  # deferred, late-bound
    return server._hist_crypto_ok()


def _games_key() -> bytes:
    import server  # deferred, late-bound
    key_path = os.path.join(games_state_dir(), "games.key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            k = f.read()
        if len(k) == 32:
            return k
    k = secrets.token_bytes(32)
    tmp = key_path + "." + secrets.token_hex(6) + ".tmp"
    os.makedirs(os.path.dirname(tmp) or ".", exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(k)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, key_path)
    server._log("games-key-generated")
    return k


def _encrypt(plain: bytes) -> str:
    if not _crypto_ok():
        return plain.decode("utf-8")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_games_key()).encrypt(nonce, plain, None)
    import base64
    return base64.b64encode(GAME_MAGIC + nonce + ct).decode("ascii")


def _decrypt(b64: str):
    import base64
    if not _crypto_ok():
        return b64.encode("utf-8")
    try:
        raw = base64.b64decode(b64.encode("ascii"))
    except Exception:
        return None
    if raw[:10] != GAME_MAGIC or len(raw) < 10 + 12 + 16:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        return AESGCM(_games_key()).decrypt(raw[10:22], raw[22:], None)
    except Exception:
        return None


def _games_path(game_id: str) -> str:
    return os.path.join(games_state_dir(), game_id + ".json")


def _serialize_session(s: dict) -> dict:
    """Persistable view of a session (drop the live Game instance)."""
    out = {k: v for k, v in s.items() if k != "gameInst"}
    try:
        out["state"] = s["gameInst"].serialize()
    except Exception as e:
        _log("game-error", game=s.get("gameId"), op="serialize", error=str(e))
        out["state"] = None
    return out


def save_session(room_id, game_id):
    s = get_session(room_id, game_id)
    if s is None:
        return None
    import server  # deferred, late-bound
    path = _games_path(game_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    server.atomic_write(path, _encrypt(json.dumps(_serialize_session(s), separators=(",", ":")) .encode("utf-8")))
    return path


def load_session(game_id):
    """Restore a persisted session by gameId (returns a dict without a live
    Game instance, or None if absent/unreadable)."""
    path = _games_path(game_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    data = None
    try:
        data = json.loads(raw)
    except ValueError:
        plain = _decrypt(raw)
        if plain is not None:
            try:
                data = json.loads(plain.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                data = None
    return data if isinstance(data, dict) else None


def _persist() -> None:
    """Persist every live session's serialized state (rejoin safety)."""
    for room_id, games in sessions().items():
        for game_id in list(games.keys()):
            try:
                save_session(room_id, game_id)
            except Exception as e:
                _log("game-error", game=game_id, op="persist", error=str(e))


def _log(event: str, **kw) -> None:
    import server  # deferred, late-bound
    kw["event"] = event
    server._emit(kw)


# --------------------------------------------------------------------------
# Phase 2: wire transport (daemon ↔ daemon over the existing TLS socket)
# --------------------------------------------------------------------------
# All game traffic rides the existing authenticated socket as {"t":"game",...}
# via server._write — no new ports/security surface. The room OWNER's daemon is
# the host (runs the sim); members send input and receive snapshots/events.
#
# Wire kinds (peer daemon <-> peer daemon):
#   invite          member->host   {game, mode, theme}         initiate
#   inviteAccept    host->member   {gameId, mode, seed, theme} start
#   inviteDecline   host->member   {}                          refuse
#   join            member->host   {gameId, theme}             live join/rejoin
#   joinAck         host->member   {gameId, mode, seed, theme, youAre}
#   drop            member->host   {gameId}                    leave mid-game
#   input           member->host   {gameId, action, value}     control
#   snapshot        host->member   {gameId, state}             authoritative state
#   event           host->member   {gameId, gameEvent}         discrete signal
#   leave           member->host   {gameId}                    abandon
#
# The first version that carries the games protocol (both machines must run it
# for a game to be joinable — same rule as every lanchat protocol change).
GAME_MIN_VERSION = "1.5.52"


def send_game(peer_id: str, payload: dict) -> bool:
    """Send a t:"game" envelope to a peer over the existing socket."""
    import server  # deferred, late-bound
    payload.setdefault("t", "game")
    payload.setdefault("from", server.host_id())
    payload.setdefault("fromName", server.display_name())
    return server._write(peer_id, payload)


def _peer_supports_games(pid: str) -> bool:
    """Version gate: refuse to start/join a game with a peer whose advertised
    base version predates the games protocol."""
    import server  # deferred, late-bound
    peer = server.find_peer(pid) or {}
    ver = str(peer.get("version") or "")
    base = ver.split("-")[0] if ver else ""
    if not base:
        return False  # no advertised version = can't verify the protocol exists
    try:
        parts = [int(x) for x in base.split(".")]
    except ValueError:
        return False
    want = [int(x) for x in GAME_MIN_VERSION.split(".")]
    return parts >= want


def _base_theme(peer_id: str) -> dict:
    """A member's theme (accent/normal/border) — best-effort from the daemon's
    own palette; the real per-player colors come from the frontend later.
    The daemon doesn't hold the QML palette; carry a neutral placeholder the
    frontend replaces with the live theme at render time."""
    return {"accent": "", "normal": "", "border": ""}


# ---- inbound t:"game" dispatch (called from server._handle_incoming) ------

def handle_game_msg(msg: dict, addr) -> None:
    """Route an inbound t:"game" envelope. The connection is already
    authenticated (from = proven fingerprint), same trust level as chat."""
    import server  # deferred, late-bound
    kind = str(msg.get("kind", ""))
    from_pid = str(msg.get("from", ""))
    room_id = str(msg.get("roomId", ""))
    game_id = str(msg.get("gameId", ""))
    me = server.host_id()

    if kind == "invite":
        # A member wants to play. We are (or aren't) the room owner; only the
        # owner runs the sim. Emit the invite to the UI for Accept/Decline.
        if room_id and not server.is_trusted(from_pid):
            return
        server._emit({"event": "game", "kind": "invite", "roomId": room_id,
                      "from": from_pid, "fromName": str(msg.get("fromName") or ""),
                      "game": str(msg.get("game", "")), "mode": str(msg.get("mode", "")),
                      "theme": msg.get("theme") or {}})
        return

    if kind == "inviteAccept":
        # The host accepted our invite: session starts. Store the mirror so
        # our local feed can serve it.
        if _mirror_upsert(room_id, game_id, host=from_pid, mode=str(msg.get("mode", "")),
                          game=str(msg.get("game", "")), seed=msg.get("seed"),
                          theme=msg.get("theme") or {}, you_are=me):
            server._emit({"event": "game", "kind": "invite-accepted",
                          "roomId": room_id, "gameId": game_id, "from": from_pid,
                          "windowUrl": game_window_url(str(msg.get("game", "")), room_id, game_id)})
        return

    if kind == "inviteDecline":
        server._emit({"event": "game", "kind": "invite-declined",
                      "roomId": room_id, "gameId": game_id, "from": from_pid})
        return

    if kind == "joinAck":
        if _mirror_upsert(room_id, game_id, host=from_pid, mode=str(msg.get("mode", "")),
                          game=str(msg.get("game", "")), seed=msg.get("seed"),
                          theme=msg.get("theme") or {}, you_are=str(msg.get("youAre", ""))):
            server._emit({"event": "game", "kind": "joined",
                          "roomId": room_id, "gameId": game_id, "from": from_pid,
                          "windowUrl": game_window_url(str(msg.get("game", "")), room_id, game_id)})
        return

    if kind in ("snapshot", "event"):
        # Host -> member: authoritative state / discrete event. Cache the
        # latest snapshot for the local feed; emit for the UI.
        _mirror_state(game_id, msg.get("state"))
        feed_push_snapshot(room_id, game_id, msg.get("state"))
        if kind == "event" and msg.get("gameEvent") is not None:
            feed_push_event(room_id, game_id, msg.get("gameEvent"))
        server._emit({"event": "game", "kind": kind, "roomId": room_id,
                      "gameId": game_id,
                      "gameEvent": msg.get("gameEvent") if kind == "event" else None,
                      "state": msg.get("state") if kind == "snapshot" else None})
        return

    # Everything below is addressed to the HOST (we must own the room and the
    # session). Non-hosts ignore — mirrors the room owner-authority model.
    if kind in ("join", "drop", "input", "leave"):
        s = get_session(room_id, game_id)
        if s is None or s.get("host") != me:
            return
        if kind == "join":
            if not _peer_supports_games(from_pid):
                send_game(from_pid, {"t": "game", "kind": "joinAck",
                                     "roomId": room_id, "gameId": game_id,
                                     "error": "peer-version-too-old"})
                return
            player_join(room_id, game_id, from_pid, msg.get("theme") or _base_theme(from_pid))
            send_game(from_pid, {"t": "game", "kind": "joinAck", "roomId": room_id,
                                 "gameId": game_id, "mode": s.get("mode"),
                                 "game": s.get("game"), "seed": s.get("seed"),
                                 "theme": msg.get("theme") or {}, "youAre": from_pid})
            _broadcast_snapshot(s)
        elif kind == "drop":
            player_drop(room_id, game_id, from_pid)
        elif kind == "input":
            apply_input(room_id, game_id, from_pid, str(msg.get("action", "")), msg.get("value"))
        elif kind == "leave":
            player_drop(room_id, game_id, from_pid)
        return


# ---- member-side mirror (for the local feed; host also keeps one) ---------

def _mirror_upsert(room_id, game_id, **kw) -> bool:
    if not game_id:
        return False
    with _lock():
        s = sessions()
        room_games = s.setdefault(room_id, {})
        if game_id in room_games:
            return True
        mirror = {"gameId": game_id, "roomId": room_id, "phase": "playing",
                  "players": {}, "host": kw.get("host"), "mode": kw.get("mode"),
                  "game": kw.get("game"), "seed": kw.get("seed"),
                  "theme": kw.get("theme") or {}, "youAre": kw.get("you_are"),
                  "state": None, "mirror": True}
        room_games[game_id] = mirror
        return True


def _mirror_state(game_id, state) -> None:
    for room_id, games in sessions().items():
        if game_id in games:
            games[game_id]["state"] = state
            return


# --------------------------------------------------------------------------
# Per-session feed buffer (for the local loopback feed the browser polls)
# --------------------------------------------------------------------------

FEED_MAX_EVENTS = 200  # keep only the latest N queued events per session


def _feed(session) -> dict:
    """Lazy per-session feed buffer: { seq, snapshot, events } where seq is a
    monotonic cursor and events is a capped list [{ seq, ... }] the browser
    drains with ?after=<seq>."""
    if not isinstance(session.get("feed"), dict):
        session["feed"] = {"seq": 0, "snapshot": None, "events": []}
    return session["feed"]


def feed_push_event(room_id, game_id, game_event) -> None:
    """Buffer a discrete event for the local feed (host's own sim events or a
    member's received events). Kept capped; never raises."""
    try:
        s = get_session(room_id, game_id)
        if s is None:
            return
        f = _feed(s)
        with _lock():
            f["seq"] += 1
            f["events"].append({"seq": f["seq"], **game_event})
            if len(f["events"]) > FEED_MAX_EVENTS:
                del f["events"][: len(f["events"]) - FEED_MAX_EVENTS]
    except Exception:
        pass


def feed_push_snapshot(room_id, game_id, state) -> None:
    """Record the latest authoritative snapshot for the local feed."""
    try:
        s = get_session(room_id, game_id)
        if s is None:
            return
        with _lock():
            _feed(s)["snapshot"] = state
    except Exception:
        pass


def feed_drain(room_id, game_id, after):
    """Return { snapshot, events, latest } for the loopback feed — events with
    seq > `after`, plus the latest snapshot. Thread-safe."""
    s = get_session(room_id, game_id)
    if s is None:
        return None
    with _lock():
        f = _feed(s)
        events = [e for e in f["events"] if e["seq"] > (after or 0)]
        return {"snapshot": f["snapshot"], "events": events, "latest": f["seq"]}


# --------------------------------------------------------------------------
# Loopback control (called by http_api /game/* endpoints — the game window's
# transport hits these; the daemon relays to the host or applies locally)
# --------------------------------------------------------------------------

def route_join(room_id, game_id, seat_id, theme) -> dict:
    """Game window join/rejoin. If we are the room owner we apply locally;
    otherwise forward a join over the wire to the host. Returns a result dict
    the endpoint echoes to the browser."""
    import rooms
    import server  # deferred, late-bound
    room = rooms.get_room(room_id)
    host = (room or {}).get("owner")
    me = server.host_id()
    if host == me:
        s = get_session(room_id, game_id)
        if s is None:
            return {"ok": False, "error": "session not found"}
        if seat_id:
            player_join(room_id, game_id, seat_id, theme or {})
        return {"ok": True, "gameId": game_id, "roomId": room_id,
                "mode": s.get("mode"), "seed": s.get("seed"), "youAre": seat_id or me}
    if host:
        # MEMBER side: create a local mirror NOW so this machine's feed can
        # serve the window immediately (the host's joinAck will populate it).
        # Without this, the window's first feed poll 404s ("session not found")
        # and the client appears to "not connect."
        _mirror_upsert(room_id, game_id, host=host, mode="", game="", seed=None, you_are=seat_id or "")
        send_game(host, {"t": "game", "kind": "join", "roomId": room_id,
                         "gameId": game_id, "theme": theme or {}, "fromName": server.display_name()})
        return {"ok": True, "pending": True}
    return {"ok": False, "error": "host offline — changes frozen"}


def route_input(room_id, game_id, action, value) -> dict:
    """Game window control input. Host applies locally; member forwards to host."""
    import rooms
    import server  # deferred, late-bound
    room = rooms.get_room(room_id)
    host = (room or {}).get("owner")
    me = server.host_id()
    if host == me:
        apply_input(room_id, game_id, me, action, value)
        return {"ok": True}
    if host:
        send_game(host, {"t": "game", "kind": "input", "roomId": room_id,
                         "gameId": game_id, "action": action, "value": value,
                         "fromName": server.display_name()})
        return {"ok": True}
    return {"ok": False, "error": "host offline"}


def route_drop(room_id, game_id, seat_id) -> dict:
    """Game window drop. Host applies locally; member forwards to host."""
    import rooms
    import server  # deferred, late-bound
    room = rooms.get_room(room_id)
    host = (room or {}).get("owner")
    me = server.host_id()
    if host == me:
        player_drop(room_id, game_id, seat_id or me)
        return {"ok": True}
    if host:
        send_game(host, {"t": "game", "kind": "drop", "roomId": room_id,
                         "gameId": game_id, "fromName": server.display_name()})
        return {"ok": True}
    return {"ok": False, "error": "host offline"}


def route_leave(room_id, game_id) -> dict:
    """Game window leave (abandon). Member forwards to host."""
    import rooms
    import server  # deferred, late-bound
    room = rooms.get_room(room_id)
    host = (room or {}).get("owner")
    me = server.host_id()
    if host and host != me:
        send_game(host, {"t": "game", "kind": "leave", "roomId": room_id,
                         "gameId": game_id, "fromName": server.display_name()})
    return {"ok": True}


# ---- host sim thread ------------------------------------------------------

def _sim_thread() -> None:
    """Authoritative sim loop on the HOST (owner) daemon: ticks every live
    session we own and broadcasts snapshots at the game's snapshot_rate."""
    while True:
        start = time.time()
        with _lock():
            snap_sets = []  # (session, due) collected per game
            for room_id, games in sessions().items():
                for gid, s in list(games.items()):
                    inst = s.get("gameInst")
                    if inst is None or s.get("host") != _my_id():
                        continue  # only host sims tick here
                    rate = getattr(inst, "snapshot_rate", 20) or 20
                    tick_session(room_id, gid, 1.0 / (getattr(inst, "tick_rate", 60) or 60))
                    s.setdefault("_last_snap", 0)
                    due = (start - s["_last_snap"]) >= (1.0 / rate)
                    if due:
                        s["_last_snap"] = start
                        snap_sets.append(s)
        for s in snap_sets:
            try:
                _broadcast_snapshot(s)
            except Exception:
                pass
        # sleep to approx tick_rate (cap 200Hz to avoid busy-spin)
        elapsed = time.time() - start
        time.sleep(max(0.0, (1.0 / 200.0) - elapsed))


def _my_id():
    import server  # deferred, late-bound
    return server.host_id()


def _broadcast_snapshot(s) -> None:
    """Host: send the authoritative snapshot + discrete events to every player,
    and buffer the snapshot into the HOST's own local feed (the host's browser
    window reads the local feed, not the wire)."""
    room_id, gid = s.get("roomId"), s.get("gameId")
    state = session_snapshot(room_id, gid)
    if state is None:
        return
    feed_push_snapshot(room_id, gid, state)
    for fp in list(s.get("players", {}).keys()):
        if fp == _my_id():
            continue  # host's own window reads the local feed, not the wire
        send_game(fp, {"t": "game", "kind": "snapshot", "roomId": room_id,
                       "gameId": gid, "state": state})


def start_sim_thread() -> None:
    """Start the daemon-wide host sim thread (called once at daemon init)."""
    t = threading.Thread(target=_sim_thread, daemon=True)
    t.start()

