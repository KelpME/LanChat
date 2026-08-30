# KelpME.lanchat

Private messaging between your own machines on your local network, as an
[Omarchy Quattro](https://github.com/omacom/omarchy/tree/quattro) shell plugin.
Install it on every computer you own, set them all to the same shared secret,
and they find each other automatically — no IP addresses, no accounts, no cloud.

## Features

- **Automatic discovery** — machines announce themselves with a UDP broadcast
  on your LAN and appear in each other's peer list within seconds.
- **Encrypted by default** — all messaging and file transfer runs over **TLS**
  with a per-install self-signed certificate. Your device's identity is its
  cert's SHA-256 fingerprint, and peers verify each other's fingerprint when
  connecting — an imposter can't impersonate a friend.
- **Friend/handshake gate** — a stranger with the token can't message you.
  Starting a conversation sends a friend request; the other side accepts or
  rejects it in the chat. Only confirmed friends (or peers you've requested)
  can reach you.
- **Online/offline toggle** — go invisible: stop broadcasting and drop all
  inbound messages until you're back.
- **Undoable sends** — an optional send-delay window holds a message with a
  countdown so you can undo it before it goes out.
- **LocalSend-style file attachments** — send single files over the encrypted
  transport; the receiver accepts them and they're saved to your chosen folder
  (default `~/Downloads`).
- **Lazy-loaded conversations** — long threads page in as you scroll, keeping
  the panel fluid.
- **Per-machine history** — each machine keeps its own copy of its threads in
  `~/.local/state/lanchat/history.json`, so messages survive reboots.
- **A native bar UI** — one chat icon in the bar, an online-peer + unread
  badge, and a click opens the chat panel (peer list + thread + compose box).

## Requirements

- **Omarchy Quattro** (the shell plugin system, `schemaVersion: 1`)
- **Python 3** with `openssl` (the daemon is stdlib-only; Arch/Omarchy ship both)
- Every machine must be on the same LAN/subnet (UDP broadcast + TCP)

## Install

On every machine:

```bash
omarchy plugin add https://github.com/<you>/KelpME.lanchat.git --enable --yes
```

Or by hand:

```bash
cp -r KelpME.lanchat ~/.config/omarchy/plugins/KelpME.lanchat
omarchy-shell shell rescanPlugins
omarchy plugin enable KelpME.lanchat
```

To update, `omarchy plugin update` (fast-forward pulls the git checkout).

## First-run setup: the shared secret

On first run the daemon generates its config and TLS certificate:

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

Do this **once**, on every machine:

1. Start Lanchat on your first machine, then **copy the `token` value** from
   that machine's `~/.config/omarchy/lanchat.json`.
2. On every other machine, paste the **same token** into the same field. Set
   `displayName` to whatever you want that machine called (or leave it — it
   gets a friendly skate-trick name you can re-roll in the UI).
3. Restart the shell (`omarchy-restart-shell`) or rescan plugins.

Machines only see peers that share the same token. Change it anywhere and you
must update it everywhere. The TLS cert and identity are separate from the
token — those stay per-machine and are what friends actually trust.

### Config options

| Key           | Default            | Meaning                                      |
|---------------|--------------------|----------------------------------------------|
| `token`       | *(random)*         | Shared secret — must match on every machine  |
| `port`        | `4812`             | TCP + UDP port (same on every machine)       |
| `displayName` | friendly name      | Cosmetic name shown to peers (re-rollable)   |
| `httpEnabled` | `false`            | On/off for the HTTP API (toggle in the UI)   |
| `httpPort`    | `4814`             | Port the HTTPS API listens on                |
| `online`      | `true`             | Presence toggle (stop receiving while off)   |
| `friends`     | `[]`               | Your confirmed/pending friends               |
| `downloadDir` | `~/Downloads`      | Where accepted attachments are saved         |
| `sendDelay`   | `0`                | Undo window in seconds (`0` = off)           |

## Using the app

1. **Start a conversation** — pick a peer from the list and type a message.
   If they're not a friend yet, your first message is a friend request.
2. **Accept a friend request** — it appears as a banner in the thread with
   **Accept** / **Reject**. Accepting completes the handshake; from then on you
   message freely.
3. **Send a file** — click the paperclip in the compose box, pick a file. The
   receiver sees an **"Incoming file"** bar at the bottom of the conversation
   with a **Save** button; accepting saves it to the download folder.
4. **Undo a send** — turn on **Undo delay** in Settings (seconds). Sent
   messages are held with a countdown; hit undo before it hits zero and the
   message is cancelled.
5. **Go offline** — toggle **Online** in Settings to stop receiving.
6. **Clear a chat / delete a message** — via the thread controls (local-only).

## HTTP API (optional)

Lanchat ships a small HTTPS API for scripts, agents, or curl. **Off by default**;
toggle it from the **API** switch in Settings. All endpoints except `/health`
require the shared token.

| Method | Path                | Auth        | What it does                                   |
|--------|---------------------|-------------|------------------------------------------------|
| `GET`  | `/health`           | —           | Liveness probe (`{"ok":true}`)                 |
| `GET`  | `/peers?token=…`    | token       | List online peers                             |
| `GET`  | `/messages?token=…` | token       | This machine's message history                |
| `POST` | `/send`             | token (body)| Send a message to a peer                      |

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
  checked against the one you friended, preventing impersonation.
- **Access** — the shared token gates discovery; the friend/handshake gates
  messaging. Only confirmed friends (or peers you've requested) can reach you.
- **Presence** — the online toggle stops broadcasts and drops inbound while off.

> The cert is self-signed (no CA), which is appropriate for LAN peer-to-peer.
> The token is a shared secret — treat it carefully; a machine holding it can
> see you in discovery.

## How it works

The plugin is three pieces:

- **`shared/Lanchat.qml`** — a QML singleton that owns the daemon process and
  all shared state. A true singleton, so exactly one daemon runs no matter how
  many bar surfaces or entry points exist.
- **`server.py`** — a stdlib-only Python daemon: TLS TCP server for messages,
  UDP broadcast discovery, heartbeat/expiry for online status, attachment
  serving, and JSON persistence.
- **`Service.qml` / `BarWidget.qml` / `Panel.qml`** — the always-on service,
  the bar icon + badge, and the chat panel.

Discovery: each machine broadcasts a signed `hello` every five seconds and
listens on the same port. Peers reply with a `pong`, and both sides track each
other until heartbeats stop (15s timeout). Messaging is a TLS connection to the
peer, authenticated with the shared token and verified by cert fingerprint.

The QML and the daemon talk over newline-delimited JSON — commands on stdin,
events on stdout:

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
plugin code automatically; the daemon is restarted by the singleton when it
exits. Run the offline end-to-end suites (two isolated instances):

```bash
python3 server.py
python3 test_server.py     # core: discovery, TLS delivery, auth, HTTPS API
python3 test_friends.py    # friend/handshake + online-toggle over TLS
python3 test_features.py   # lazy-load, clear/delete, attachment plumbing, config
```

> The plugin reloads QML but not always compiled types — after editing QML,
> `rm -rf ~/.cache/quickshell/qmlcache && omarchy-restart-shell`.

## License

MIT — see `LICENSE`.
