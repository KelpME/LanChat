- HELP.md dev section added; HELP.html regenerated via make help-html (python-markdown present); bump-version -> 1.5.50; make check EXIT=0
# step 8 progress
- removed 4 dead symbols: amRoomOwner property, deleteMsg, openLog, sendFile (zero refs repo-wide, verified)
- sweep verified: qmllint 0 errors on all 11 files (warnings are pre-existing unqualified-access/Commons style; counts: Panel 155, ChatMessage 55, ChatThread 18, ComposeBox 63, Lanchat 6, PeerList 187, RoomListSection 125, RoomMessage 104, RoomView 12, SettingsPanel 410); check_qml.py OK 12 files
- TODO(modularize) markers: none found in Panel.qml or shared/*.qml
