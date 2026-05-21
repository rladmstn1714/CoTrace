"""
Build dialogue turns for hybrid action extraction (CoGym-style event_log).

- SEND_TEAMMATE_MESSAGE(...): turn text = inner message= value only (same text the LLM sees as the utterance).
- START(task_description=..., query=...): turn text = **task_description** only (batched with teammate NL for Step 1a LLM).
- All other actions: turn text = "Calling Tool: " + original action string (no LLM for extraction).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _inner_of_outer_call(s: str, func_name: str) -> Optional[str]:
    """Return inside of FUNC_NAME( ... ) using paren depth from first '(' after func_name."""
    prefix = f"{func_name}("
    t = s.strip()
    if not t.startswith(prefix):
        return None
    i_open = len(func_name)  # index of '('
    depth = 0
    for j in range(i_open, len(t)):
        c = t[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return t[i_open + 1 : j]
    return None


def parse_send_teammate_message(action_str: str) -> Optional[str]:
    """
    If action_str is SEND_TEAMMATE_MESSAGE(message=...), return the message body only.
    Otherwise None. Unquoted bodies are returned as-is; quoted bodies are unwrapped.
    """
    inner = _inner_of_outer_call(action_str, "SEND_TEAMMATE_MESSAGE")
    if inner is None:
        return None
    inner = inner.strip()
    key = "message="
    if not inner.startswith(key):
        return None
    val = inner[len(key) :].strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    return val


def parse_start_task_description(action_str: str) -> Optional[str]:
    """
    If action_str is START(task_description=..., query=...), return the task_description value only
    (text before the first top-level `, query=`). Otherwise None.
    """
    inner = _inner_of_outer_call(action_str.strip(), "START")
    if inner is None:
        return None
    inner = inner.strip()
    key = "task_description="
    if not inner.startswith(key):
        return None
    rest = inner[len(key) :].lstrip()
    sep = ", query="
    idx = rest.find(sep)
    if idx >= 0:
        val = rest[:idx].strip()
    else:
        val = rest.strip()
    if not val:
        return None
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    return val


def _speaker_from_role(role: Any) -> str:
    if not isinstance(role, str):
        return "unknown"
    if role in ("agent", "assistant") or "agent" in role:
        return "assistant"
    if role.startswith("user"):
        return "user"
    return role


def build_hybrid_dialogue_from_event_log(data: Dict, dialogue_id: str) -> Dict:
    """
    Convert CoGym session dict (with event_log) into dialogue format for hybrid extraction.

    Each event_log row becomes one turn. meta.hybrid_kind is 'teammate_message', 'start_task', or 'tool'.
    """
    turns: List[Dict] = []
    for entry in data.get("event_log") or []:
        raw = entry.get("action")
        if not isinstance(raw, str) or not raw.strip():
            raw = entry.get("content")
        if not isinstance(raw, str):
            raw = ""
        raw = raw.strip()
        speaker = _speaker_from_role(entry.get("role"))
        msg = parse_send_teammate_message(raw)
        tid = len(turns)
        if msg is not None:
            turns.append(
                {
                    "turn_id": tid,
                    "speaker": speaker,
                    "text": msg,
                    "meta": {"hybrid_kind": "teammate_message", "raw_action": raw},
                }
            )
        else:
            start_body = parse_start_task_description(raw)
            if start_body is not None:
                turns.append(
                    {
                        "turn_id": tid,
                        "speaker": speaker,
                        "text": start_body,
                        "meta": {"hybrid_kind": "start_task", "raw_action": raw},
                    }
                )
            else:
                line = f"Calling Tool: {raw}" if raw else "Calling Tool: (empty)"
                turns.append(
                    {
                        "turn_id": tid,
                        "speaker": speaker,
                        "text": line,
                        "meta": {"hybrid_kind": "tool", "raw_action": raw},
                    }
                )
    return {
        "dialogue_id": dialogue_id,
        "turns": turns,
        "metadata": {"source": "cogym_event_log_hybrid"},
    }


def dialogue_id_from_path(path: str) -> str:
    """Stable id for outputs (JSON file stem, e.g. session_<uuid>)."""
    from pathlib import Path

    return Path(path).stem
