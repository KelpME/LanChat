#!/usr/bin/env python3
"""Update-button daemon restart: the applyUpdate bash command must explicitly
restart the lanchat.service daemon after a successful apply, so the daemon
never keeps running stale code / reporting a stale version (the lanchat.path
watcher only fires on server.py + top-level scripts/ changes — an update
touching only shared/*.qml or py modules would otherwise leave it stale).

Also guards: the restart cannot suppress the APPLIED echo, and the
dirty-guard (git status --porcelain) behavior is unchanged.

Run: python3 test_update_restart.py
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
QML = os.path.join(HERE, "shared", "Lanchat.qml")
SRV = os.path.join(HERE, "server.py")

failures = []


def check(cond, msg):
    if cond:
        print("PASS: " + msg)
    else:
        failures.append(msg)
        print("FAIL: " + msg)


with open(QML, encoding="utf-8") as f:
    qml = f.read()

# --- extract the applyUpdate function body --------------------------------
m = re.search(r"function applyUpdate\(force\) \{", qml)
check(m is not None, "applyUpdate function found in shared/Lanchat.qml")
start = m.end() - 1
depth = 0
end = None
for i in range(start, len(qml)):
    if qml[i] == "{":
        depth += 1
    elif qml[i] == "}":
        depth -= 1
        if depth == 0:
            end = i
            break
body = qml[start:end] if end else ""
check(bool(body), "applyUpdate body extracted (non-empty)")

# Reconstruct the single bash command string the way QML/JS concatenates it:
# pull every double-quoted JS string literal out of the statement segment, in
# order, skipping // comment lines. All `+` joins of literals collapse to
# their literal parts; the `dir`/`safe` variable fragments are handled by
# pre-substituting <DIR>/<SAFE> placeholders and asserted/spliced separately.
#
# NOTE: the QML statements are terminated by JS automatic-semicolon-insertion
# (no trailing `;`), and `//` line comments sit between the `+`
# continuations — both legal JS. So we do NOT scan for a `;` terminator;
# instead we take the statement segment up to a deterministic next-line
# anchor and let js_strings() drop the comments.
def js_strings(segment):
    """Concatenate all double-quoted string literals in a code segment,
    honoring backslash escapes, ignoring `//` line comments."""
    out, i, n = [], 0, len(segment)
    while i < n:
        c = segment[i]
        if c == '"':
            i += 1
            buf = []
            while i < n and segment[i] != '"':
                if segment[i] == "\\":
                    esc = segment[i + 1]
                    buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\",
                                "'": "'"}.get(esc, esc))
                    i += 2
                else:
                    buf.append(segment[i])
                    i += 1
            i += 1  # closing quote
            out.append("".join(buf))
        elif c == "/" and segment[i + 1:i + 2] == "/":
            # skip to end of line (comment)
            nl = segment.find("\n", i)
            i = n if nl == -1 else nl + 1
        else:
            i += 1
    return "".join(out)


def extract_segment(body, start_pat, stop_pat):
    """Return the text from the end of `start_pat` up to (not including) the
    line matching `stop_pat` (line-anchored). Deterministic segment bounds
    for ASI-terminated multi-line statements."""
    m = re.search(start_pat, body)
    if not m:
        return None
    rest = body[m.end():]
    stop = re.search(stop_pat, rest, flags=re.M)
    if not stop:
        return None
    return rest[:stop.start()]


def bind_vars(segment):
    """Turn bare concatenation operands `dir` and `safe` into quoted
    <DIR>/<SAFE> placeholder literals so js_strings() keeps their slots."""
    segment = re.sub(r"\+\s*dir\s*\+", ' + "<DIR>" + ', segment)
    segment = re.sub(r"\+\s*safe\s*\+", ' + "<SAFE>" + ', segment)
    return segment


cmd_rhs = extract_segment(body, r"var\s+cmd\s*=\s*", r"^\s*applyProc\b")
safe_rhs = extract_segment(body, r"var\s+safe\s*=\s*", r"^\s*var\s+cmd\b")
check(cmd_rhs is not None,
      "cmd assignment segment found in applyUpdate (ASI-terminated)")
check(safe_rhs is not None,
      "safe assignment segment found in applyUpdate (ASI-terminated)")

safe = js_strings(safe_rhs or "")
# The runtime command is: "cd " + dir + " && git fetch ... && { " + safe +
# <rest>. <DIR> stays as the dir placeholder; splice the reconstructed
# safe-guard fragment where the <SAFE> placeholder sits.
cmd = js_strings(bind_vars(cmd_rhs) or "")
full_cmd = cmd.replace("<SAFE>", safe)
# Any leftover bare `+ <ident> +` would be unknown vars; flag them.
leftover = re.findall(r"\+\s*[A-Za-z_$][\w$.]*\s*\+", full_cmd)
check(not leftover,
      "no unresolved variable concatenations in the command (%r)" % (leftover,))

# --- assertions -----------------------------------------------------------
check("systemctl --user restart lanchat.service" in full_cmd,
      "command contains explicit `systemctl --user restart lanchat.service`")

# (b) restart failure must not suppress the APPLIED echo.
restart_idx = full_cmd.find("systemctl --user restart lanchat.service")
applied_idx = full_cmd.find("echo APPLIED")
check(restart_idx != -1 and applied_idx != -1 and restart_idx < applied_idx,
      "restart is ordered before the APPLIED echo")
tail = full_cmd[restart_idx:applied_idx]
check("|| true" in tail,
      "restart is failure-tolerant (`|| true`) so APPLIED still echoes")

# (c) server.py changelog entry
with open(SRV, encoding="utf-8") as f:
    srv = f.read()
check("1.5.51 — update button: explicitly restart the lanchat.service daemon "
      "after a" in srv and "successful apply (the lanchat.path watcher misses "
      "updates that don't touch" in srv,
      "server.py changelog has the 1.5.51 explicit-daemon-restart entry")

# (d) dirty-guard unchanged: porcelain check before reset, DIRTY on local
# edits, and only in the safe path (force=false keeps it; force=true's empty
# safe string skips it).
check("git status --porcelain" in safe,
      "dirty-guard (`git status --porcelain`) still in the safe path")
check("echo DIRTY" in safe and "exit 0" in safe,
      "dirty-guard still echoes DIRTY + exits when local edits exist")
porcelain_idx = full_cmd.find("git status --porcelain")
reset_idx = full_cmd.find("git reset --hard origin/main")
check(porcelain_idx != -1 and reset_idx != -1 and porcelain_idx < reset_idx,
      "dirty-guard runs before `git reset --hard`")

# (e) behavioral simulation: run the reconstructed command in a temp git repo
# with dir="." and a systemctl stub. Covers: DIRTY gating, APPLIED even when
# systemctl fails, ERROR when the reset fails.
tmp = tempfile.mkdtemp(prefix="lnc-upd-restart-")
# systemctl stubs live OUTSIDE the probe checkout too — same reason as the
# bare origin below: an untracked dir inside it would trip the
# dirty-guard's `git status --porcelain`.
bins = tmp.rstrip("/") + "-bin"
binsfail = tmp.rstrip("/") + "-binfail"
try:
    stub = bins
    os.makedirs(stub, exist_ok=True)
    with open(os.path.join(stub, "systemctl"), "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(os.path.join(stub, "systemctl"), 0o755)
    env = dict(os.environ)
    env["PATH"] = stub + os.pathsep + env.get("PATH", "")

    def sh(script, extra_env=None, fail_stub=False):
        e = dict(env)
        if fail_stub:
            fs = binsfail
            os.makedirs(fs, exist_ok=True)
            with open(os.path.join(fs, "systemctl"), "w") as f:
                f.write("#!/bin/sh\nexit 1\n")
            os.chmod(os.path.join(fs, "systemctl"), 0o755)
            e = dict(env)
            e["PATH"] = fs + os.pathsep + env.get("PATH", "")
        if extra_env:
            e.update(extra_env)
        return subprocess.run(["bash", "-c", script], cwd=tmp, env=e,
                              capture_output=True, text=True, timeout=30)

    # Seed a local bare `origin` so `git fetch origin main` in the probe
    # actually succeeds (the real daemon fetches over the network; here the
    # fetch must succeed for the apply path to be exercised at all). The bare
    # repo lives OUTSIDE the probe checkout — an untracked dir inside it would
    # trip the dirty-guard's `git status --porcelain`.
    origin = tmp.rstrip("/") + "-origin.git"
    shutil.rmtree(origin, ignore_errors=True)
    sh("git init -q . && git config user.email t@t && git config user.name t "
       "&& echo hi > f && git add f && git commit -qm one && git branch -M main "
       "&& git init -q --bare %s && git remote add origin %s && git push -q origin main"
       % (shlex.quote(origin), shlex.quote(origin)))

    probe = full_cmd.replace("<DIR>", ".")
    check("cd . &&" in probe, "behavioral: probe command built (dir bound to .)")

    # dirty checkout + safe path -> DIRTY (no reset, no restart)
    open(os.path.join(tmp, "f"), "w").write("dirty edit")
    r = sh(probe)
    check(r.stdout.strip() == "DIRTY",
          "behavioral: dirty checkout + safe path -> DIRTY (got %r)" % r.stdout.strip())

    # clean checkout + working systemctl -> APPLIED (fetch succeeds from the
    # local bare origin; reset --hard to origin/main is a no-op but runs).
    subprocess.run("git checkout -q -- f && git update-ref refs/remotes/origin/main refs/heads/main",
                   shell=True, cwd=tmp)
    r = sh(probe)
    # Production parses stdout with a line-based SplitParser (`t === "APPLIED"`),
    # so match it line-wise: `git reset --hard` prints "HEAD is now at ..." to
    # stdout (2>/dev/null doesn't cover it), and that extra line is ignored by
    # the real parser too.
    check("APPLIED" in [l.strip() for l in r.stdout.splitlines()],
          "behavioral: clean checkout -> APPLIED (got %r)" % r.stdout.strip())
    # and the reset must have run (f is tracked-clean after reset --hard)
    r2 = subprocess.run("git status --porcelain", shell=True, cwd=tmp,
                        capture_output=True, text=True)
    check(r2.stdout.strip() == "",
          "behavioral: after APPLIED the checkout is reset --hard (clean)")

    # systemctl failing -> still APPLIED (restart failure tolerated)
    r = sh(probe, fail_stub=True)
    check("APPLIED" in [l.strip() for l in r.stdout.splitlines()],
          "behavioral: systemctl failure still yields APPLIED (got %r)" % r.stdout.strip())

    # reset failure -> ERROR (never APPLIED)
    bad = probe.replace("git reset --hard origin/main 2>/dev/null",
                        "false 2>/dev/null")
    r = sh(bad)
    check(r.stdout.strip() == "ERROR",
          "behavioral: reset failure -> ERROR (got %r)" % r.stdout.strip())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(bins, ignore_errors=True)
    shutil.rmtree(binsfail, ignore_errors=True)

print()
if failures:
    print("FAILED (%d):" % len(failures))
    for f_ in failures:
        print("  - " + f_)
    sys.exit(1)
print("All update-restart checks passed.")
