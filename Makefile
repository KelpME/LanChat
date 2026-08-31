# KelpME.lanchat — developer targets.
# Pure-Python plugin: no build step. These wrap the tests, lint, and QML checks.
#
# Usage:
#   make            # same as `make help`
#   make test       # run every test_*.py suite
#   make check      # lint + QML structural check + python syntax
#
# Ruff runs from PATH; pyright is configured in pyproject.toml but not bundled
# here (install it yourself if you want `make typecheck` to do anything).

PY      ?= python3
RUFF    ?= ruff
TESTS   := test_server.py test_friends.py test_persistent.py test_attachments.py test_features.py test_discovery_visibility.py test_systemd_control.py
PYFILES := server.py naming.py $(TESTS) test_peer.py

# Bare `make` (no target) shows the help listing.
.DEFAULT_GOAL := help

.PHONY: help test lint fmt check qml syntax clean typecheck run run-dev dev-info help-html test-systemd-control systemd-install systemd-status

## help: list all targets and what they do
help: ## (default) show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## test: run the full offline test suite (all test_*.py)
test: ## run every test_*.py suite (server, friends, persistent, attachments, features, discovery-visibility, systemd-control)
	@for t in $(TESTS); do \
		printf "=== %s ===\n" "$$t"; \
		$(PY) "$$t" || exit 1; \
	done

## test-server: just test_server.py
test-server: ## run test_server.py (transport, spoofing, HTTP API)
	@$(PY) test_server.py

## test-friends: just test_friends.py
test-friends: ## run test_friends.py (friend handshake gate)
	@$(PY) test_friends.py

## test-persistent: just test_persistent.py
test-persistent: ## run test_persistent.py (persistent socket, dedupe, reconnect)
	@$(PY) test_persistent.py

## test-attachments: just test_attachments.py
test-attachments: ## run test_attachments.py (file transfer, sha256, path sanitize)
	@$(PY) test_attachments.py

## test-features: just test_features.py
test-features: ## run test_features.py (history, config, misc commands)
	@$(PY) test_features.py

## test-discovery-visibility: just test_discovery_visibility.py
test-discovery-visibility: ## run test_discovery_visibility.py (broadcast side of the visibility flip)
	@$(PY) test_discovery_visibility.py

## test-systemd-control: just test_systemd_control.py
test-systemd-control: ## run test_systemd_control.py (systemd unix-socket control channel + bridge)
	@$(PY) test_systemd_control.py

## systemd-install: install + enable the lanchat systemd user unit
systemd-install: ## copy systemd/lanchat.service to ~/.config/systemd/user and enable it
	@mkdir -p $${XDG_CONFIG_HOME:-$$HOME/.config}/systemd/user
	@cp systemd/lanchat.service $${XDG_CONFIG_HOME:-$$HOME/.config}/systemd/user/lanchat.service
	@systemctl --user daemon-reload
	@systemctl --user enable --now lanchat.service
	@echo "Lanchat daemon now runs under systemd. Status: systemctl --user status lanchat"

## systemd-status: show the daemon's systemd state
systemd-status: ## systemctl --user status lanchat (is the daemon running?)
	@systemctl --user status lanchat --no-pager || true

## lint: ruff check on all Python
lint: ## run `ruff check` (fast, no fix)
	@command -v $(RUFF) >/dev/null 2>&1 || { echo "ruff not found on PATH; install it (e.g. 'pip install ruff')"; exit 1; }
	$(RUFF) check $(PYFILES)

## fmt: ruff format in check-only mode (report what would change)
fmt: ## check formatting with `ruff format --check` (does NOT rewrite)
	@command -v $(RUFF) >/dev/null 2>&1 || { echo "ruff not found on PATH; install it (e.g. 'pip install ruff')"; exit 1; }
	$(RUFF) format --check $(PYFILES)

## qml: structural QML check (braces/parens/brackets)
qml: ## run scripts/check_qml.py on Panel.qml, BarWidget.qml, shared/Lanchat.qml, Service.qml
	@$(PY) scripts/check_qml.py

## help-html: regenerate HELP.html from HELP.md (needs python-markdown)
help-html: ## rebuild the built-in help page from HELP.md
	@$(PY) scripts/gen_help_html.py

## syntax: py_compile all Python files (fast syntax gate)
syntax: ## python-compile everything (catches syntax errors)
	@$(PY) -m py_compile $(PYFILES) && echo "syntax OK"

## check: lint + QML check + syntax (the pre-commit gate)
check: lint qml syntax ## run lint, qml, and syntax together

## typecheck: pyright (only if you've installed it)
typecheck: ## run pyright static analysis (requires pyright installed)
	@command -v pyright >/dev/null 2>&1 || { echo "pyright not found on PATH; install it (e.g. 'pip install pyright' or npm i -g pyright)"; exit 1; }
	pyright

## clean: remove Python bytecode/cache dirs
clean: ## delete __pycache__ and *.pyc
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	@find . -name '*.pyc' -delete
	@echo "cleaned"

## run: start the daemon against the real ~/.config/omarchy config (foreground)
run: ## launch server.py with the installed config; stop the plugin daemon first (both bind :4812)
	@echo "Using ~/.config/omarchy/lanchat.json. Stop the Omarchy plugin daemon first (ports 4812/4814) or this will fail to bind."
	@$(PY) server.py

## run-dev: launch an isolated throwaway daemon (dev port 4899, temp HOME) that
##          never touches your real config, peers, or history
run-dev: ## start a throwaway daemon on :4899 in a temp HOME (Ctrl-C to stop)
	@tmp=$$(mktemp -d /tmp/lanchat-dev-XXXXXX); \
	 mkdir -p "$$tmp/.config/omarchy"; \
	 printf '{"token":"dev-secret-token-1234","port":4899,"displayName":"Dev","httpPort":4898}' > "$$tmp/.config/omarchy/lanchat.json"; \
	 echo "dev HOME: $$tmp  (token: dev-secret-token-1234, msg port 4899, http 4898)"; \
	 HOME="$$tmp" $(PY) server.py

## dev-info: show where the real config/certs/state live and the current token
dev-info: ## print the live config/cert/state paths (no changes)
	@echo "config: ~/.config/omarchy/lanchat.json"
	@echo "certs:  ~/.config/omarchy/lanchat-certs/"
	@echo "state:  ~/.local/state/lanchat/history.json"
	@test -f ~/.config/omarchy/lanchat.json && python3 -c "import json,os;c=json.load(open(os.path.expanduser('~/.config/omarchy/lanchat.json')));print('displayName:',c.get('displayName'));print('port:',c.get('port'));print('httpEnabled:',c.get('httpEnabled'));print('apiFullAccess:',c.get('apiFullAccess'));print('friends:',len(c.get('friends',[])))" || echo "(no config yet — first \`make run\` generates one)"
