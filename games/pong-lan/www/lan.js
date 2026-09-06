/**
 * Forge LAN — multiplayer host interface for HermesForge games.
 *
 * A game drives `createLanHost()` to join a session, send input, and receive
 * authoritative snapshots/events. The host interface is TRANSPORT-AGNOSTIC:
 * the game never touches sockets, peers, or rooms. The actual network transport
 * is an adapter supplied via `transport` (see `createLoopbackTransport` — the
 * adapter that talks to THIS machine's local lanchat daemon over its loopback
 * HTTP feed, which relays to peers over the existing authenticated TLS socket).
 *
 * Contract (mirrors the lanchat `Game` sim contract so the two line up):
 *   - join(seatId, theme)      live join or rejoin
 *   - drop(seatId)             leave mid-game (seat state persists)
 *   - sendInput(action, value) normalized control (~30/s)
 *   - snapshot()               latest authoritative state
 *   - leave()                  abandon the session
 *   - onSessionStart / onPlayerJoin / onPlayerDrop / onPlayerRejoin / onSnapshot / onEvent
 *
 * The game renders `snapshot()` locally (three.js), pushes normalized input,
 * and handles join/drop/rejoin via the callbacks. Persistence (serialize/load)
 * is the game's own concern on top of the host's session.
 */

/**
 * Create a LAN host for the current game window.
 * @param {object} opts
 * @param {object}  opts.transport   the network adapter (default: loopback to the
 *                                   local lanchat daemon). Must expose:
 *                                   { connect(sessionOpts), sendInput, leave, onEvent }.
 * @param {string}  [opts.selfId]    this window's player/seat id
 * @param {string}  [opts.theme]     per-seat theme {accent, normal, border}
 * @returns {object} the host interface.
 */
export function createLanHost(opts = {}) {
  const transport = opts.transport || createLoopbackTransport()
  const selfId = opts.selfId || ''
  const theme = opts.theme || {}
  const callbacks = {
    onSessionStart: null,
    onPlayerJoin: null,
    onPlayerDrop: null,
    onPlayerRejoin: null,
    onSnapshot: null,
    onEvent: null,
  }
  let latest = null

  transport.onEvent((ev) => {
    if (!ev || typeof ev !== 'object') return
    switch (ev.kind) {
      case 'session-started': callbacks.onSessionStart && callbacks.onSessionStart(ev); break
      case 'player-joined': callbacks.onPlayerJoin && callbacks.onPlayerJoin(ev); break
      case 'player-dropped': callbacks.onPlayerDrop && callbacks.onPlayerDrop(ev); break
      case 'player-rejoined': callbacks.onPlayerRejoin && callbacks.onPlayerRejoin(ev); break
      case 'snapshot': latest = ev.state; callbacks.onSnapshot && callbacks.onSnapshot(ev.state); break
      case 'event': callbacks.onEvent && callbacks.onEvent(ev.gameEvent); break
      default: break
    }
  })

  return {
    join(seatId, seatTheme) {
      return transport.connect({ selfId: seatId || selfId, theme: seatTheme || theme })
    },
    drop(seatId) { return transport.drop(seatId) },
    rejoin(seatId, seatTheme) { return transport.connect({ selfId: seatId || selfId, theme: seatTheme || theme }) },
    sendInput(action, value) { return transport.sendInput(action, value) },
    snapshot() { return latest },
    leave() { return transport.leave() },
    onSessionStart(fn) { callbacks.onSessionStart = fn },
    onPlayerJoin(fn) { callbacks.onPlayerJoin = fn },
    onPlayerDrop(fn) { callbacks.onPlayerDrop = fn },
    onPlayerRejoin(fn) { callbacks.onPlayerRejoin = fn },
    onSnapshot(fn) { callbacks.onSnapshot = fn },
    onEvent(fn) { callbacks.onEvent = fn },
  }
}

/**
 * Loopback transport to THIS machine's local lanchat daemon.
 *
 * The game window lives in the browser; the lanchat daemon is a local Python
 * process. This adapter talks to it over loopback HTTP (the daemon's loopback
 * game feed/control endpoints), which relays game messages to peers over the
 * existing authenticated TLS `t:"game"` socket. Everything stays on 127.0.0.1 —
 * no new LAN surface.
 *
 * Session identity: the daemon scopes every game endpoint by roomId+gameId.
 * The transport is created with that identity (the game window knows which
 * session it belongs to — the daemon/QML supplies it when launching the
 * window, via `?lan=` + `?room=` + `?game=` or `window.FORGE_LAN_*`).
 *
 * Endpoint contract (lanchat daemon, loopback HTTP, token-auth):
 *   GET  /game/feed?roomId=&gameId=&after=<seq>  -> { snapshot, events:[{seq,...}], latest }
 *   POST /game/join  { roomId, gameId, seat, theme, token }
 *   POST /game/input { roomId, gameId, action, value, token }
 *   POST /game/drop  { roomId, gameId, seat, token }
 *   POST /game/leave { roomId, gameId, token }
 *
 * If no endpoint is supplied (no lanchat daemon on this machine), the adapter
 * degrades to a local-only solo mode — the game still runs, just no peers.
 *
 * @param {object} [opts] { endpoint?, token?, roomId?, gameId?, fetchImpl? }
 */
export function createLoopbackTransport(opts = {}) {
  const endpoint = opts.endpoint
    || (typeof window !== 'undefined' && window.FORGE_LAN_ENDPOINT)
    || (typeof window !== 'undefined' && new URLSearchParams(location.search).get('lan'))
    || ''
  const token = opts.token
    || (typeof window !== 'undefined' && (window.FORGE_LAN_TOKEN || new URLSearchParams(location.search).get('lanToken')))
    || ''
  const roomId = opts.roomId
    || (typeof window !== 'undefined' && new URLSearchParams(location.search).get('room'))
    || ''
  const gameId = opts.gameId
    || (typeof window !== 'undefined' && new URLSearchParams(location.search).get('game'))
    || ''
  const doFetch = opts.fetchImpl || ((...a) => fetch(...a))
  const listeners = []
  let pollTimer = null

  function emit(ev) { for (const fn of listeners) { try { fn(ev) } catch (e) { console.warn('[forge-lan] event handler:', e) } } }

  function base(path, qs = {}) {
    if (!endpoint) return ''
    const u = new URL(endpoint)
    u.pathname = u.pathname.replace(/\/$/, '') + path
    for (const [k, v] of Object.entries({ ...qs, roomId, gameId })) {
      if (v !== '' && v != null) u.searchParams.set(k, String(v))
    }
    if (token) u.searchParams.set('token', token)
    return u.toString()
  }

  function body(path, payload = {}) {
    return doFetch(base(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, roomId, gameId, ...payload }),
    }).then((r) => (r.ok ? r.json().catch(() => ({})) : { ok: false })).catch((e) => {
      console.warn('[forge-lan] POST', path, e); return { ok: false }
    })
  }

  return {
    connect(sessionOpts) {
      const seat = (sessionOpts && sessionOpts.selfId) || ''
      const theme = (sessionOpts && sessionOpts.theme) || {}
      if (!endpoint) { console.warn('[forge-lan] no loopback endpoint — solo/no-op mode'); return null }
      // Join the session (creates/joins the local mirror; host applies or relays).
      body('/game/join', { seat, theme }).then((res) => {
        if (res.ok) emit({ kind: 'session-started', gameId, roomId, youAre: res.youAre, mode: res.mode })
      })
      // Poll the feed for snapshots/events (direct-localhost consumer; LAN-local
      // latency makes polling plenty smooth — no per-frame stream needed).
      let cursor = 0
      const poll = async () => {
        if (!endpoint) return
        const url = base('/game/feed', { after: cursor })
        try {
          const r = await doFetch(url)
          if (r.ok) {
            const data = await r.json()
            for (const ev of (data.events || [])) { cursor = Math.max(cursor, ev.seq || 0); emit(ev) }
            if (data.snapshot) emit({ kind: 'snapshot', state: data.snapshot })
          }
        } catch (e) { console.warn('[forge-lan] feed poll:', e) }
        pollTimer = setTimeout(poll, 50)
      }
      poll()
      return this
    },
    drop(seatId) { return body('/game/drop', { seat: seatId }) },
    sendInput(action, value) { return body('/game/input', { action, value }) },
    leave() { return body('/game/leave') },
    onEvent(fn) { listeners.push(fn) },
    dispose() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null },
  }
}
