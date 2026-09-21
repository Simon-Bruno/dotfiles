#!/bin/sh
# Refreshes a stale session label. Claude generates its session title once and
# caches it, so a long session drifts away from what it is actually doing.
# Recent prompts alone are too conversational to slug well, so this asks Haiku
# for a short subject and writes it as an override for name-agent.sh.
#
# Runs detached and throttled; it never blocks a turn.
set -eu

key="${1:-}"
transcript="${2:-}"
[ -n "$key" ] || exit 0
[ -f "$transcript" ] || exit 0
[ -z "${HERDR_NAME_CHILD:-}" ] || exit 0   # never recurse into ourselves
command -v claude >/dev/null 2>&1 || exit 0

state_dir="${HOME}/.config/herdr/hooks/state"
recent="$(python3 - "$transcript" <<'PY' 2>/dev/null || true
import json, sys
rows = []
try:
    for line in open(sys.argv[1], encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
except Exception:
    raise SystemExit(0)
out = []
for r in rows:
    if r.get("type") != "user":
        continue
    c = r.get("message", {}).get("content")
    if isinstance(c, str):
        txt = c
    elif isinstance(c, list):
        txt = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    else:
        continue
    txt = " ".join(txt.split())
    if txt and not txt.startswith("<") and not txt.startswith("[Request interrupted"):
        out.append(txt[:200])
print("\n".join(out[-10:]))
PY
)"
[ -n "$recent" ] || exit 0

title="$(printf '%s\n\n%s\n' \
  "Recent user messages from a coding session, oldest first:" "$recent" \
  | HERDR_NAME_CHILD=1 claude -p --model haiku \
      "Reply with ONLY a 2-5 word title naming what this session is working on now. Weight the most recent messages. No quotes, no punctuation, no preamble." \
      2>/dev/null | head -1 | tr -d '"' | cut -c1-60 || true)"
title="$(printf '%s' "$title" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

title="$(printf '%s' "$title" | tr -cd '\11\40-\176')"
[ -n "$title" ] || exit 0   # nothing usable, leave the old label alone
printf '%s' "$title" > "${state_dir}/override-${key}.txt" 2>/dev/null || true
