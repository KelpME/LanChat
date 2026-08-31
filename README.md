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
  (default `~/Downloads`). Downloads are pinned to the sender's cert
  fingerprint, so the file comes from who you think it does.
- **Lazy-loaded conversations** — long threads page in as you scroll, keeping
  the panel fluid.
- **Unfriend** — remove a friend from the thread header.
- **Edit a message** — revise a sent message with an "(edited)" marker.
- **Typing indicators** — see when a friend is typing (toggleable).
- **Read receipts** — see when a friend has read your message (toggleable).
- **Per-machine history** — each machine keeps its own copy of its threads in
  `~/.local/state/lanchat/history.json`, so messages survive reboots.
- **A native bar UI** — one chat icon in the bar whose color reflects your
  status, an unread-count badge, and a click opens the chat panel (peer list +
  thread + compose box). Right-click toggles online/offline or sets your status.

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

To update, `omarchy plugin update` (fast-forward pulls the git checkout).

### Daemon runs under systemd

Since 1.4.0 the daemon is a **systemd user service**, not a child of the shell.
This is what makes it reliable and observable: it starts at login, restarts
automatically on crash (`Restart=always`), and you can always tell whether it's
running:

```bash
systemctl --user status lanchat        # is the daemon running?
systemctl --user restart lanchat       # restart it
```

**Install is fully automatic — no manual step, no sudo.** On first run the
shell runs `lanchat-ensure-systemd.py`, which installs the unit (shipped at
`systemd/lanchat.service`) into `~/.config/systemd/user/`, enables it, and
starts it. All user-level, so no root is needed. (If you ever need to do it by
hand: `make systemd-install`.)

The shell no longer spawns `server.py` directly. Instead it runs a tiny
`lanchat-bridge.py` that connects to the daemon's unix-socket control channel
(`$XDG_RUNTIME_DIR/lanchat.sock`) and proxies commands/events, so the QML
wiring is unchanged. If the daemon is down, the bridge exits and the UI shows a
clear **"Daemon not running"** warning (bar icon turns red) instead of a
misleading "0 peers online".

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
   pinned below the peer list, showing the requester's **verified fingerprint**.
   Click **Accept**, then **Confirm** to accept after checking the fingerprint
   matches, or **Reject**.
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

**API access mode** — the **Agent full access** toggle in Settings controls
whether the API can *read* chat data:
- **On:** `/peers` and `/messages` work (full read access).
- **Off (default):** the API is **send-only** — the agent can send messages to
  your friends but cannot read history or list peers; those read endpoints
  return `403`. (Serving an accepted file transfer is peer-to-peer, not script
  read-access, so downloads are not gated by this toggle.)

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
> (not its hostname). The API binds all interfaces, reachable by any machine
> with the token.

## Security model

- **Transport** — TLS-encrypted messaging and file transfer (per-install
  self-signed cert).
- **Identity** — the cert's SHA-256 fingerprint is the device's true identity,
  decoupled from the cosmetic display name. Renaming never breaks a friend link.
- **Peer verification** — when connecting, the peer's cert fingerprint is
  checked against the one you friended, preventing impersonation. The same
  check pins file downloads to the sender's identity. On **inbound**
  connections (the peer dials you), identity is proven by challenge-response
  (1.2.0): the dialer must sign a random nonce with the private key matching
  its claimed fingerprint before any of its messages are trusted. A stranger
  who harvests a friend's fingerprint (broadcast in the open) but not its key
  cannot impersonate them.
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
  serving, and JSON persistence. Runs under systemd (`systemd/lanchat.service`).
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
python3 test_attachments.py  # recipient file receipt: accept, checksum, auth pinning
python3 test_discovery_visibility.py  # broadcast side of the private/open visibility flip
python3 test_systemd_control.py      # systemd unix-socket control channel + bridge
```

> The plugin reloads QML but not always compiled types — after editing QML,
> `rm -rf ~/.cache/quickshell/qmlcache && omarchy-restart-shell`.

## License

MIT — see `LICENSE`.
