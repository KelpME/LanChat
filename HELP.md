# Lanchat Help

Private messaging between your own machines on your local network, as an
Omarchy Quattro shell plugin. Install it on each machine, enable it, and they
find each other — no accounts, no cloud, no shared secrets to copy.

## How it connects

Lanchat is **private by default** — safe on public networks out of the box.

- **Private mode (default):** your machine is **invisible** on the network. It
  doesn't broadcast its presence, doesn't reply to discovery probes, so
  strangers on the same wifi can't find you or even confirm you exist.
- **Open mode:** you broadcast your presence and are discoverable, and anyone
  on the LAN can send you a friend request. Use this on **trusted networks**
  (your own machines at home).

To talk to someone:

- **In private mode** — add them directly by their **cert fingerprint**. Get
  their fingerprint from *their* Settings → **My ID**, paste it into
  **Settings → Add friend**, and they're added as a confirmed friend.
- **In open mode** — they show up in your peer list; click **+** on their card
  to send a friend request, or they send one to you.

**The friend handshake is the gate.** Seeing a machine doesn't mean it can
message you. A stranger can *ask* to be friends, but can't message you until
you accept.

**Verified friend requests.** Every incoming friend request shows the
requester's **verified certificate fingerprint**. When you accept, you confirm
the fingerprint matches the person you expect — so a stranger can't impersonate
a friend by faking a name. Their identity is locked to their certificate, and
they can't forge yours.

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
  directly (how you connect in private mode).
- **Agent full access** — for the optional HTTP API. On = an agent can read
  your chats/peers/files; off = send-only. The API itself is loopback-only.

## The bar icon

Click the chat icon in the bar to open Lanchat. An **unread-count badge** shows
new messages (the icon also lights up when peers are online). The **icon's
color reflects your status**: white = Available, red = Do Not Disturb, orange
= Away, yellow = Be Right Back. **Right-click** the icon for a menu: toggle
online/offline, or pick a status.

## The chat panel

- **Left column** — your peers (each with a status dot; friends stay visible
  even when offline, shown grayed out), the peer count, and Settings.
- **Right column** — the selected peer's conversation and the compose box.
- **Draggable divider** — drag the thin bar between the peer list and the chat
  to resize the columns.
- **Paperclip** — attach and send one or more files. The receiver gets an
  **Incoming file** bar with a **Save** button; accepting streams the transfer
  with progress, verifies it, and saves it to your download folder. Transfers
  are pinned to the sender's certificate, so the file comes from who you think
  it does.
- **Copy button** (hover a message) — copies that message to the clipboard.
- **Edit a message** (hover an outgoing message) — click the pencil to revise
  it; it's marked "(edited)".
- **Unfriend** — the **Unfriend** button in the thread header removes a friend.
- **Typing / read state** — see "… is typing" and a ✓ when a message is read
  (both toggleable).
- **Lazy-load** — long conversations load older messages as you scroll, so the
  panel stays fluid.

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
  (send-only). The agent can still send to friends. (Serving an accepted file
  transfer is peer-to-peer, not script read-access, so downloads work
  regardless of this toggle.)
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
2. **Is it on the same network?** Both machines must be on the same LAN
   (same subnet) for discovery broadcasts to reach each other.
3. **Is Discoverable on?** In **Settings**, switch on **Discoverable (open
   mode)** — it's off by default. Peers appear within ~3 seconds of it being on.
4. **Both on the same version?** Update each machine with
   `omarchy plugin update KelpME.lanchat --yes`, then restart the shell.

History stays per-machine and is never synced to a server.
