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

Phase 1 scope (this commit): the framework core — Game backend interface,
registry (bundled + user dirs), session lifecycle, host authority, and
persistence helpers. All headless-testable (no transport, no QML yet).
Transport/wire + localhost feed land in Phase 2; the reference game
(pong_bricks) in Phase 3.

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


# --------------------------------------------------------------------------
# Misc helpers
# --------------------------------------------------------------------------

def _log(event: str, **kw) -> None:
    import server  # deferred, late-bound
    kw["event"] = event
    server._emit(kw)
