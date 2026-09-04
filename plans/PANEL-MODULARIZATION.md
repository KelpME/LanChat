# Panel.qml Modularization Plan

## Why

Panel.qml is 3,829 lines — one file holds the peer list, onboarding, friend
requests, the rooms section, a 1,655-line settings block, the room chat view,
the 1:1 thread, the compose box, and attachment staging. The phantom-attachment
bug (fixed in d6ff65d) hid two broken QML bindings in that mass; both had been
throwing in the journal for weeks. Files this size cannot be reviewed, tested,
or safely edited. This plan splits it along its existing seams — no behavior
change.

## Constraint: zero behavior change

- Every extraction is a move, not a rewrite: same ids, same bindings, same
  property names unless renaming is required by the move (documented per step).
- Gate after EVERY step: `qmllint` clean-ish, shell hot-reloads with no new
  `Panel.qml[...]`/`qmldir` errors in `journalctl --user -t omarchy-shell`,
  manual smoke (open panel, select peer, send room text, open settings).
- Gate after every 2-3 steps: `git push` so a bad extraction is revertable.
- QML specifics that bit us before, enforced in review:
  - ALL bool bindings `!!(...)`-coerced when the source may be undefined.
  - No `x: someId.parent` indirections — direct id refs or explicit properties.
  - Cross-file references go through the singleton (`Lanchat`) or a root-level
    alias, never `parent`-chain walks.

## Architecture

Plugin entry points stay untouched (`Service.qml`, `BarWidget.qml` load
`Panel.qml` at the same path). New components live in `shared/` (already a
qmldir-imported directory; `import "shared"` already exists in Panel.qml).
Each new file gets one line in `shared/qmldir`.

Shared state problem: Panel.qml's ~40 root properties/functions are used
everywhere (selection, staging, editing, dialogs). Root stays the state owner;
children receive what they need via explicit `property` inputs and call back
via `signal`s. NO child reaches into `root.` for anything not declared as its
own input — enforced per step in review. Where a child needs too many inputs
(>8), that's a signal the cut line is wrong: re-cut, don't pass a bag.

## Target structure (~10 files, Panel.qml shrinks to <600 lines)

| New file (shared/) | Moves | ~Lines |
|---|---|---|
| `ChatMessage.qml` | 1:1 message bubble delegate (incl. attachment row) | ~120 |
| `RoomMessage.qml` | room text bubble + file bubble + member chips + delivery statuses | ~200 |
| `PeerList.qml` | peers list, onboarding banner, friend-request section, online section | ~400 |
| `RoomListSection.qml` | rooms: collapsible list, create-dialog, invite flow, colors | ~330 |
| `SettingsPanel.qml` | the 1,655-line settings block (identity, presence, rooms colors, chat, appearance, agents, reachability, developer) | ~1655 |
| `ChatThread.qml` | 1:1 thread view (uses ChatMessage) + incoming-file bar + undo bar | ~430 |
| `RoomView.qml` | room chat view (uses RoomMessage) | ~270 |
| `ComposeBox.qml` | compose box, staged-attachments preview, in-input alert | ~245 |
| `ProcessHelpers.qml` | the 2 Processes (file picker, download dir) — or inline into ComposeBox if cleaner | ~40 |

`shared/qmldir` gains one `singleton`-less line per component.

## Steps (each is one commit, gate, then next)

1. **Scaffold + ChatMessage.qml** — lowest-risk extraction proves the pattern:
   create the component, move the 1:1 bubble delegate, wire inputs
   (`modelData`, thread colors), gate. Panel.qml -120.
2. **RoomMessage.qml** — move room text/file bubbles + status chips
   (the bug site — extra care, re-verify the phantom fix renders right).
   Panel.qml -200.
3. **ComposeBox.qml** — move compose box + staging preview + alert + the two
   Processes. Root keeps `send()`; child emits `sendRequested(text)`.
   Panel.qml -285.
4. **PeerList.qml** — peers + onboarding + friend requests + online. Root
   keeps selection state; child emits `peerSelected(id)`,
   `friendAccepted(id)`, etc. Panel.qml -400.
5. **RoomListSection.qml** — rooms list + create-dialog + colors. Child emits
   `roomSelected(roomId)` / `roomCreated(name)`. Panel.qml -330.
6. **ChatThread.qml + RoomView.qml** — thread views, now thin because their
   delegates already moved in 1-2. Panel.qml -700.
7. **SettingsPanel.qml** — the big one, LAST: 1,655 lines moved as ONE block
   with its existing internal section comments intact (its sections already
   read as sub-modules; splitting it further is a follow-up if ever needed).
   It is the most self-contained (reads Lanchat singleton, emits nothing back
   except via Lanchat). Panel.qml -1655.
8. **Final pass** — dead code sweep, qmllint across shared/, README note in
   HELP.md dev section, version bump in manifest.json, push.

## Explicit non-goals

- No restyling, no renamed user-visible strings, no settings reorganization.
- No logic changes in Lanchat.qml (it's already a singleton; fine at 1,327
  lines for now).
- No behavior-preserving "improvements" spotted along the way — anything
  found gets a `// TODO(modularize):` comment, not a fix.

## Risk register

- **QML id scoping across files** — ids are file-local; anything a child
  references must become an explicit property. Caught by the per-step gate
  (ReferenceErrors appear in journal within seconds of hot-reload).
- **`readonly property` chains recomputing differently** — bindings are
  reactive across files same as within; watch for accidental eager loops via
  the `property var` snapshot pattern (slice() copies) — pattern preserved as-is.
- **Settings block hidden coupling to compose/thread state** — grep before
  step 7 confirms; if found, those become explicit inputs.
- **Hot-reload masking stale state** — every gate includes one full shell
  restart (`systemctl --user restart omarchy-shell` equivalent) not just
  hot-reload, at steps 3, 5, and 8.

## Time estimate

Steps 1-3: one session (~1-2 h). Steps 4-6: second session (~2 h).
Step 7-8: third session (~1-2 h, mostly mechanical but large diff).
