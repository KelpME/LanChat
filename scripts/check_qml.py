#!/usr/bin/env python3
"""Structural QML check: verify braces/parens/brackets balance in QML files.

Catches the "Expected token '}'" class of breakage without needing the Omarchy
shell modules (qs.Ui / qs.Commons), which only exist on a real install. Not a
full QML parser — walks each file and counts structural tokens, ignoring
content inside line comments, block comments, and double/single-quoted strings.
Used by CI.
"""
import sys

FILES = ["Panel.qml", "BarWidget.qml", "shared/Lanchat.qml", "Service.qml", "shared/ChatMessage.qml", "shared/RoomMessage.qml", "shared/ComposeBox.qml", "shared/PeerList.qml", "shared/RoomListSection.qml"]

PAIRS = {"{": "}", "(": ")", "[": "]"}
NAMES = {"braces": "{ }", "parens": "( )", "brackets": "[ ]"}


def check_file(path: str):
    """Return (list_of_problems, counts)."""
    with open(path, encoding="utf-8") as f:
        data = f.read()
    n = len(data)
    stack = []  # list of (char, line)
    line = 1
    i = 0
    problems = []
    while i < n:
        c = data[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        # line comment
        if c == "/" and i + 1 < n and data[i + 1] == "/":
            while i < n and data[i] != "\n":
                i += 1
            continue
        # block comment
        if c == "/" and i + 1 < n and data[i + 1] == "*":
            i += 2
            while i + 1 < n and not (data[i] == "*" and data[i + 1] == "/"):
                if data[i] == "\n":
                    line += 1
                i += 1
            i = min(i + 2, n)
            continue
        # double-quoted string (QML uses \ escapes)
        if c == '"':
            i += 1
            while i < n and data[i] != '"':
                if data[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if data[i] == "\n":
                    line += 1
                i += 1
            i += 1
            continue
        # single-quoted string
        if c == "'":
            i += 1
            while i < n and data[i] != "'":
                if data[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if data[i] == "\n":
                    line += 1
                i += 1
            i += 1
            continue
        # structural char
        if c in PAIRS:
            stack.append((c, line))
        elif c in PAIRS.values():
            if stack:
                open_c = stack.pop()[0]
                if PAIRS[open_c] != c:
                    problems.append("mismatch %r at line %d (expected %s)" % (c, line, PAIRS[open_c]))
            else:
                problems.append("unexpected closing %r at line %d" % (c, line))
        i += 1

    # unclosed
    for open_c, ln in stack:
        name = "{ }" if open_c == "{" else ("( )" if open_c == "(" else "[ ]")
        problems.append("unclosed %s from line %d" % (name, ln))
    return problems


def main() -> int:
    fail = 0
    for path in FILES:
        problems = check_file(path)
        if problems:
            for p in problems:
                print("FAIL %s: %s" % (path, p))
            fail = 1
        else:
            print("OK   %s: structural tokens balanced" % path)
    return fail


if __name__ == "__main__":
    sys.exit(main())
