# KelpME.lanchat

Private messaging between your own machines on your local network, as an
[Omarchy Quattro](https://github.com/omacom/omarchy/tree/quattro) shell plugin.
Install it on every computer you own, set them all to the same shared secret,
and they find each other automatically — no IP addresses, no accounts, no
cloud.

- **Automatic discovery** — machines announce themselves with a UDP broadcast
  on your LAN and appear in each other's peer list within seconds.
- **Shared-secret auth** — every connection and every discovery packet must
  present the same token, which you copy between machines once.
- **Per-machine history** — each machine keeps its own copy of its threads in
  `~/.local/state/lanchat/history.json`, so messages survive reboots and
  machines can be offline.
- **A native bar UI** — one chat icon in the bar, an online-peer + unread
  badge, and a click opens the chat panel (peer list + thread + compose box).

> ⚠️ **Security caveat.** This is plaintext messaging over your local network.
> The shared token gates *who can talk to you*, but it does not encrypt the
> traffic. It is intended for a trusted home or office LAN. Anyone who can
> sniff the wire can read messages. Don't use it on a public or untrusted
> network.

## Requirements

- Omarchy Quattro (the shell plugin system, `schemaVersion: 1`)
- `python3` (stdlib only — the daemon has no dependencies)
- Every machine you want to reach must be on the same LAN/subnet

## Install

```bash
omarchy plugin add https://github.com/<you>/KelpME.lanchat.git --enable --yes
```

or, by hand:

```bash
cp -r KelpME.lanchat ~/.config/omarchy/plugins/KelpME.lanchat
omarchy-shell shell rescanPlugins
omarchy plugin enable KelpME.lanchat
```

## First-run setup (the shared secret)

The daemon writes its config on first run to
`~/.config/omarchy/lanchat.json`:

```json
{
  "token": "a-long-random-hex-token",
  "port": 4812,
  "displayName": "desktop"
}
```

Do this once:

1. Start Lanchat on your first machine, then **copy the `token` value** from
   that machine's config.
2. On every other machine, edit `~/.config/omarchy/lanchat.json` and paste the
   **same token**. Set `displayName` to whatever you want that machine to be
   called in your peer list.
3. Restart each shell (`omarchy-restart-shell`) or rescan plugins.

Machines only see peers that share the same token. If you later change the
token, update it everywhere.

### Config options

| Key           | Default            | Meaning                                      |
|---------------|--------------------|----------------------------------------------|
| `token`       | *(random)*         | Shared secret — must match on every machine  |
| `port`        | `4812`             | TCP + UDP port (same value on every machine) |
| `displayName` | friendly name     | Name shown to other machines. Defaults to a deterministic friendly name (e.g. `MegaBoardslide`); set it in the UI if you want a custom one |
| `httpEnabled` | `false`            | On/off for the HTTP API (toggle in the UI)   |
| `httpPort`    | `4814`             | Port the HTTP API listens on                 |

## HTTP API (optional)

Lanchat ships a small HTTP API so other tools — scripts, agents like Hermes, or
curl — can send messages and read state without the desktop UI. It is **off by
default**; toggle it from the **API** switch in the chat panel's peer-list
footer (it restarts the listener live, no reboot needed).

Once enabled, all endpoints except `/health` require the shared token. Get the
token from `~/.config/omarchy/lanchat.json`.

| Method | Path                | Auth        | What it does                                   |
|--------|---------------------|-------------|------------------------------------------------|
| `GET`  | `/health`           | —           | Liveness probe (`{"ok":true}`)                 |
| `GET`  | `/peers?token=…`    | token       | List online peers                             |
| `GET`  | `/messages?token=…` | token       | This machine's message history                |
| `POST` | `/send`             | token (body)| Send a message to a peer                      |

Send a message (JSON body carries the token):

```bash
curl -k -X POST https://localhost:4814/send \
  -H 'Content-Type: application/json' \
  -d '{"token":"<TOKEN>","to":"<peer-id>","text":"hello from the shell"}'
```

List peers:

```bash
curl -k 'https://localhost:4814/peers?token=<TOKEN>'
```

> The API now serves **HTTPS** with a per-install self-signed certificate. `-k`
> skips verification for the self-signed cert — good for local scripting; for
> real trust, verify the cert fingerprint against the device's `lanchat-certs/`.
> The peer id is the peer's cert fingerprint (not its hostname), so `to` must
> be a fingerprint. The API listens on all interfaces, reachable by any machine
> with the token.

## How it works

The plugin is three pieces:

- **`shared/Lanchat.qml`** — a QML singleton that owns the daemon process and
  all shared state. Because it is a true singleton, exactly one daemon runs no
  matter how many bar surfaces or entry points exist.
- **`server.py`** — a stdlib-only Python daemon that does what a Quickshell
  plugin cannot: a TCP server for incoming messages, a UDP broadcast for
  discovery, heartbeat/expiry for online status, and message persistence.
- **`Service.qml` / `BarWidget.qml` / `Panel.qml`** — the always-on service,
  the bar icon + badge, and the chat panel.

Discovery works like this: each machine broadcasts a signed `hello` every five
seconds and listens on the same port. Peers reply with a `pong`, and both sides
track each other until they stop hearing heartbeats (15s timeout). Sending a
message is a plain TCP connection to the peer's address and port, authenticated
with the shared token.

The QML and the daemon talk over newline-delimited JSON — commands on stdin,
events on stdout:

```
stdin  →  {"cmd":"send","to":"<id>","text":"..."}
           {"cmd":"history"}  {"cmd":"list"}
stdout →  {"event":"ready"|"peer"|"peer-gone"|"message"|"history"|"peers"|"error"|"notice", ...}
```

## Development

Validate your copy before installing:

```bash
omarchy plugin validate .
```

Saving any file under `~/.config/omarchy/plugins/KelpME.lanchat/` reloads the
plugin code automatically; the daemon is restarted by the singleton when it
exits. You can also run the daemon standalone to test networking, or run the offline
end-to-end suite (two isolated instances: discovery, TCP delivery, auth
rejection, persistence):

```bash
python3 server.py
python3 test_server.py   # ALL TESTS PASSED expected
```

## Roadmap (ideas)

- Message timestamps / grouped day headers
- Typing indicators and read receipts
- File/emoji sharing
- Optional end-to-end encryption (e.g. a per-pair key) for non-trusted LANs

## License

MIT — see `LICENSE`.
