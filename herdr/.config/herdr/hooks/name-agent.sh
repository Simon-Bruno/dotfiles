#!/bin/sh
# Names the Herdr agent and tab from whatever the agent tells us it is working on.
#
#   title  (Claude Code, Stop/SessionStart) - source is the terminal title Claude
#          reports. A title is a noun phrase, so the subject is at the END.
#   prompt (Codex, UserPromptSubmit) - records the prompt as the pending subject.
#          Codex reports only its cwd as a title, and a prompt is imperative, so the
#          subject is at the START. First substantive prompt of a session wins.
#   apply  (Codex, Stop) - applies the pending subject once the turn has settled.
#          Renaming mid-turn is unreliable: Herdr drops the name when it believes the
#          pane occupant was replaced, which a redrawing TUI can look like.
#   reset  (Codex, SessionStart) - forgets the pending subject for a new session.
#
# Both back off permanently once the agent or tab is renamed by hand.
set -eu

mode="${1:-title}"
from_apply=0

[ -z "${HERDR_NAME_CHILD:-}" ] || exit 0
[ "${HERDR_ENV:-}" = "1" ] || exit 0
[ -n "${HERDR_TAB_ID:-}" ] || exit 0
[ -n "${HERDR_PANE_ID:-}" ] || exit 0
command -v herdr >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

hook_input="$(cat 2>/dev/null || true)"

state_dir="${HOME}/.config/herdr/hooks/state"
mkdir -p "$state_dir" 2>/dev/null || exit 0
key="$(printf '%s' "$HERDR_TAB_ID" | tr ':' '-')"

if [ -n "${HERDR_NAME_DEBUG:-}" ] || [ -f "${state_dir}/.trace" ]; then
  log="${state_dir}/name-agent.log"
  printf '%s mode=%s pane=%s input=%s\n' "$(date +%H:%M:%S)" "$mode" "$HERDR_PANE_ID" "$hook_input" >> "$log" 2>/dev/null || true
  tail -n 40 "$log" > "$log.tmp" 2>/dev/null && mv "$log.tmp" "$log" 2>/dev/null || true
fi

# Subagents share the pane; only the main session owns these labels.
if [ -n "$hook_input" ]; then
  is_sub="$(printf '%s' "$hook_input" | jq -r 'if (.agent_id // null) == null then "no" else "yes" end' 2>/dev/null || echo no)"
  [ "$is_sub" = "no" ] || exit 0
fi

info="$(herdr agent get "$HERDR_PANE_ID" 2>/dev/null || true)"
[ -n "$info" ] || exit 0
name="$(printf '%s' "$info" | jq -r '.result.agent.name // empty' 2>/dev/null || true)"

pending="${state_dir}/pending-${key}.txt"

case "$mode" in
  emit)
    # Claude Code accepts a sessionTitle from a UserPromptSubmit hook, and it wins
    # over the cached AI title. Feed the refreshed one back so Claude's own title,
    # and therefore the terminal title, stops being stale.
    # Stdout on this event is injected into the prompt, so emit strict JSON or nothing.
    ov="${state_dir}/override-${key}.txt"
    [ -s "$ov" ] || exit 0
    t="$(tr -cd '\11\40-\176' < "$ov" | cut -c1-60)"
    [ -n "$t" ] || exit 0
    jq -cn --arg t "$t" '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",sessionTitle:$t}}' 2>/dev/null || true
    exit 0
    ;;
  reset)
    # Codex also fires SessionStart after compaction and on resume; only a fresh session starts over.
    src="$(printf '%s' "$hook_input" | jq -r '.source // "startup"' 2>/dev/null || echo startup)"
    [ "$src" = "startup" ] || [ "$src" = "clear" ] || exit 0
    rm -f "$pending" "${state_dir}/name-${key}.last" "${state_dir}/tab-${key}.last" 2>/dev/null || true
    exit 0
    ;;
  prompt)
    # Record only; the rename happens at end of turn. First subject wins.
    [ -f "$pending" ] && exit 0
    raw_prompt="$(printf '%s' "$hook_input" | jq -r '.prompt // empty' 2>/dev/null || true)"
    [ -n "$raw_prompt" ] || exit 0
    printf '%s' "$raw_prompt" > "$pending" 2>/dev/null || true
    exit 0
    ;;
  apply)
    # Already named and still ours, so nothing to do. An empty name means Herdr
    # dropped it, and re-applying heals that.
    [ -z "$name" ] || exit 0
    [ -f "$pending" ] || exit 0
    source_text="$(cat "$pending" 2>/dev/null || true)"
    mode=prompt
    from_apply=1
    ;;
  *)
    # Claude titles its session once and caches it, so refresh periodically from
    # the transcript. Detached, so the turn never waits on it.
    turns="${state_dir}/turns-${key}.count"
    n=$(( $(cat "$turns" 2>/dev/null || echo 0) + 1 ))
    printf '%s' "$n" > "$turns" 2>/dev/null || true
    if [ "$n" -ge "${HERDR_RETITLE_EVERY:-8}" ]; then
      printf '0' > "$turns" 2>/dev/null || true
      tr_path="$(printf '%s' "$hook_input" | jq -r '.transcript_path // empty' 2>/dev/null || true)"
      if [ -n "$tr_path" ]; then
        (setsid nohup sh "${HOME}/.config/herdr/hooks/retitle.sh" "$key" "$tr_path" >/dev/null 2>&1 &) || true
      fi
    fi
    override="${state_dir}/override-${key}.txt"
    if [ -s "$override" ]; then
      source_text="$(cat "$override" 2>/dev/null || true)"
    else
      source_text="$(printf '%s' "$info" | jq -r '.result.agent.terminal_title_stripped // empty' 2>/dev/null || true)"
    fi
    ;;
esac
[ -n "$source_text" ] || exit 0

parsed="$(printf '%s' "$source_text" | MODE="$mode" python3 -c '
import os, re, sys
STOP = {"the","a","an","of","for","to","and","in","on","with","my","our","from","into","at","by",
        "is","are","can","you","please","it","this","that","i","we","do","make","just","now",
        "why","what","how","when","where","which","some","any","all","be","been","will","should",
        "yes","no","ok","okay","yep","sure","thanks","thank","also","too","still","again","instead",
        "let","lets","try","keep","want","need","like","so","but","then","get"}
mode = os.environ.get("MODE", "title")
raw = " ".join(sys.stdin.read().split())[:400]
low = raw.lower()
ident = re.search(r"\b([a-z]{2,6}-\d{1,6})\b", low)
low = re.sub(r"\b[a-z]{2,6}-\d{1,6}\b", " ", low)
words = [w for w in re.split(r"[^a-z0-9]+", low) if w]
kept = [w for w in words if w not in STOP] or words
if mode == "prompt" and (len(words) < 4 or len(kept) < 2):
    print("")
    print("")
    raise SystemExit(0)
pick = kept[:2] if mode == "prompt" else kept[-2:]
slug = "-".join(pick) if pick else (ident.group(1) if ident else "")
slug = re.sub(r"^[^a-z]+", "", slug)[:32].strip("-")
label = raw if mode != "prompt" else " ".join(raw.split()[:8])
if len(label) > 60:
    label = label[:57].rstrip() + "..."
print(slug)
print(label)
' 2>/dev/null || true)"
slug="$(printf '%s' "$parsed" | sed -n 1p)"
label_want="$(printf '%s' "$parsed" | sed -n 2p)"
if [ -z "$slug" ]; then
  # Too little signal to name anything. Release the subject so a later prompt can claim.
  [ "${from_apply:-0}" = "1" ] && rm -f "$pending" 2>/dev/null
  exit 0
fi

# Agent name: claim while unset or still ours, then find a free variant.
name_state="${state_dir}/name-${key}.last"
if [ -z "$name" ] || { [ -f "$name_state" ] && [ "$(cat "$name_state" 2>/dev/null)" = "$name" ]; }; then
  if [ "$name" != "$slug" ]; then
    i=1
    candidate="$slug"
    while [ "$i" -le 9 ]; do
      if herdr agent rename "$HERDR_PANE_ID" "$candidate" >/dev/null 2>&1; then
        printf '%s' "$candidate" > "$name_state" 2>/dev/null || true
        break
      fi
      i=$((i + 1))
      candidate="${slug}-${i}"
    done
  fi
fi

# Tab label: claimed only while the tab is still numbered.
[ -n "$label_want" ] || exit 0
tab_state="${state_dir}/tab-${key}.last"
label="$(herdr tab list --workspace "${HERDR_WORKSPACE_ID:-}" 2>/dev/null \
  | jq -r --arg t "$HERDR_TAB_ID" '.result.tabs[] | select(.tab_id == $t) | .label' 2>/dev/null || true)"
[ "$label" = "$label_want" ] && exit 0
if [ -f "$tab_state" ]; then
  [ "$(cat "$tab_state" 2>/dev/null)" = "$label" ] || exit 0
else
  case "$label" in
    ''|*[!0-9]*) exit 0 ;;
  esac
fi
herdr tab rename "$HERDR_TAB_ID" "$label_want" >/dev/null 2>&1 || exit 0
printf '%s' "$label_want" > "$tab_state" 2>/dev/null || true
exit 0
