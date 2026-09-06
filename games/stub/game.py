"""Stub game — the minimal Game subclass every add-on game looks like.

Ships bundled so the games-platform wire transport (and future games) have a
real, loadable backend to exercise in tests and live. It is deliberately the
smallest valid implementation: it only tracks a paddle position and a score,
so tests can drive join/drop/input/snapshot/persistence without gameplay.

A real game replaces this folder's manifest.json + game.py + web/ (see
plans/GAMES-AI-REFERENCE.md and the game-authoring skill, Phase 6).
"""

import game_kit


class Game(game_kit.Game):
    name = "stub"
    title = "Stub (framework test game)"
    modes = ["vs"]
    min_players = 1
    max_players = 8

    def init(self, ctx, seed, members):
        self.state = {
            "x": 0.5, "score": 0, "lives": 5, "phase": "playing",
            "players": {}, "seed": seed,
        }

    def add_player(self, fp, theme):
        self.state.setdefault("players", {})[fp] = theme or {}

    def remove_player(self, fp):
        self.state.get("players", {}).pop(fp, None)

    def apply_input(self, fp, action, value):
        if action == "paddle":
            self.state["x"] = value
        elif action == "score":
            self.state["score"] = int(value or 0)

    def tick(self, dt):
        pass  # stateless sim; real games advance physics here

    def snapshot(self):
        return {"x": self.state.get("x", 0.5), "score": self.state.get("score", 0),
                "phase": self.state.get("phase", "playing"),
                "players": list(self.state.get("players", {}).keys())}

    def serialize(self):
        return dict(self.state)

    def load(self, data):
        self.state = dict(data or {})
