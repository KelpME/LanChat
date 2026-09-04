# Lanchat Help

Private messaging between your own machines on your local network, as an
Omarchy Quattro shell plugin. Install it on each machine, enable it, and they
find each other — no accounts, no cloud, no shared secrets to copy.

## How it connects

Lanchat is **invisible by default** — safe on public networks out of the box.
Your visibility is controlled by the **Discoverable** switch in **Settings**
(it's **Off** by default):

- **Discoverable Off:** your machine is **invisible** on the network. It
  doesn't broadcast its presence, doesn't reply to discovery probes, so
  strangers on the same wifi can't find you or even confirm you exist.
- **Discoverable On:** you broadcast your presence and are discoverable, and
  anyone on the LAN can send you a friend request. Use this on **trusted
  networks** (your own machines at home).

To talk to someone:

- **When Discoverable is off** — add them directly by their **cert
  fingerprint**. Get their fingerprint from *their* Settings → **My ID**,
  paste it into **Settings → Add friend**, and they're added as a confirmed
  friend.
- **When Discoverable is on** — they show up in your peer list; click **+** on
  their card to send a friend request, or they send one to you.

**The friend handshake is the gate.** Seeing a machine doesn't mean it can
message you. A stranger can *ask* to be friends, but can't message you until
you accept.

**Verified friend requests.** Every incoming friend request shows the
requester's **verified certificate fingerprint** — shortened to its first 4
and last 4 hex characters (`a1b2…c3d4`) so you can spot-check the start and end
at a glance without the full 64-character string overflowing the panel. When
you accept, you confirm the fingerprint matches the person you expect — so a
stranger can't impersonate a friend by faking a name. Their identity is locked
to their certificate, and they can't forge yours.

**Retracting a request.** If you sent a friend request that's still waiting to
be accepted, open the friend-request notifications and press **Cancel** on it.
That withdraws the request on both sides: the recipient's incoming banner is
cleared (so they can't Accept a request you've retracted) and no friendship is
formed.

**Denying a request.** When you decline an incoming request with **Reject**, the
denial travels back to the sender automatically — their "Waiting to accept"
banner clears and they don't add you as a friend. If you sent a request that
was denied, it simply disappears from your pending requests.

## Encrypted by default

Every message and file transfer runs over TLS with a per-install self-signed
certificate. Your device's identity is that cert's fingerprint — permanent and
independent of your display name, so you can re-roll your name without breaking
anything.

When another machine connects to you, it must prove it holds the private key
for the fingerprint it claims (it signs a random challenge). Without the
matching key a stranger can't forge messages from you. All your machines should
run the same recent version.

## Settings explained

- **Discoverable (open mode)** — the visibility switch. **Off** (the default)
  keeps you invisible on the network; **on** makes you discoverable for trusted
  LANs. Existing friends keep working either way — visibility only controls how
  *new* people find you.
- **Receive friend requests** — whether incoming friend requests are shown to
  you at all. **On** (the default): requests appear in the notifications banner
  with the requester's verified fingerprint to confirm. **Off**: all incoming
  requests are rejected silently.
- **Add friend** — paste a cert fingerprint here to add a confirmed friend
  directly (how you connect when Discoverable is off).
- **Agent full access** — for the optional HTTP API. On = an agent can read
  your chats/peers/files; off = send-only. The API itself is loopback-only.

## The bar icon

Click the chat icon in the bar to open Lanchat. An **unread-count badge**
(top-right of the icon) shows new messages, and a **pending-friend-request
badge** (bottom-right, accent-colored) shows friend requests waiting to be
accepted (the icon also lights up when peers are online). The **icon's
color reflects your status**: white = Available, red = Do Not Disturb, orange
= Away, yellow = Be Right Back. **Right-click** the icon for a menu: toggle
online/offline, or pick a status.

## The chat panel

- **Left column** — your peers (each with a status dot; friends stay visible
  even when offline, shown grayed out), the peer count, the **Rooms** list,
  and Settings.
- **Right column** — the selected peer's conversation and the compose box.
  With a **room** open, a **roster** column sits between the divider and the
  chat showing every member.
- **Update indicator** — when a chat is open, a refresh-glyph button sits in the
  thread header's left corner. It turns the accent color when a newer version is
  available on the remote (checked read-only against the installed git checkout).
  **Click it to apply the update** (fetch + fast-forward the git checkout); the
  daemon restarts automatically and the shell reloads so the new UI appears. If
  local uncommitted edits block a safe update, a **Discard & update** button
  offers a clean install of the latest version instead.
- **Draggable divider** — drag the thin bar between the peer list and the chat
  to resize the columns.
- **Paperclip** — attach and send one or more files. The receiver gets an
  **Incoming file** bar with a **Save** button; accepting streams the transfer
  with progress, verifies it, and saves it to your download folder. Transfers
  ride the encrypted, identity-verified message socket, so the file comes from
  who you think it does.
- **Copy button** (hover a message) — copies that message to the clipboard.
- **Edit a message** (hover an outgoing message) — click the pencil to revise
  it; it's marked "(edited)".
- **Unfriend** — the **Unfriend** button in the thread header removes a friend.
- **Typing / read state** — see "… is typing" and a ✓ when a message is read
  (both toggleable).
- **Lazy-load** — long conversations load older messages as you scroll, so the
  panel stays fluid.

## Rooms (group chat)

- **Create** — expand **Rooms** under the peer list and press **＋**. You become
  the room's **owner**: your machine holds the member list, permissions, and
  colors.
- **Invite** — with your room open, type a friend's fingerprint in the roster's
  **Add** field. They see the invite under Rooms and click **Join**. Members
  you've granted **can add** may propose people; the invite still comes from
  you (the owner), and both sides must already be friends of you — the room
  rides the same trusted links as chat.
- **Roster** — selecting a room shows every member between the divider and the
  chat. As owner you can **✕ remove** a member and flip their **can add**
  permission per person. Everyone sees the owner marked with a ★.
- **Member colors** — pick **My color** from the theme palette swatches (every
  color is offered — nothing is filtered), or **Match my theme accent** to
  follow your machine's theme live. The choice is stored as a palette token
  plus its color, so everyone sees the same color; text ink adapts to the
  bubble automatically so every choice stays readable.
- **Owner colors kill-switch** — in Settings ("Rooms: members pick their
  colors", visible when you own a room): turning it off renders the room with
  the standard theme colors instead of member colors.
- **Chat & files are direct** — messages and file bytes flow straight between
  members over their own encrypted links; the owner is never a relay. If the
  owner is offline the room keeps chatting, but membership changes freeze
  ("Host offline — changes frozen") until they return.
- **Room files** — attach via the paperclip exactly like 1:1 chat. Every member
  sees the file's card. Pulling the bytes requires being **friends with the
  sender**: if you aren't yet, the card shows a befriend notice, and the Save
  button appears as soon as you befriend them (no re-request needed). The
  sender sees who saved the file; someone offline at send time pulls it when
  they're back (the file exists only while the sender is online).

## Settings

- **Name** — your display name, with a re-roll button for a fresh friendly
  name.
- **Online** — go offline to stop broadcasting and drop inbound messages.
- **Status** — set your status: **Available**, **DND**, **Away**, or **BRB**.
  Shown to friends on their peer list and via the bar icon color.
- **Message sound** — play a soft chime when a new message arrives.
- **Let friends see me typing** — when on, your friends see "typing…" while you type.
- **Show when friends are typing** — when on, you see "[friend] is typing…".
- **Let friends see when I've read** — when on, friends see a ✓ on read messages.
- **Show when friends have read** — when on, you see a ✓ on read messages.
- **API** — the HTTP API for scripts/agents (see below).
- **Agent full access** — when the API is on, whether the agent can *read* your
  chat data or only *send*.
- **Undo delay** — hold messages for N seconds so you can undo them before they
  send.
- **Save to** — the folder accepted files go to (default `~/Downloads`).
- **Rooms: members pick their colors** — owner-level room setting (shown when
  you own at least one room). Off = rooms you own render with the standard
  theme colors instead of member-picked colors.
- **Panel size** — choose **S**, **M**, **L**, **XL**, or **F** (full-screen)
  for the panel window size. S/M/L/XL scale as independent fractions of each
  screen axis — width vs height grow separately. Width goes 1/2 → 2/3 → 4/5 →
  9/10 of screen width, height goes 0.45 → 3/5 → 3/4 → 0.85 of screen height,
  growing incrementally. **F** fills the whole screen.
- **Version** — the installed version.

## The API (for scripts / agents)

An HTTPS server (default port 4814) so tools or agents can send messages and
read state. **Off by default** — toggle **API** in Settings. All endpoints
except `/health` require the token in `~/.config/omarchy/lanchat.json`.

| Method | Path                | Auth        | What it does                                   |
|--------|---------------------|-------------|------------------------------------------------|
| `GET`  | `/health`           | —           | Liveness probe                                 |
| `GET`  | `/peers?token=…`    | token       | List online peers                             |
| `GET`  | `/messages?token=…` | token       | Message history                               |
| `POST` | `/send`             | token (body)| Send a message to a peer                      |

- The peer id is its cert fingerprint (not a hostname).
- The agent can only message **friends** — the friend gate applies to the API
  too.
- With **Agent full access** off, `/peers` and `/messages` return 403
  (send-only). The agent can still send to friends. (File transfers between
  friends do **not** use this API — they ride the encrypted message socket —
  so saving a received file works regardless of this toggle.)
- HTTPS uses the self-signed cert, so scripts skip verification (`curl -k`).

## Files & privacy

- Config: `~/.config/omarchy/lanchat.json`
- TLS identity: `~/.config/omarchy/lanchat-certs/`
- Message history: `~/.local/state/lanchat/history.json` (per machine)

## The daemon & troubleshooting

Lanchat's daemon runs as a **systemd user service** (not a child of the shell),
so it starts at login, restarts automatically if it crashes, and survives shell
restarts. If it's ever down, the bar icon turns **red** and the peer list shows
**"⚠ Daemon not running — lanchat is offline"** instead of a misleading
"0 peers online".

Check or restart it from a terminal:

```bash
systemctl --user status lanchat     # is it running? (active = yes)
systemctl --user restart lanchat    # restart it
```

If a machine isn't showing up in your peer list:

1. **Is its daemon running?** `systemctl --user status lanchat` on that machine
   (or look for the red bar icon). A machine that's "Discoverable" but whose
   daemon is down won't broadcast at all.
2. **Is its firewall port open?** Omarchy ships with the firewall closed, and
   lanchat needs port 4812 (udp+tcp) reachable inbound from the LAN for
   discovery, friend requests, and messaging. Run `make systemd-install` (or
   `make firewall-open`) once on that machine — it opens 4812 to the LAN only
   (it will prompt for your password via polkit). You can also toggle it from
   **Settings → Port 4812** in the panel.
3. **Is it on the same network?** Both machines must be on the same LAN
   (same subnet) for discovery broadcasts to reach each other.
4. **Is Discoverable on?** In **Settings**, switch on **Discoverable (open
   mode)** — it's off by default. Peers appear within ~3 seconds of it being on.
5. **Both on the same version?** Each machine updates itself from the
   **refresh button** in the chat thread header (when it shows an update
   available), or with `omarchy plugin update KelpME.lanchat --yes`, then
   restart the shell.

To **fully uninstall** (remove the daemon, systemd unit, and all lanchat data):
`cd ~/.config/omarchy/plugins/KelpME.lanchat && make systemd-uninstall`, then
`omarchy plugin remove KelpME.lanchat --yes`.

History stays per-machine and is never synced to a server.

## For contributors: Panel.qml layout

`Panel.qml` is decomposed into components under `shared/` (see
`shared/qmldir`). The panel root stays the state owner; children receive what
they need via explicit properties and call back via signals.

- `shared/Lanchat.qml` — shared-state singleton (peers, rooms, history, settings).
- `shared/PeerList.qml` — left column: peers, onboarding banner, friend requests, online section.
- `shared/ChatThread.qml` — 1:1 conversation view (uses `ChatMessage`).
- `shared/ChatMessage.qml` — 1:1 message bubble delegate (incl. attachment row).
- `shared/RoomView.qml` — room conversation view (uses `RoomMessage`).
- `shared/RoomMessage.qml` — room text/file bubbles + member chips + delivery statuses.
- `shared/RoomListSection.qml` — rooms list, create-dialog, invite flow, colors.
- `shared/ComposeBox.qml` — compose box, staged-attachment preview, in-input alert.
- `shared/SettingsPanel.qml` — the full settings block (identity, presence, colors, chat, appearance, agents, reachability, developer).

Before changing any of these, run the gate: `scripts/qml_gate/run.sh`, and
verify with `python3 scripts/check_qml.py` + `qmllint` (zero errors). Compare
against the pre-modularization baseline (`Panel.qml` at commit `d6ff65d^`)
when validating behavior — the A/B baseline rule from
`plans/PANEL-MODULARIZATION.md`.
