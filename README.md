# Lanchat

Private messaging between your own machines on your local network, as an
[Omarchy Quattro](https://github.com/omacom/omarchy/tree/quattro) shell plugin.
Install it on every computer you own and they find each other automatically —
no IP addresses, no accounts, no cloud, no shared keys to copy.

## Features

- **Automatic discovery** — machines announce themselves with a UDP broadcast
  on your LAN and appear in each other's peer list within seconds.
- **Encrypted by default** — all messaging and file transfer runs over **TLS**
  with a per-install self-signed certificate. Your device's identity is its
  cert's SHA-256 fingerprint, and peers verify each other's fingerprint when
  connecting — an imposter can't impersonate a friend.
- **Friend/handshake gate** — a stranger on the LAN can't message you.
  You add a friend with the **+** button on their peer card; the other side
  accepts or rejects the request in a notifications banner. Only confirmed
  friends (or peers you've requested) can reach you.
- **Online/offline toggle** — go invisible: stop broadcasting and drop all
  inbound messages until you're back.
- **Undoable sends** — an optional send-delay window holds a message with a
  countdown so you can undo it before it goes out.
- **LocalSend-style file attachments** — stage and send one or more files over
  the encrypted, authenticated transport. The receiver sees an incoming-file
  bar with a **Save** button; accepting streams the transfer with live
  progress, verifies the file's checksum, and saves it to your chosen folder
  (default `~/Downloads`). Transfers ride the identity-verified message
  socket, so the file comes from the friend the connection proved.
- **Lazy-loaded conversations** — long threads page in as you scroll, keeping
  the panel fluid.
- **Unfriend** — remove a friend from the thread header.
- **Edit a message** — revise a sent message with an "(edited)" marker.
- **Typing indicators** — see when a friend is typing (toggleable).
- **Read receipts** — see when a friend has read your message (toggleable).
- **Per-machine history** — each machine keeps its own copy of its threads in
  `~/.local/state/lanchat/history.json`, so messages survive reboots.
- **Group chat rooms** — create rooms, invite friends, and chat with several
  people at once. The room **owner** manages membership and per-member
  permissions; members pick a color from their own theme's palette (or match
  their theme accent live). Text always stays readable — the ink color is
  derived from the bubble background, so every color choice renders readable.
  **Group file sharing** rides the same encrypted transport as 1:1 files: a
  room file's metadata is announced to every member, and each member who wants
  it pulls the bytes directly from the sender (no relay server). Pulling
  requires being friends with the sender — room membership alone can't move
  bytes; if you're not friends yet the file shows a befriend notice and the
  Save button appears the moment you befriend them. The owner's roster shows
  who saved what; members offline at post time simply pull when they return.
- **A native bar UI** — one chat icon in the bar whose color reflects your
  status, an unread-count badge (top-right), a pending-friend-request badge
  (bottom-right), and a click opens the chat panel (peer list + thread +
  compose box). Right-click toggles online/offline or sets your status.
- **Update indicator** — a refresh-glyph button in the thread header turns the
  accent color when a newer version is available on the remote (checked read-only
  against the installed git checkout). **Click it to apply the update** (fetch +
  fast-forward the checkout; the daemon restarts and the shell reloads). A
  **Discard & update** button offers a clean install when local edits block a
  safe update.
- **One-command install & updates** — `omarchy plugin add` installs everything
  automatically (daemon, systemd unit, firewall port); the daemon restarts
  itself when its files change after an update.
- **Firewall-aware** — a **Settings → Port 4812** toggle opens/closes the
  port to your LAN (prompting via polkit), with a persistent warning when the
  port is blocked.

## Requirements

- **Omarchy Quattro** (the shell plugin system, `schemaVersion: 1`)
- **Python 3** with `openssl` (the daemon is stdlib-only; Arch/Omarchy ship both)
- Every machine must be on the same LAN/subnet (UDP broadcast + TCP)

## Install

On every machine:

```bash
omarchy plugin add https://github.com/KelpME/LanChat.git --enable --yes
```

Or by hand:

```bash
cp -r KelpME.lanchat ~/.config/omarchy/plugins/KelpME.lanchat
omarchy-shell shell rescanPlugins
omarchy plugin enable KelpME.lanchat
```

To update, click the **refresh button** in the chat thread header (it turns the
accent color when a newer version is available, and applies the update on click —
fetch + fast-forward, then the daemon restarts and the shell reloads), or
`omarchy plugin update` (fast-forward pulls the git checkout).

### Daemon runs under systemd

Since 1.4.0 the daemon is a **systemd user service**, not a child of the shell.
This is what makes it reliable and observable: it starts at login, restarts
automatically on crash (`Restart=always`), and you can always tell whether it's
running:

```bash
systemctl --user status lanchat        # is the daemon running?
systemctl --user restart lanchat       # restart it
```

**The daemon also restarts itself when its files change** (`lanchat.path` +
`lanchat-restart.service`, added in 1.5.16): after `omarchy plugin update` (or
any `git` pull) it picks up the new code automatically instead of silently
staying on the old version until you restart it manually.

**Install is a single command** (needs sudo once for the firewall step). Lanchat
needs its port (4812, udp+tcp) reachable inbound from the LAN — for discovery,
friend requests, and messaging — and Omarchy ships with the firewall closed.
`make systemd-install` handles everything: it installs the systemd unit, and
opens **UDP 4812 + TCP 4812 to your LAN subnet only** (e.g. `192.168.1.0/24`,
auto-detected) — never the internet:

```bash
make systemd-install   # prompts for sudo once: enables daemon + opens 4812 to the LAN
```

The firewall part uses **polkit (pkexec)** — it prompts for your password each
time you open/close the port, and creates **no permanent sudoers rule**. The
port is opened to your LAN subnet only. To close the port later:
`make firewall-close`. A full `make systemd-uninstall` also closes it.

From the Lanchat panel you can also toggle the port from **Settings → Port
4812**: a status dot shows whether it's open, and a single button opens/closes
it (prompting for your password via polkit each time). If the port is blocked,
a persistent warning appears in the peers-online bar.

The shell no longer spawns `server.py` directly. Instead it runs a tiny
`lanchat-bridge.py` that connects to the daemon's unix-socket control channel
(`$XDG_RUNTIME_DIR/lanchat.sock`) and proxies commands/events, so the QML
wiring is unchanged. If the daemon is down, the bridge exits and the UI shows a
clear **"Daemon not running"** warning (bar icon turns red) instead of a
misleading "0 peers online".

### Uninstalling

A full uninstall removes the systemd unit **and** all your lanchat data. Do it
in two steps — the systemd unit first, because `omarchy plugin remove` alone
would leave the daemon running under systemd:

```bash
# 1. Stop the daemon, remove the systemd unit, and wipe config/certs/history
cd ~/.config/omarchy/plugins/KelpME.lanchat && make systemd-uninstall

# 2. Remove the plugin itself
omarchy plugin remove KelpME.lanchat --yes
```

`make systemd-uninstall` removes:
- the daemon + its systemd user unit (stops, disables, deletes the unit file)
- `~/.config/omarchy/lanchat.json` (config, incl. token & friends)
- `~/.config/omarchy/lanchat-certs/` (TLS identity — a fresh install gets a new one)
- `~/.local/state/lanchat/` (message history + logs)

So after a full uninstall + reinstall, lanchat starts from a completely clean
state (new identity, empty history). If you'd rather **keep** your identity,
config, and history across a reinstall, just skip step 1's data-wipe and only
remove the systemd unit + plugin — or simply reinstall without uninstalling at
all (`omarchy plugin update` upgrades in place).

## First-run setup

On first run the daemon generates its config and TLS certificate automatically —
nothing to configure:

- Config: `~/.config/omarchy/lanchat.json`
- Certificate: `~/.config/omarchy/lanchat-certs/{cert.pem,key.pem}`

```json
{
  "token": "a-long-random-hex-token",
  "port": 4812,
  "displayName": "MegaBoardslide",
  "httpEnabled": false,
  "httpPort": 4814,
  "online": true,
  "friends": [],
  "downloadDir": "/home/you/Downloads",
  "sendDelay": 0
}
```

Install Lanchat on each machine, enable it, and the daemons start themselves.
**There's no shared secret to copy.** Discovery is open on your LAN — any
machine running Lanchat sees the others automatically. To actually chat, add
the other machine as a friend (the **+** button on their peer card); they
accept your request in the notifications banner — that handshake is the gate
that lets you talk.

The `token` in the config is now **only** for the optional HTTPS API (scripts,
agents) — it is not needed for peer messaging.

### Config options

| Key           | Default            | Meaning                                      |
|---------------|--------------------|----------------------------------------------|
| `token`       | *(random)*         | Only for the HTTPS API (not needed for chat) |
| `port`        | `4812`             | TCP + UDP port                               |
| `displayName` | friendly name      | Cosmetic name shown to peers (re-rollable)   |
| `httpEnabled` | `false`            | On/off for the HTTP API (toggle in the UI)   |
| `httpPort`    | `4814`             | Port the HTTPS API listens on                |
| `httpBind`    | `127.0.0.1`        | HTTP API bind: loopback-only by default      |
| `visibility`  | `private`          | `private` (invisible on discovery) or `open` (broadcast, discoverable) |
| `acceptRequests` | `true`          | Whether inbound friend requests are received |
| `online`      | `true`             | Presence toggle (stop receiving while off)   |
| `friends`     | `[]`               | Your confirmed/pending friends               |
| `downloadDir` | `~/Downloads`      | Where accepted attachments are saved         |
| `sendDelay`   | `0`                | Undo window in seconds (`0` = off)           |
| `apiFullAccess` | `false`          | Whether the HTTP API can read chat data      |
| `panelSize`   | `medium`           | Panel size preset: `small`, `medium`, `large`, `xl`, `full`. Custom W×H typed in the panel overrides the preset. |
| `customW`     | `0`                | Manual panel width in px (`0` = follow the preset)          |
| `customH`     | `0`                | Manual panel height in px (`0` = follow the preset)         |
| `status`      | `available`        | Your status: `available`, `dnd`, `away`, `brb`       |
| `soundEnabled`| `true`             | Play a chime when a new message arrives              |
| `typingEnabled`| `true`            | Send my typing indicator to friends                  |
| `showTyping`  | `true`             | Show friends' typing indicators                       |
| `readReceiptsEnabled` | `true`      | Send read receipts to friends                        |
| `showReadReceipts` | `true`         | Show friends' read receipts                          |

## Using the app

Lanchat is **private by default**: your machine is invisible on the network.
How you connect depends on your mode:

1. **Add a friend (private mode)** — get the friend's **cert fingerprint** from
   *their* Settings → **My ID**, then paste it into **Settings → Add friend**.
   They're added as a confirmed friend and you can message them.
   **In open mode** (switch on "Discoverable (open mode)" for a trusted LAN)
   friends show up in the peer list — click the **+** on their card to send a
   friend request.
2. **Accept a friend request** — requests appear in the notifications banner
   pinned below the peer list, showing the requester's **verified fingerprint**
   (shortened to its first 4 and last 4 hex characters, e.g. `a1b2…c3d4`, so you
   can spot-check the start and end). Click **Accept**, then **Confirm** to
   accept after checking the fingerprint matches, or **Reject** (the denial
   travels back to the sender, whose pending request clears).
   **Retract a request you sent** — while a request is still waiting to be
   accepted, press **Cancel** on it in the notifications banner to withdraw it
   on both sides.
3. **Send a file** — click the paperclip in the compose box, pick one or more
   files. The receiver sees an **"Incoming file"** bar at the bottom of the
   conversation with a **Save** button; accepting streams the transfer with
   progress, verifies it, and saves it to the download folder.
4. **Undo a send** — turn on **Undo delay** in Settings (seconds). Sent
   messages are held with a countdown; hit undo before it hits zero and the
   message is cancelled.
5. **Go offline** — toggle **Online** in Settings to stop receiving.
6. **Clear a chat / delete a message** — via the thread controls (local-only).
7. **Edit a message** — hover an outgoing message and click the **pencil** to
   revise it; it's marked "(edited)".
8. **Unfriend** — click **Unfriend** in the thread header to remove a friend.
9. **Help** — the **?** button in the Settings header opens the built-in help
   page (`HELP.html`). Hover any setting for a short explanation.
10. **Group rooms** — open **Rooms** (under the peer list) and press **＋** to
    create one; you become its **owner**. Invite members: type a friend's
    fingerprint in the roster's **Add** field (or let a member you've granted
    "can add" propose people — the invite still comes from you, the owner).
    They see the invite and click **Join**. Selecting a room shows the
    **roster** (between the divider and the chat) where you manage members and
    everyone picks **My color** from the theme palette. Chat flows directly
    between members; the owner is only needed to change membership.
    **Owner offline** → the room keeps chatting but membership/permissions are
    frozen ("Host offline — changes frozen"). **Share a file** — same
    paperclip compose as 1:1; every member sees the file, and pulling it
    requires being a friend of the sender (a notice offers to befriend
    otherwise; Save appears once you are). Files exist only while the sender
    is online — a member who missed it pulls when the sender returns.

## HTTP API (optional)

Lanchat ships a small HTTPS API for scripts, agents, or curl. **Off by default**;
toggle it from the **API** switch in Settings. All endpoints except `/health`
require this machine's token (from `lanchat.json`).

| Method | Path                | Auth        | What it does                                   |
|--------|---------------------|-------------|------------------------------------------------|
| `GET`  | `/health`           | —           | Liveness probe (`{"ok":true}`)                 |
| `GET`  | `/peers?token=…`    | token       | List online peers                             |
| `GET`  | `/messages?token=…` | token       | This machine's message history                |
| `POST` | `/send`             | token (body)| Send a message to a peer                      |

**Rooms are NOT exposed over the HTTP API.** Room management (create, invite,
membership, permissions, colors) and room files run only through the daemon's
authenticated socket protocol — the trusted-peer channel — so a leaked API
token can't alter room membership or pull room files. Room state changes
surface as events on the same JSON stream the UI consumes (`room-list`,
`room-state`, `room-invite`, `room-file-status`).

**API access mode** — the **Agent full access** toggle in Settings controls
whether the API can *read* chat data:
- **On:** `/peers` and `/messages` work (full read access).
- **Off (default):** the API is **send-only** — the agent can send messages to
  your friends but cannot read history or list peers; those read endpoints
  return `403`. (File transfers between friends do **not** use this API — they
  ride the encrypted message socket — so saving a received file is never gated
  by this toggle.)

Send a message:

```bash
curl -k -X POST https://localhost:4814/send \
  -H 'Content-Type: application/json' \
  -d '{"token":"<TOKEN>","to":"<peer-id>","text":"hello from the shell"}'
```

List peers:

```bash
curl -k 'https://localhost:4814/peers?token=<TOKEN>'
```

> The API serves **HTTPS** with the per-install self-signed cert, so `-k` skips
> verification for local scripting. The `to` id is a peer's cert fingerprint
> (not its hostname). By default the API binds **loopback only** (`127.0.0.1`);
> set `httpBind: "0.0.0.0"` in `lanchat.json` to expose it to the LAN (then any
> machine with the token can reach it).

## Security model

- **Transport** — TLS-encrypted messaging and file transfer (per-install
  self-signed cert).
- **Identity** — the cert's SHA-256 fingerprint is the device's true identity,
  decoupled from the cosmetic display name. Renaming never breaks a friend link.
- **Peer verification** — when connecting, the peer's cert fingerprint is
  checked against the one you friended, preventing impersonation. File
  transfers ride that same verified socket, so the sender is the friend the
  connection proved. On **inbound** connections (the peer dials you), identity
  is proven by challenge-response (1.2.0): the dialer must sign a random nonce
  with the private key matching its claimed fingerprint before any of its
  messages are trusted. A stranger who harvests a friend's fingerprint
  (broadcast in the open) but not its key cannot impersonate them.
- **Access** — discovery is open (any LAN machine is visible); the
  friend/handshake gates messaging. Only confirmed friends (or peers you've
  requested) can reach you.
- **Presence** — the online toggle stops broadcasts and drops inbound while off.
- **Optional HTTP API** — token-authenticated, **loopback-only by default**
  (1.2.1+; `httpBind: "0.0.0.0"` opts into LAN exposure), with rate limits on
  `/send` and a brute-force guard on failed auth, plus a request-body cap.
- **Transport hardening** (1.2.2) — per-connection buffered input is bounded
  (512 KB) and concurrent inbound connections are capped (64), so a flooding
  LAN peer can't exhaust memory or threads.
- **At-rest encryption** (1.2.3) — message history is AES-256-GCM encrypted on
  disk with a dedicated 0600 key, so `history.json` isn't readable as
  plaintext. This protects the file in isolation (a backup, a copy, sync);
  it is not a defense against full compromise of the machine.
- **Visibility & requests** (1.3) — safe on public networks by default.
  `visibility` is `private` (invisible: no discovery broadcast/scan, and hello
  probes are ignored) unless you switch to `open` for a trusted LAN. Inbound
  friend requests are gated by `acceptRequests` and, when shown, carry the
  requester's **verified cert fingerprint** for you to confirm matches before
  accepting. Connect to friends in private mode by adding their fingerprint
  directly.

> The cert is self-signed (no CA), which is appropriate for LAN peer-to-peer.
> Visibility is `private` by default — you're invisible on discovery until you
> switch to `open`. In `open` mode any machine on your LAN can see you and send
> a friend request; they can't message you until you accept, and their verified
> cert fingerprint identifies them. The `token` in config only protects the
> optional HTTPS API, which is loopback-only by default.

## How it works

The plugin is three pieces:

- **`shared/Lanchat.qml`** — a QML singleton that owns the bridge process and
  all shared state. A true singleton, so exactly one bridge runs no matter how
  many bar surfaces or entry points exist.
- **`server.py`** — a stdlib-only Python daemon: TLS TCP server for messages,
  UDP broadcast discovery, heartbeat/expiry for online status, attachment
  file transfer (over the message socket), and JSON persistence. Runs under
  systemd (`systemd/lanchat.service`).
- **`lanchat-bridge.py`** — a stdlib-only proxy the shell spawns: it connects
  to the daemon's unix-socket control channel and forwards commands/events, so
  the QML's Process-based wiring is unchanged.
- **`Service.qml` / `BarWidget.qml` / `Panel.qml`** — the always-on service,
  the bar icon + badge, and the chat panel.

Discovery: each machine broadcasts a `hello` every three seconds and listens on
the same port. Peers reply with a `pong`, and both sides track each other until
heartbeats stop (a peer expires after ~6 seconds). Messaging is a TLS connection
to the peer, authenticated by the peer's cert fingerprint (the shared token is
used only by the optional HTTPS API).

The QML and the daemon talk over newline-delimited JSON — commands on the
bridge's stdin, events on its stdout (the bridge proxies both to/from the
daemon's unix socket):

```
stdin  →  {"cmd":"send","to":"<id>","text":"..."}  {"cmd":"history"}  {"cmd":"list"}
stdout →  {"event":"ready"|"peer"|"peer-gone"|"message"|"history"|"peers"|"error"|"notice", ...}
```

## Development

Validate your copy before installing:

```bash
omarchy plugin validate .
```

Saving any file under `~/.config/omarchy/plugins/KelpME.lanchat/` reloads the
plugin code automatically; the daemon is supervised by systemd (it restarts
itself on crash). Run the offline end-to-end suites (two isolated instances):

```bash
python3 server.py
python3 test_server.py       # core: discovery, TLS delivery, auth, HTTPS API
python3 test_friends.py      # friend/handshake + online-toggle over TLS
python3 test_persistent.py   # persistent connections, reconnect/hold/dedupe
python3 test_features.py     # lazy-load, clear/delete, attachment plumbing, config
python3 test_attachments.py  # recipient file receipt over the message socket: accept, checksum, trust gate
python3 test_discovery_visibility.py  # broadcast side of the private/open visibility flip
python3 test_systemd_control.py      # systemd unix-socket control channel + bridge
python3 test_cert_reload.py          # served cert tracks a regenerated cert (no stale fingerprint)
python3 test_udp_resilience.py       # UDP listener survives bad packets (no silent thread death)
python3 test_units.py                # unit-level: naming, filenames, safe sanitize
python3 test_groups.py               # rooms: host-authoritative state, mesh chat, room files, trust gate
```

> The plugin reloads QML but not always compiled types — after editing QML,
> `rm -rf ~/.cache/quickshell/qmlcache && omarchy-restart-shell`.

## License

MIT — see `LICENSE`.
