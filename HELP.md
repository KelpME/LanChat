# Lanchat Help

Private messaging between your own machines on your local network, as an
Omarchy Quattro shell plugin. Install it on each machine, enable it, and they
find each other automatically — no accounts, no cloud, no shared secrets to
copy.

## How it connects

**Discovery is open on your LAN.** Any machine running Lanchat broadcasts its
presence and shows up in everyone's peer list. No token or setup is needed to
*see* other machines.

**The friend handshake is the gate.** Seeing a machine doesn't mean it can
message you. To actually talk:

1. Pick a peer and send a message. Your first message to them is a **friend
   request**.
2. They see it in their chat with **Accept / Reject** buttons.
3. Accepting completes the handshake — from then on you message freely.

A stranger can send a friend request, but can't message you until you accept.
Their identity is locked to their certificate fingerprint, so they can't
impersonate a friend.

## Encrypted by default

Every message and file transfer runs over TLS with a per-install self-signed
certificate. Your device's identity is that cert's fingerprint — permanent and
independent of your display name, so you can re-roll your name without breaking
anything.

## The bar icon

Click the chat icon in the bar to open Lanchat. The badge shows unread messages
and how many peers are online. The **icon's color reflects your status**:
white = Available, red = Do Not Disturb, orange = Away, yellow = Be Right Back.
**Right-click** the icon for a menu: toggle online/offline, or pick a status.

## The chat panel

- **Left column** — your online peers (each with a status dot), the peer count,
  and Settings.
- **Right column** — the selected peer's conversation and the compose box.
- **Draggable divider** — drag the thin bar between the peer list and the chat
  to resize the columns.
- **Paperclip** — attach and send a file. The receiver gets an **Incoming
  file** bar with a **Save** button; accepting saves it to your download
  folder.
- **Copy button** (hover a message) — copies that message to the clipboard.
- **Lazy-load** — long conversations load older messages as you scroll, so the
  panel stays fluid.

## Settings

- **Name** — your display name, with a re-roll button for a fresh friendly
  name.
- **Online** — go offline to stop broadcasting and drop inbound messages.
- **Status** — set your status: **Available**, **DND**, **Away**, or **BRB**.
  Shown to friends on their peer list and via the bar icon color.
- **API** — the HTTP API for scripts/agents (see below).
- **Agent full access** — when the API is on, whether the agent can *read* your
  chat data or only *send*.
- **Undo delay** — hold messages for N seconds so you can undo them before they
  send.
- **Save to** — the folder accepted files go to (default `~/Downloads`).
- **Panel size** — choose **S**, **M**, **L**, **XL**, or **F** (full-screen)
  for the panel window size.
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
- With **Agent full access** off, `/peers`, `/messages`, and file downloads
  return 403 (send-only). The agent can still send to friends.
- HTTPS uses the self-signed cert, so scripts skip verification (`curl -k`).

## Files & privacy

- Config: `~/.config/omarchy/lanchat.json`
- TLS identity: `~/.config/omarchy/lanchat-certs/`
- Message history: `~/.local/state/lanchat/history.json` (per machine)

History stays per-machine and is never synced to a server.
