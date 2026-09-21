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
MODEL_EVERY_SECONDS = 300
RECENT_MESSAGES = 10
TERMINAL_LINES = 12
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

Tab title: only for tabs marked "needs a title". 2 to 6 words, at most 40 characters, plain text.
A tab with several agents: title the shared theme, or list the subjects briefly. Start with the issue key in
capitals (STLR-2349) when all its agents share one. A terminal tab has no agent: title what it is for, from
the running command and its output (for example "baresip SIP phone" or "Studio dev server"); for an idle
shell, go by its last commands. Other tabs are named after their agent automatically.

A "current" name or title is one you gave earlier. Keep it exactly unless the work has clearly moved on;
names that change every few minutes are worse than slightly imperfect ones.

Return every agent, and a title for every tab that needs one."""


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def herdr(*args):
    out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"herdr {' '.join(args)} failed")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def terminal(pane):
    """What a non-agent pane is doing: its foreground command, folder and last lines of output."""
    info = herdr("pane", "process-info", "--pane", pane["pane_id"])["result"]["process_info"]
    running = " ".join(p.get("cmdline", "")[:80] for p in info.get("foreground_processes", []))
    out = subprocess.run(["herdr", "pane", "read", pane["pane_id"], "--source", "recent-unwrapped",
                          "--lines", str(TERMINAL_LINES)], capture_output=True, text=True, timeout=30)
    lines = [" ".join(l.split())[:150] for l in out.stdout.splitlines() if l.strip()][-TERMINAL_LINES:]
    return {"pane_id": pane["pane_id"], "running": running,
            "cwd": pane.get("foreground_cwd") or pane.get("cwd") or "", "output": lines}


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
        header = f"TAB {tab_id}"
        if tab["needs_title"]:
            # Show only titles we set: a hand-picked or stale title would leak into agents' names.
            header += " (needs a title" + (f", current: {tab['title']}" if tab["title"] else "") + ")"
        parts.append(header)
        for a in agents:
            if a["tab_id"] != tab_id:
                continue
            current = f", current: {a['name']}" if a["claimable"] and a["name"] else ""
            parts.append(f"  AGENT {a['pane_id']} ({a['kind']}, cwd {a['cwd']}{current})")
            parts.extend(f"    {m}" for m in a["messages"])
        for t in tab.get("terminals", []):
            parts.append(f"  TERMINAL {t['pane_id']} (running: {t['running'] or 'nothing'}, cwd {t['cwd']})")
            parts.extend(f"    | {line}" for line in t["output"])
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
        if kind:
            # Shown in the sidebar as $kind. Re-sent every tick, since herdr drops it when the pane's occupant changes.
            try:
                herdr("pane", "report-metadata", a["pane_id"], "--source", "namer", "--token", f"kind={kind}")
            except RuntimeError as e:
                log(f"Could not label pane {a['pane_id']}: {e}.")
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

    names = {a["pane_id"]: a.get("name") or "" for a in listed}
    panes_by_tab = {}
    for a in listed:
        panes_by_tab.setdefault(a["tab_id"], []).append(a["pane_id"])

    tabs = {}
    for ws in [w["workspace_id"] for w in herdr("workspace", "list")["result"]["workspaces"]]:
        shells = {}
        for pn in herdr("pane", "list", "--workspace", ws)["result"]["panes"]:
            shells.setdefault(pn["tab_id"], []).append(pn)
        for t in herdr("tab", "list", "--workspace", ws)["result"]["tabs"]:
            panes = sorted(panes_by_tab.get(t["tab_id"], []))
            label = t.get("label") or ""
            terminals, fp = [], None
            if not panes:
                # A tab with no agent is titled from what its terminals run.
                if not (label.isdigit() or label == state["tabs"].get(t["tab_id"])):
                    continue
                terminals = [terminal(pn) for pn in shells.get(t["tab_id"], [])]
                # Retitle when the command or recent output changes, not on every new log line.
                fp = json.dumps([[x["running"], x["cwd"], x["output"][-3:]] for x in terminals])
            if not state.get("tab_labels_adopted"):
                state["tabs"][t["tab_id"]] = label  # One-off takeover of every tab that holds an agent.
            # Herdr's default label is the tab number; anything else we did not set was named by hand.
            claimable = label.isdigit() or label == state["tabs"].get(t["tab_id"])
            stored = state.setdefault("tab_titles", {}).get(t["tab_id"]) or {}
            title = stored.get("title") if stored.get("panes") == panes and stored.get("fp") == fp else None
            tabs[t["tab_id"]] = {"label": label, "claimable": claimable, "panes": panes, "terminals": terminals,
                                 "fp": fp, "needs_title": claimable and len(panes) != 1, "title": title}
    state["tab_labels_adopted"] = True

    # A tab that gained or lost agents needs a fresh shared title.
    changed = changed or any(t["needs_title"] and not t["title"] for t in tabs.values())
    if changed and (force or time.time() - state.get("last_model_call", 0) >= MODEL_EVERY_SECONDS):
        state["last_model_call"] = time.time()
        reply = ask_model(agents, {k: v for k, v in tabs.items()
                                   if v["terminals"] or any(a["tab_id"] == k for a in agents)})

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
            names[a["pane_id"]] = candidate

        for item in reply.get("tabs", []):
            tab = tabs.get(item.get("tab_id"))
            want = " ".join((item.get("title") or "").split())[:40]
            if tab and tab["needs_title"] and want:
                tab["title"] = want
                state["tab_titles"][item["tab_id"]] = {"panes": tab["panes"], "fp": tab["fp"], "title": want}

        for a in agents:
            state["seen"][a["pane_id"]] = a["seen"]

    # Every tick: a lone agent's tab carries its name, a shared tab its shared title.
    for tab_id, tab in tabs.items():
        want = names[tab["panes"][0]] if len(tab["panes"]) == 1 else tab["title"]
        if not tab["claimable"] or not want:
            continue
        if want != tab["label"]:
            try:
                herdr("tab", "rename", tab_id, want)
                log(f"Renamed tab {tab_id} to {want}.")
            except RuntimeError as e:
                log(f"Could not rename tab {tab_id}: {e}.")
                continue
        state["tabs"][tab_id] = want

    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # A failed run just waits for the next tick.
        log(f"Run failed: {e}.")
        sys.exit(1)
