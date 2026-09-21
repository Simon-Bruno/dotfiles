#!/usr/bin/env python3
"""Names every Herdr agent and tab from its recent conversation.

Runs on a timer (launchd). Reads the last few messages of each Claude and Codex
session, asks Sonnet for all names in one call, and applies them. Names and tab
labels that were set by hand are left alone: we only overwrite what we set last.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

MODEL = "sonnet"
RECENT_MESSAGES = 10
MESSAGE_CHARS = 300
STATE_DIR = os.path.expanduser("~/.config/herdr/namer/state")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

SCHEMA = {
    "type": "object",
    "properties": {
        "agents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"pane_id": {"type": "string"}, "name": {"type": "string"}},
                "required": ["pane_id", "name"],
            },
        },
        "tabs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tab_id": {"type": "string"}, "title": {"type": "string"}},
                "required": ["tab_id", "title"],
            },
        },
    },
    "required": ["agents", "tabs"],
}

INSTRUCTIONS = """You name coding-agent sessions shown in a terminal multiplexer sidebar.
For each agent, read its recent conversation and describe what it is working on overall, weighting recent work.

Agent name: 2 to 4 lowercase words joined by hyphens, at most 32 characters, starting with a letter.
Keep an issue key like stlr-2349 at the front when the work is about one, but only a key that appears in that
agent's own messages or cwd. Name the subject, not the action
(save-bar-rollout, not implement-changes). Every agent name must be unique.

Tab title: 2 to 6 words, at most 40 characters, plain text. Start with the issue key in capitals (STLR-2349)
when there is one. When a tab holds several agents, title the shared theme, or list the subjects briefly.

A "current" name or title is one you gave earlier. Keep it exactly unless the work has clearly moved on;
names that change every few minutes are worse than slightly imperfect ones.

Return every agent and every tab listed below."""


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def herdr(*args):
    out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"herdr {' '.join(args)} failed")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text")
        )
    return ""


def keep(role, txt):
    # Skip injected context (instructions, command wrappers, tool results) that says nothing about the task.
    if not txt or txt.startswith(("<", "# AGENTS.md", "[Request interrupted", "Caveat:")):
        return None
    return f"{role}: {txt[:MESSAGE_CHARS]}"


def claude_messages(session_id):
    paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session_id}.jsonl"))
    if not paths:
        return None, None
    path = max(paths, key=os.path.getmtime)
    msgs = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in ("user", "assistant") or r.get("isSidechain") or r.get("isMeta"):
            continue
        m = keep(r["type"], " ".join(text_of(r.get("message", {}).get("content")).split()))
        if m:
            msgs.append(m)
    return path, msgs


def codex_messages(session_id):
    paths = glob.glob(os.path.expanduser(f"~/.codex/sessions/*/*/*/rollout-*-{session_id}.jsonl"))
    if not paths:
        return None, None
    path = max(paths, key=os.path.getmtime)
    msgs = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        p = r.get("payload") or {}
        if r.get("type") != "response_item" or p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
            continue
        m = keep(p["role"], " ".join(text_of(p.get("content")).split()))
        if m:
            msgs.append(m)
    return path, msgs


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except (OSError, ValueError):
        return {"agents": {}, "tabs": {}, "seen": {}, "adopt_hook_names": True}


def adopt_hook_names(state, listed):
    # One-off takeover from the old per-turn naming hooks, which kept what they set per tab.
    old = os.path.expanduser("~/.config/herdr/hooks/state")

    def read(name):
        try:
            return open(os.path.join(old, name)).read().strip()
        except OSError:
            return None

    # Every current agent name came from those hooks, and agents have since moved between tabs.
    for a in listed:
        if a.get("name"):
            state["agents"][a["pane_id"]] = a["name"]
    for ws in {a["workspace_id"] for a in listed}:
        for t in herdr("tab", "list", "--workspace", ws)["result"]["tabs"]:
            label = read(f"tab-{t['tab_id'].replace(':', '-')}.last")
            if label and t.get("label") == label:
                state["tabs"][t["tab_id"]] = label
    state.pop("adopt_hook_names", None)


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE_FILE)


def ask_model(agents, tabs):
    parts = [INSTRUCTIONS, ""]
    for tab_id, tab in tabs.items():
        # Show only titles we set: a hand-picked or stale title would leak into other agents' names.
        parts.append(f"TAB {tab_id}" + (f" (current: {tab['label']})" if tab["claimable"] and not tab["label"].isdigit() else ""))
        for a in agents:
            if a["tab_id"] != tab_id:
                continue
            current = f", current: {a['name']}" if a["claimable"] and a["name"] else ""
            parts.append(f"  AGENT {a['pane_id']} ({a['kind']}, cwd {a['cwd']}{current})")
            parts.extend(f"    {m}" for m in a["messages"])
        parts.append("")
    out = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
         "--no-session-persistence", "--tools", ""],
        input="\n".join(parts), capture_output=True, text=True, timeout=180, cwd=STATE_DIR,
        env={**os.environ, "HERDR_NAME_CHILD": "1"},
    )
    if out.returncode != 0:
        raise RuntimeError(f"claude failed: {out.stderr.strip()[:300]}")
    result = json.loads(out.stdout)
    structured = result.get("structured_output")
    if structured is None:
        structured = json.loads(result.get("result", ""))
    return structured


def main():
    force = "--force" in sys.argv
    os.makedirs(STATE_DIR, exist_ok=True)
    state = load_state()

    listed = herdr("agent", "list")["result"]["agents"]
    if state.get("adopt_hook_names"):
        adopt_hook_names(state, listed)
    agents, changed = [], force
    for a in listed:
        session = (a.get("agent_session") or {}).get("value")
        kind = a.get("agent")
        if not session or kind not in ("claude", "codex"):
            continue
        path, msgs = (claude_messages if kind == "claude" else codex_messages)(session)
        if not msgs:
            continue
        mtime = os.path.getmtime(path)
        pane = a["pane_id"]
        current = a.get("name") or ""
        ours = state["agents"].get(pane)
        # Only rename a name we set, or none at all; a hand-picked name wins.
        claimable = not current or current == ours
        if state["seen"].get(pane) != [session, mtime] or not current:
            changed = changed or claimable
        agents.append({
            "pane_id": pane, "tab_id": a["tab_id"], "workspace_id": a["workspace_id"], "kind": kind,
            "cwd": a.get("foreground_cwd") or a.get("cwd") or "", "name": current, "claimable": claimable,
            "messages": msgs[-RECENT_MESSAGES:], "seen": [session, mtime],
        })

    if not agents or not changed:
        return

    tabs = {}
    for ws in {a["workspace_id"] for a in agents}:
        for t in herdr("tab", "list", "--workspace", ws)["result"]["tabs"]:
            if any(a["tab_id"] == t["tab_id"] for a in agents):
                label = t.get("label") or ""
                # Herdr's default label is the tab number; anything else we did not set was named by hand.
                claimable = label.isdigit() or label == state["tabs"].get(t["tab_id"])
                tabs[t["tab_id"]] = {"label": label, "claimable": claimable}

    reply = ask_model(agents, tabs)

    taken = {a["name"] for a in agents if a["name"] and not a["claimable"]}
    by_pane = {a["pane_id"]: a for a in agents}
    for item in reply.get("agents", []):
        a = by_pane.get(item.get("pane_id"))
        want = re.sub(r"[^a-z0-9_-]+", "-", (item.get("name") or "").lower()).strip("-")[:32]
        if not a or not a["claimable"] or not NAME_RE.match(want):
            continue
        candidate, i = want, 2
        while candidate in taken:
            candidate = f"{want[:29]}-{i}"
            i += 1
        taken.add(candidate)
        if candidate != a["name"]:
            try:
                herdr("agent", "rename", a["pane_id"], candidate)
                log(f"Renamed agent {a['pane_id']} to {candidate}.")
            except RuntimeError as e:
                log(f"Could not rename agent {a['pane_id']}: {e}.")
                continue
        state["agents"][a["pane_id"]] = candidate

    for item in reply.get("tabs", []):
        tab = tabs.get(item.get("tab_id"))
        want = " ".join((item.get("title") or "").split())[:40]
        if not tab or not tab["claimable"] or not want:
            continue
        if want != tab["label"]:
            try:
                herdr("tab", "rename", item["tab_id"], want)
                log(f"Renamed tab {item['tab_id']} to {want}.")
            except RuntimeError as e:
                log(f"Could not rename tab {item['tab_id']}: {e}.")
                continue
        state["tabs"][item["tab_id"]] = want

    for a in agents:
        state["seen"][a["pane_id"]] = a["seen"]
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # A failed run just waits for the next tick.
        log(f"Run failed: {e}.")
        sys.exit(1)
