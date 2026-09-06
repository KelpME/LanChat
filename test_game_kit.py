#!/usr/bin/env python3
"""Games platform framework (game_kit) — Phase 1 offline tests.

Validates the framework core (plans/GAMES-PLATFORM.md, approved 2026-09-06)
WITHOUT a full daemon or any transport:
  - registry discovers bundled + user drop-in game folders
  - a user game folder drops in with no code change (manifest + game.py + web/)
  - load_game imports a Game subclass from a game.py
  - session lifecycle: create_session on the host, live join, drop, rejoin
  - host authority: the host fp is recorded; input routes to the sim
  - persistence: save_session/load_session round-trip survives the live
    Game instance being dropped (rejoin restores a clean session dict)
  - a broken user game never bricks discovery (skip + log, not raise)

Run: python3 test_game_kit.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import game_kit  # noqa: E402

# ---- test fixtures -------------------------------------------------------

# A tiny stub STATE with the fields game_kit lazily touches.
class _StubState:
    pass


def _make_state():
    s = _StubState()
    s.game_sessions = {}
    s.games_lock = threading.Lock()
    s.hist_crypto = None  # force plaintext (no cryptography path in tests)
    return s


STUB_GAME_PY = '''\
"""Stub game for game_kit tests — a minimal Game subclass."""
import game_kit

class Game(game_kit.Game):
    name = "stub"
    title = "Stub Game"
    modes = ["vs", "coop"]
    min_players = 1
    max_players = 8

    def init(self, ctx, seed, members):
        self.state = {"x": 0.5, "score": 0, "seed": seed}

    def add_player(self, fp, theme):
        self.state.setdefault("players", {})[fp] = theme or {}

    def remove_player(self, fp):
        self.state.get("players", {}).pop(fp, None)

    def apply_input(self, fp, action, value):
        if action == "paddle":
            self.state["x"] = value

    def tick(self, dt):
        self.state["x"] = self.state.get("x", 0.5)

    def snapshot(self):
        return {"x": self.state.get("x", 0.5), "score": self.state.get("score", 0)}

    def serialize(self):
        return dict(self.state)
'''

STUB_MANIFEST = {
    "name": "stub",
    "title": "Stub Game",
    "modes": ["vs", "coop"],
    "minPlayers": 1,
    "maxPlayers": 8,
    "version": "1.0.0",
}

BAD_MANIFEST_DIR = {
    "name": "broken",  # game.py raises at import
}


class _Fixture:
    """Create temp bundled + user games dirs and point game_kit at them."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="gamekit-")
        self.bundled = os.path.join(self.root, "bundled")
        self.user = os.path.join(self.root, "user")
        self.state = os.path.join(self.root, "state")
        os.makedirs(self.bundled, exist_ok=True)
        os.makedirs(self.user, exist_ok=True)
        os.makedirs(self.state, exist_ok=True)
        # real module globals get redirected so the tests are hermetic
        self._orig_bundled = game_kit.bundled_games_dir
        self._orig_user = game_kit.user_games_dir
        self._orig_state = game_kit.games_state_dir
        game_kit.bundled_games_dir = lambda: self.bundled
        game_kit.user_games_dir = lambda: self.user
        game_kit.games_state_dir = lambda: self.state

    def add_bundled(self, name, manifest, game_py, web_files=None):
        d = os.path.join(self.bundled, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(manifest, f)
        with open(os.path.join(d, "game.py"), "w") as f:
            f.write(game_py)
        if web_files:
            w = os.path.join(d, "web")
            os.makedirs(w, exist_ok=True)
            for fn, content in web_files.items():
                with open(os.path.join(w, fn), "w") as f:
                    f.write(content)

    def cleanup(self):
        game_kit.bundled_games_dir = self._orig_bundled
        game_kit.user_games_dir = self._orig_user
        game_kit.games_state_dir = self._orig_state
        shutil.rmtree(self.root, ignore_errors=True)


def _setup(fx):
    game_kit.init(_make_state())


# ---- tests ---------------------------------------------------------------

def test_registry_bundled_and_user():
    fx = _Fixture()
    try:
        _setup(fx)
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY,
                       web_files={"index.html": "<html>stub</html>"})
        games = game_kit.discover_games()
        assert any(g["name"] == "stub" and g["bundled"] and g["version"] == "1.0.0"
                   for g in games), games
        # user dir with same name wins
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY)  # still bundled
        print("  registry: bundled game discovered OK")
    finally:
        fx.cleanup()


def test_user_drop_in_wins_over_bundled():
    fx = _Fixture()
    try:
        _setup(fx)
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY)
        # a user-dropped newer copy shadows the bundled one
        user_dir = os.path.join(fx.user, "stub")
        os.makedirs(user_dir, exist_ok=True)
        m2 = dict(STUB_MANIFEST, version="2.0.0")
        with open(os.path.join(user_dir, "manifest.json"), "w") as f:
            json.dump(m2, f)
        with open(os.path.join(user_dir, "game.py"), "w") as f:
            f.write(STUB_GAME_PY)
        games = {g["name"]: g for g in game_kit.discover_games()}
        assert games["stub"]["version"] == "2.0.0"
        assert games["stub"]["bundled"] is False
        print("  user drop-in shadows bundled: OK")
    finally:
        fx.cleanup()


def test_broken_game_never_bricks_discovery():
    fx = _Fixture()
    try:
        _setup(fx)
        fx.add_bundled("broken", BAD_MANIFEST_DIR, "raise RuntimeError('boom')")
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY)
        games = game_kit.discover_games()  # must not raise
        names = [g["name"] for g in games]
        assert "stub" in names, names
        # broken folder may appear in the registry (manifest read ok) but
        # load_game must return None rather than raise
        assert game_kit.load_game("broken") is None
        print("  broken game skipped (no raise): OK")
    finally:
        fx.cleanup()


def test_load_game_imports_subclass():
    fx = _Fixture()
    try:
        _setup(fx)
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY)
        inst = game_kit.load_game("stub")
        assert inst is not None
        assert inst.name == "stub"
        assert inst.modes == ["vs", "coop"]
        print("  load_game imports Game subclass: OK")
    finally:
        fx.cleanup()


def test_session_lifecycle_host_authority():
    fx = _Fixture()
    try:
        _setup(fx)
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY)
        host = "h" * 40
        s = game_kit.create_session("room1", "stub", "vs", host, {"accent": "#fff"}, {})
        assert s is not None
        assert s["roomId"] == "room1"
        assert s["host"] == host
        assert s["phase"] == "playing"
        assert host in s["players"]
        gid = s["gameId"]
        assert game_kit.get_session("room1", gid) is not None
        # live join a second player
        p2 = "p" * 40
        s2 = game_kit.player_join("room1", gid, p2, {"accent": "#000"})
        assert p2 in s2["players"]
        # input routes to the sim
        game_kit.apply_input("room1", gid, p2, "paddle", 0.42)
        snap = game_kit.session_snapshot("room1", gid)
        assert snap["x"] == 0.42
        # drop leaves state for rejoin
        game_kit.player_drop("room1", gid, p2)
        assert p2 not in game_kit.get_session("room1", gid)["players"]
        print("  session lifecycle + host authority: OK")
    finally:
        fx.cleanup()


def test_create_unknown_game_returns_none():
    fx = _Fixture()
    try:
        _setup(fx)
        s = game_kit.create_session("room1", "no_such_game", "vs", "h" * 40, {}, {})
        assert s is None
        print("  create_session with unknown game -> None: OK")
    finally:
        fx.cleanup()


def test_persistence_roundtrip_drops_game_instance():
    fx = _Fixture()
    try:
        _setup(fx)
        fx.add_bundled("stub", STUB_MANIFEST, STUB_GAME_PY)
        host = "h" * 40
        s = game_kit.create_session("room1", "stub", "coop", host, {}, {})
        gid = s["gameId"]
        path = game_kit.save_session("room1", gid)
        assert os.path.exists(path)
        loaded = game_kit.load_session(gid)
        assert loaded is not None
        assert loaded["gameId"] == gid
        assert "gameInst" not in loaded  # live instance must NOT be persisted
        assert loaded["state"]["x"] == 0.5
        print("  persistence round-trip (no live instance in store): OK")
    finally:
        fx.cleanup()


def _main():
    t0 = time.time()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"game_kit: running {len(tests)} tests")
    for t in tests:
        t()
    print(f"game_kit: {len(tests)} passed in {time.time() - t0:.2f}s")


if __name__ == "__main__":
    _main()
