"""Pong LAN — a real, minimal 2-player pong sim on the games framework.

This is the FIRST real game on game_kit (not the stub). It is a host-authoritative
pong sim: the room owner's daemon runs it, players send `paddle` input, and the
host broadcasts snapshots. The web frontend (`www/index.html`, a forge-lan game)
renders the snapshot and sends normalized paddle input through the loopback feed.

Rules (vs, 2 players max):
  - Each player controls a paddle (normalized x 0..1). p1 is the bottom, p2 top.
  - The ball bounces off paddles, side walls, and the top/bottom.
  - If the ball crosses a player's boundary, the OTHER player scores.
  - First to 5 wins (phase -> "over", winner p1|p2).

The sim runs in normalized coords: x,y in [0,1]. Paddles at y=0.92 (p1) / y=0.08 (p2),
ball radius 0.02, paddle half-width 0.08.
"""

import random

import game_kit

WIN_SCORE = 5


class Game(game_kit.Game):
    name = "pong-lan"
    title = "Pong LAN"
    modes = ["vs"]
    min_players = 1
    max_players = 2

    def init(self, ctx, seed, members):
        rng = random.Random(seed)
        self.state = {
            "mode": "vs", "phase": "playing", "winner": None,
            "p1": {"x": 0.5, "score": 0, "lives": WIN_SCORE},
            "p2": {"x": 0.5, "score": 0, "lives": WIN_SCORE},
            "ball": {"x": 0.5, "y": 0.5,
                     "vx": rng.choice([-1, 1]) * 0.01, "vy": rng.choice([-1, 1]) * 0.008},
        }

    def _ball(self):
        return self.state["ball"]

    def add_player(self, fp, theme):
        # First player = p1, second = p2. Keep it simple (host is p1).
        if fp not in self.state.get("players", {}):
            self.state.setdefault("players", {})[fp] = {"seat": None, "theme": theme or {}}
            # assign seat by count
            seats = list(self.state["players"].keys())
            self.state["players"][fp]["seat"] = "p1" if seats.index(fp) == 0 else "p2"

    def remove_player(self, fp):
        self.state.get("players", {}).pop(fp, None)

    def apply_input(self, fp, action, value):
        if action != "paddle":
            return
        seat = (self.state.get("players", {}).get(fp) or {}).get("seat")
        if seat in ("p1", "p2") and isinstance(value, (int, float)):
            self.state[seat]["x"] = max(0.0, min(1.0, float(value)))

    def tick(self, dt):
        if self.state.get("phase") != "playing":
            return
        b = self._ball()
        # move
        b["x"] += b["vx"] * dt * 60
        b["y"] += b["vy"] * dt * 60
        # side walls
        if b["x"] < 0.02 or b["x"] > 0.98:
            b["vx"] = -b["vx"]
            b["x"] = max(0.02, min(0.98, b["x"]))
        # paddle bounces (p1 bottom, p2 top)
        for seat, wall in (("p1", 0.92), ("p2", 0.08)):
            p = self.state[seat]["x"]
            pad_half = 0.08
            if abs(b["y"] - wall) < 0.02 and abs(b["x"] - p) < pad_half:
                b["vy"] = -abs(b["vy"]) if seat == "p1" else abs(b["vy"])
                b["vy"] *= 1.05  # speed up slightly
        # top/bottom walls -> score the other player
        if b["y"] <= 0.0:
            self._score("p2")
        elif b["y"] >= 1.0:
            self._score("p1")

    def _score(self, scorer):
        self.state[scorer]["score"] += 1
        if self.state[scorer]["score"] >= WIN_SCORE:
            self.state["phase"] = "over"
            self.state["winner"] = scorer
        else:
            # reset ball to center
            self.state["ball"] = {"x": 0.5, "y": 0.5, "vx": 0.01, "vy": 0.008}

    def snapshot(self):
        return {
            "mode": self.state.get("mode"), "phase": self.state.get("phase"),
            "winner": self.state.get("winner"),
            "p1": {"x": self.state["p1"]["x"], "score": self.state["p1"]["score"]},
            "p2": {"x": self.state["p2"]["x"], "score": self.state["p2"]["score"]},
            "ball": dict(self._ball()),
        }

    def serialize(self):
        return dict(self.state)

    def load(self, data):
        self.state = dict(data or {})
