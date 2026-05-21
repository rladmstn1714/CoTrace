#!/usr/bin/env python3
"""
V4 pipeline logic (refactored_req_based_v5_publish bundle).

Efficient unified extraction.

Architecture (vs V3):
  Step 0:  Normalize input                       (no LLM)
  Step 1:  Unified block extraction               (1 LLM call per block → outcomes + actions + req ops)
  Step 1b: Intention extraction                   (1 LLM call total)
  Step 2a: Embedding-based pair selection          (same as V3 Step 2, no LLM)
  Step 2b: Batch relationship labeling             (1 LLM call per outcome, not per pair)
  Step 3:  Contribution analysis                   (no LLM, same math as V3 Step 4)

Typical call count for 40-turn dialogue (block_size=4, 5 outcomes, ~120 pairs):
  V3: ~148 LLM calls    V4: ~16 LLM calls
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    import json_repair
except ImportError:
    json_repair = None  # optional: pip install json-repair
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict
from tqdm import tqdm
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

try:
    from dotenv import load_dotenv
    _here = Path(__file__).resolve().parent
    for env_path in (
        _here / ".env",
        _here.parent / ".env",
        _here.parent.parent / ".env",
    ):
        if env_path.exists():
            load_dotenv(env_path)
            break
    else:
        load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from utils.helpers import save_json, parse_llm_json_response, format_turn_context, format_turn_context_for_indices
from utils.dialogue_loader import load_dialogues
from config.prompts import (
    BLOCK_ACTION_EXTRACTION_PROMPT,
    BLOCK_ACTION_EXTRACTION_PROMPT_SHARECHAT,
    GLOBAL_OUTCOME_EXTRACTION_PROMPT,
    OUTCOME_REQUIREMENT_EXTRACTION_PROMPT,
    OUTCOME_REQUIREMENT_EXTRACTION_BATCH_PROMPT,
    INTENTION_EXTRACTION_PROMPT,
    REQUIREMENT_ACTION_LABELING_PROMPT,
    REQUIREMENT_ACTION_LABELING_BATCH_PROMPT,
)
from config.models import get_provider_and_model, chat_completion, get_openai_client

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


# =============================================================================
# Logged LLM Client (same as V3 but simplified)
# =============================================================================

class LoggedLLMClient:
    """Logged LLM client using config.models.chat_completion."""

    def __init__(self, provider: str, model: str, log_file: str, api_key: Optional[str] = None):
        self.provider = provider
        self.model = model
        self.log_file = log_file
        self.api_key = api_key
        self.log_entries = []

    def call(self, messages: list, section: str = "unknown", temperature: float = 1) -> str:
        """Make LLM call and return content string."""
       
        result = chat_completion(
            messages=messages, model=self.model,
            temperature=temperature, provider=self.provider,
            api_key=self.api_key,
        )
        content = result.get("content", "")
        usage = result.get("usage")
        model_used = result.get("model", self.model)

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "section": section,
            "model": model_used,
            "messages": messages,
            "response": {"content": content, "model": model_used, "usage": usage},
        }
        self.log_entries.append(log_entry)

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*80}\nSection: {section}\nTimestamp: {log_entry['timestamp']}\n")
            f.write(f"Model: {model_used}\n{'='*80}\n\nPROMPT:\n{'-'*80}\n")
            for msg in messages:
                f.write(f"{msg.get('role','user').upper()}:\n{msg.get('content','')}\n\n")
            f.write(f"\nRESPONSE:\n{'-'*80}\n{content}\n\n")
            if usage:
                f.write(f"Usage: {usage}\n")
        return content

    def save_logs(self, output_dir: str):
        log_json = self.log_file.replace(".txt", ".json")
        save_json(self.log_entries, log_json)

        # Save monitor file (section-grouped, human-readable)
        monitor_path = os.path.join(output_dir, "llm_monitor.txt")
        self._save_monitor_file(monitor_path)

        # Summary
        total_calls = len(self.log_entries)
        total_prompt = sum((e.get("response",{}).get("usage") or {}).get("prompt_tokens",0) for e in self.log_entries)
        total_completion = sum((e.get("response",{}).get("usage") or {}).get("completion_tokens",0) for e in self.log_entries)
        print(f"  LLM calls: {total_calls}, prompt tokens: {total_prompt}, completion tokens: {total_completion}")

    def _save_monitor_file(self, output_path: str):
        """Save section-grouped LLM raw input/output monitor (same format as V3)."""
        by_section = defaultdict(list)
        for i, entry in enumerate(self.log_entries):
            sec = entry.get("section", "unknown")
            by_section[sec].append({**entry, "call_index": i + 1})

        usage_totals = {}
        for e in self.log_entries:
            u = (e.get("response") or {}).get("usage") or {}
            if isinstance(u, dict):
                usage_totals["prompt_tokens"] = usage_totals.get("prompt_tokens", 0) + u.get("prompt_tokens", 0)
                usage_totals["completion_tokens"] = usage_totals.get("completion_tokens", 0) + u.get("completion_tokens", 0)
                usage_totals["total_tokens"] = usage_totals.get("total_tokens", 0) + u.get("total_tokens", 0)

        lines = [
            "# " + "=" * 78,
            "# LLM Raw Input/Output Monitor",
            "# Generated: " + datetime.now().isoformat(),
            f"# Total API calls: {len(self.log_entries)}",
            f"# Total input tokens: {usage_totals.get('prompt_tokens', 0)}",
            f"# Total output tokens: {usage_totals.get('completion_tokens', 0)}",
            "# " + "=" * 78,
            "",
        ]
        for section in sorted(by_section.keys()):
            entries = by_section[section]
            lines += ["", "=" * 80, f"SECTION: {section}", f"  (Calls in this section: {len(entries)})", "=" * 80]
            for entry in entries:
                lines += ["", "-" * 80, f"  [Call {entry.get('call_index', 0)}] {entry.get('timestamp', '')}", "-" * 80, "", "  INPUT:"]
                for msg in entry.get("messages", []):
                    lines.append(f"    [{msg.get('role', 'user').upper()}]")
                    for line in str(msg.get("content", "")).split("\n"):
                        lines.append(f"      {line}")
                lines += ["", "  OUTPUT:"]
                for line in str((entry.get("response") or {}).get("content", "")).split("\n"):
                    lines.append(f"    {line}")
                usage = (entry.get("response") or {}).get("usage")
                if usage:
                    lines += ["", f"  Usage: {usage}"]

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


# =============================================================================
# JSON Parsing Helpers
# =============================================================================

def _parse_json_response(text: str) -> dict:
    """Parse LLM JSON response, stripping markdown fences and leading prose."""
    text = (text or "").strip()
    # Strip markdown fences
    if "```json" in text:
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    elif "```" in text:
        m = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    # Find first JSON value
    text = _extract_first_json(text)
    if text.strip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if json_repair is not None:
                try:
                    return json_repair.loads(text)
                except Exception:
                    pass
            raise
    return parse_llm_json_response(text)


def _extract_first_json(text: str) -> str:
    """Extract the first complete JSON object/array from text."""
    text = (text or "").strip()
    if not text:
        return text
    i = 0
    while i < len(text):
        c = text[i]
        if c in ("[", "{"):
            open_c, close_c = ("[", "]") if c == "[" else ("{", "}")
            depth = 0
            for j in range(i, len(text)):
                if text[j] == open_c:
                    depth += 1
                elif text[j] == close_c:
                    depth -= 1
                    if depth == 0:
                        return text[i:j + 1]
            break
        elif c in ('"', "'"):
            quote = c
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        i += 1
    return text


_CANONICAL_ROLES = {"SHAPER", "EXECUTOR", "OTHER"}

def _normalize_role(r: str) -> str:
    if not r or not str(r).strip():
        return "OTHER"
    u = str(r).strip().upper()
    return u if u in _CANONICAL_ROLES else "OTHER"


def _norm_action_id(aid: str) -> str:
    """Normalize action_id: strip brackets so \"[0-1]\" and \"0-1\" match step05."""
    if not isinstance(aid, str) or not aid:
        return aid or ""
    return aid.strip().strip("[]").strip()


# Step 3: geometric decay on the influential-action chain (req origin excluded).
# SHAPER uses raw relationship_score * alpha**distance; alpha_direct >> alpha_indirect (both < 1).
CONTRIB_ALPHA_DIRECT = 0.8
CONTRIB_ALPHA_INDIRECT = 0.4


# =============================================================================
# Step 0: Normalize Input
# =============================================================================

def run_step0(input_file: str, output_dir: str) -> Tuple[Dict, str]:
    """Load and normalize dialogue."""
    print(f"Loading dialogue from {input_file}...")
    dialogues = load_dialogues(input_file)
    if not dialogues:
        raise ValueError("No dialogues found in file")
    dialogue = dialogues[0]
    dialogue_id = dialogue.get("dialogue_id", "unknown")
    print(f"  Dialogue: {dialogue_id}, Turns: {len(dialogue.get('turns', []))}")

    step0_file = os.path.join(output_dir, "step0_input.json")
    if not os.path.exists(step0_file):
        save_json(dialogue, step0_file)
    return dialogue, dialogue_id


# =============================================================================
# Step 1: Unified Block Extraction
# =============================================================================

def _format_actions_summary(actions: list) -> str:
    """Format all actions as compact block for the global outcome+req extraction."""
    lines = []
    for a in actions:
        aid = a.get("action_id", "")
        turn = a.get("turn_id", 0)
        speaker = a.get("speaker", "?")
        atype = a.get("action_type", "")
        atext = a.get("action_text", "") or ""
        role = a.get("role", "OTHER")
        lines.append(f"[{aid}] Turn {turn} ({speaker}) {atype}: {atext} [role: {role}]")
    return "\n".join(lines)


def extract_block_actions(
    turns: list, start_idx: int, end_idx: int,
    client: LoggedLLMClient, model: str,
    *,
    use_sharechat_block_prompt: bool = False,
    turn_indices: Optional[List[int]] = None,
) -> list:
    """Step 1a: Extract actions only from one block. Lightweight per-block call.

    If turn_indices is set, formats those turns (any order, non-contiguous) in one block and
    ignores start_idx/end_idx. Empty turn_indices returns [] without calling the LLM.
    """
    if turn_indices is not None:
        if not turn_indices:
            return []
        dialogue_block = format_turn_context_for_indices(turns, turn_indices)
        span_label = f"indices {turn_indices}"
    else:
        dialogue_block = format_turn_context(turns, start_idx, end_idx)
        span_label = f"turns {start_idx}-{end_idx-1}"

    tmpl = (
        BLOCK_ACTION_EXTRACTION_PROMPT_SHARECHAT
        if use_sharechat_block_prompt
        else BLOCK_ACTION_EXTRACTION_PROMPT
    )
    prompt = tmpl.format(dialogue_block=dialogue_block)

    label = "ShareChat" if use_sharechat_block_prompt else "default"
    content = client.call(
        messages=[
            {"role": "system", "content": "You extract actions from dialogue blocks. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
        section=f"Step 1a - Action Extraction ({label}, {span_label})",
        temperature=1,
    )

    try:
        result = _parse_json_response(content)
    except Exception as e:
        print(f"  Warning: JSON parse failed for block {start_idx}-{end_idx-1}: {e}")
        return []

    actions = result.get("actions", []) if isinstance(result, dict) else result if isinstance(result, list) else []
    return actions


def extract_global_outcomes(
    all_actions: list, dialogue_summary: str,
    client: LoggedLLMClient, model: str,
) -> dict:
    """Step 1b: Single global call — outcomes + action_to_outcome only."""
    actions_block = _format_actions_summary(all_actions)

    prompt = GLOBAL_OUTCOME_EXTRACTION_PROMPT.format(
        dialogue_summary=dialogue_summary or "(not yet determined)",
        actions_block=actions_block,
    )

    content = client.call(
        messages=[
            {"role": "system", "content": "You extract outcomes from dialogue actions and assign each action to one outcome. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
        section="Step 1b - Global Outcome Extraction",
        temperature=1,
    )

    try:
        result = _parse_json_response(content)
    except Exception as e:
        print(f"  Warning: JSON parse failed for outcome extraction: {e}")
        return {"outcomes": [], "action_to_outcome": {}, "dialogue_summary": ""}

    return result


def _format_actions_with_outcome(actions: list) -> str:
    """Format actions with bound_outcome_id for requirement extraction prompt."""
    lines = []
    for a in actions:
        aid = a.get("action_id", "")
        turn = a.get("turn_id", 0)
        speaker = a.get("speaker", "?")
        atype = a.get("action_type", "")
        atext = a.get("action_text", "") or ""
        role = a.get("role", "OTHER")
        oid = a.get("bound_outcome_id", "")
        lines.append(f"[{aid}] Turn {turn} ({speaker}) [{oid}] {atype}: {atext} [role: {role}]")
    return "\n".join(lines)


def extract_requirements_for_outcome(
    outcome: dict, actions_for_outcome: list,
    client: LoggedLLMClient, model: str,
) -> list:
    """Step 1c: One call per outcome — requirement_ops for this outcome only."""
    oid = outcome.get("outcome_id", "")
    desc = outcome.get("description", outcome.get("outcome", ""))
    actions_block = _format_actions_summary(actions_for_outcome)

    prompt = OUTCOME_REQUIREMENT_EXTRACTION_PROMPT.format(
        outcome_id=oid,
        outcome_description=desc,
        actions_block=actions_block,
    )

    content = client.call(
        messages=[
            {"role": "system", "content": "You extract requirements for one outcome from its actions. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
        section=f"Step 1c - Requirement Extraction ({oid})",
        temperature=1,
    )

    try:
        result = _parse_json_response(content)
        ops = result.get("requirement_ops", [])
        for op in ops:
            op["bound_outcome_id"] = op.get("bound_outcome_id") or oid
        return ops
    except Exception as e:
        print(f"  Warning: JSON parse failed for {oid}: {e}")
        return []


def extract_requirements_for_outcomes_batch(
    batch: list, client: LoggedLLMClient, model: str,
) -> list:
    """Step 1c (batched): One call for multiple (outcome, actions_for_outcome) or (outcome, actions, hierarchy_label). Returns flat requirement_ops."""
    if not batch:
        return []
    blocks = []
    valid_oids = []
    for item in batch:
        if len(item) >= 3 and item[2] is not None:
            outcome, actions_for_outcome, hierarchy_label = item[0], item[1], item[2]
        else:
            outcome, actions_for_outcome = item[0], item[1]
            hierarchy_label = None
        oid = outcome.get("outcome_id", "")
        desc = outcome.get("description", outcome.get("outcome", ""))
        actions_block = _format_actions_summary(actions_for_outcome)
        header = f"=== OUTCOME {oid} [{hierarchy_label}] ===" if hierarchy_label else f"=== OUTCOME {oid} ==="
        blocks.append(
            f"{header}\n{desc}\n\n=== ACTIONS FOR {oid} ===\n{actions_block}"
        )
        valid_oids.append(oid)
    outcomes_blocks = "\n\n".join(blocks)
    prompt = OUTCOME_REQUIREMENT_EXTRACTION_BATCH_PROMPT.format(
        n_outcomes=len(batch),
        outcomes_blocks=outcomes_blocks,
    )
    content = client.call(
        messages=[
            {"role": "system", "content": "You extract requirements for multiple outcomes. Output JSON only. Each op must have bound_outcome_id set."},
            {"role": "user", "content": prompt},
        ],
        section=f"Step 1c - Requirement Extraction BATCH ({len(batch)} outcomes)",
        temperature=1,
    )
    try:
        result = _parse_json_response(content)
        ops = result.get("requirement_ops", [])
        for op in ops:
            oid = op.get("bound_outcome_id", "")
            if not oid and valid_oids:
                op["bound_outcome_id"] = valid_oids[0]
            else:
                op["bound_outcome_id"] = oid or valid_oids[0]
        return ops
    except Exception as e:
        print(f"  Warning: JSON parse failed for requirement batch: {e}")
        return []


def _run_step1b1c_from_actions(
    dialogue: dict,
    dialogue_id: str,
    all_actions: List[dict],
    client: LoggedLLMClient,
    model: str,
    output_dir: str,
    extra_step05_metadata: Optional[dict] = None,
    include_requirements: bool = True,
    requirements_client: Optional[LoggedLLMClient] = None,
    requirements_model: Optional[str] = None,
) -> Tuple[dict, dict, dict]:
    """
    Step 1b (global outcomes + bind actions) and optionally Step 1c (requirements), then save step05/step1.
    Uses GLOBAL_OUTCOME_EXTRACTION_PROMPT via extract_global_outcomes (unchanged).
    """
    dialogue_summary = dialogue.get("dialogue_summary", "")

    # =========================================================================
    # Step 1b: Global outcome extraction (single call)
    # =========================================================================
    print("\n" + "="*80)
    print("STEP 1b: Global Outcome Extraction")
    print("="*80)

    global_result = extract_global_outcomes(
        all_actions, dialogue_summary, client, model,
    )

    gs = (global_result.get("dialogue_summary") or "").strip()
    if gs:
        dialogue_summary = gs

    # --- Process outcomes ---
    all_outcomes = []
    outcome_dict = {}
    outcome_actions = {}

    for o in global_result.get("outcomes", []):
        oid = o.get("outcome_id", "")
        outcome = {
            "outcome_id": oid,
            "description": o.get("outcome", ""),
            "outcome_type": "artifact",
            "confidence": o.get("confidence", 0.8),
            "created_at_turn": o.get("turn_id", 0),
            "parent_outcome_id": o.get("parent_outcome_id"),
            "child_outcome_ids": o.get("child_outcome_ids", []),
            "related_outcome_ids": o.get("related_outcome_ids", []),
        }
        all_outcomes.append(outcome)
        outcome_dict[oid] = outcome
        outcome_actions[oid] = []

    # Sync parent/child bidirectionally
    for outcome in all_outcomes:
        oid = outcome["outcome_id"]
        parent_oid = outcome.get("parent_outcome_id")
        if parent_oid and parent_oid in outcome_dict:
            parent_children = outcome_dict[parent_oid].setdefault("child_outcome_ids", [])
            if oid not in parent_children:
                parent_children.append(oid)
        for cid in outcome.get("child_outcome_ids", []):
            if cid in outcome_dict:
                outcome_dict[cid]["parent_outcome_id"] = oid
        for rid in outcome.get("related_outcome_ids", []):
            if rid in outcome_dict:
                rel_list = outcome_dict[rid].setdefault("related_outcome_ids", [])
                if oid not in rel_list:
                    rel_list.append(oid)

    # --- Bind actions to outcomes ---
    action_to_outcome = global_result.get("action_to_outcome", {})
    action_lookup = {a["action_id"]: a for a in all_actions}

    for a in all_actions:
        aid = a["action_id"]
        oid = action_to_outcome.get(aid, "")
        a["bound_outcome_id"] = oid
        if oid in outcome_actions:
            outcome_actions[oid].append(a)

    # Attach actions directly to each outcome (outcome-first structure)
    for outcome in all_outcomes:
        oid = outcome["outcome_id"]
        outcome["actions"] = outcome_actions.get(oid, [])

    # =========================================================================
    # Step 1c: Requirement extraction
    #
    # Requirement extraction batching strategy:
    # - outcomes that share the same parent_outcome_id are extracted together
    # - specifically: batch the parent outcome with all its direct children
    #
    # NOTE: We keep ALL requirement prompts unchanged; only the batching/selection
    # logic is updated to reduce LLM calls and to provide parent-context together.
    # =========================================================================
    requirement_ops: List[dict] = []
    req_client = requirements_client or client
    req_model = requirements_model or model
    if include_requirements:
        print("\n" + "="*80)
        print(f"STEP 1c: Requirement Extraction (grouped by parent outcome) [model={req_model}]")
        print("="*80)

        # parent_outcome_id -> [child_outcome_id, ...]
        parent_to_children: Dict[str, List[str]] = defaultdict(list)
        for outcome in all_outcomes:
            oid = outcome.get("outcome_id", "")
            parent_oid = outcome.get("parent_outcome_id")
            if oid and parent_oid:
                parent_to_children[str(parent_oid)].append(oid)

        # Deterministic shallow->deep processing for parents (helps keep call ordering stable).
        depth_cache: Dict[str, int] = {}

        def _outcome_depth(oid: str) -> int:
            if oid in depth_cache:
                return depth_cache[oid]
            if not oid or oid not in outcome_dict:
                depth_cache[oid] = 0
                return 0
            parent_oid = outcome_dict[oid].get("parent_outcome_id")
            if not parent_oid or parent_oid not in outcome_dict:
                depth_cache[oid] = 0
                return 0
            d = _outcome_depth(str(parent_oid)) + 1
            depth_cache[oid] = d
            return d

        processed_outcomes: set = set()

        for parent_oid in sorted(parent_to_children.keys(), key=lambda x: _outcome_depth(str(x))):
            child_ids = parent_to_children.get(parent_oid) or []
            # Batch items contain (outcome, actions_for_outcome). Keep original semantics:
            # - skip outcomes with no actions bound
            # - but allow a parent to be included even if already processed (we filter ops).
            batch_items: List[tuple] = []
            batch_oids: List[str] = []

            # Parent outcome first (gives it precedence in the batch formatting).
            parent_outcome = outcome_dict.get(str(parent_oid))
            if parent_outcome:
                parent_oid_str = parent_outcome.get("outcome_id", str(parent_oid))
                parent_actions = outcome_actions.get(parent_oid_str, [])
                if parent_actions:
                    batch_items.append((parent_outcome, parent_actions))
                    batch_oids.append(parent_oid_str)

            for cid in child_ids:
                if cid == parent_oid:
                    continue
                child_outcome = outcome_dict.get(cid)
                if not child_outcome:
                    continue
                child_actions = outcome_actions.get(cid, [])
                if not child_actions:
                    continue
                batch_items.append((child_outcome, child_actions))
                batch_oids.append(cid)

            if not batch_items:
                continue

            # If every outcome in this batch was already extracted, skip the call.
            # (Filtering ops below will also protect against duplicates.)
            if all(oid in processed_outcomes for oid in batch_oids):
                continue

            print(f"  Parent {parent_oid}: extracting {[o for o in batch_oids if o not in processed_outcomes]}")
            ops = extract_requirements_for_outcomes_batch(batch_items, req_client, req_model)

            # Filter out duplicates for outcomes we already extracted.
            for op in ops:
                boid = (op or {}).get("bound_outcome_id")
                if boid and boid in processed_outcomes:
                    continue
                requirement_ops.append(op)

            for oid in batch_oids:
                processed_outcomes.add(oid)

        # Extract remaining outcomes not covered by parent batching.
        for outcome in all_outcomes:
            oid = outcome.get("outcome_id", "")
            if oid in processed_outcomes:
                continue
            actions_for_outcome = outcome_actions.get(oid, [])
            if not actions_for_outcome:
                continue
            print(f"  {oid}: {len(actions_for_outcome)} actions")
            ops = extract_requirements_for_outcome(outcome, actions_for_outcome, req_client, req_model)
            requirement_ops.extend(ops)
            processed_outcomes.add(oid)
    else:
        print("\n  (Step 1c skipped: requirement extraction disabled)")

    # --- Build action reference resolver (global) ---
    _action_ref_map = {a["action_id"]: a["action_id"] for a in all_actions}
    _turn_to_canonical = defaultdict(list)
    for a in all_actions:
        _turn_to_canonical[a["turn_id"]].append(a["action_id"])

    def _resolve_action_refs(raw_ids: list) -> list:
        resolved = []
        for ref in (raw_ids or []):
            ref = str(ref).strip()
            if not ref:
                continue
            if ref in _action_ref_map:
                resolved.append(_action_ref_map[ref])
                continue
            try:
                tid = int(ref)
                if tid in _turn_to_canonical:
                    resolved.extend(_turn_to_canonical[tid])
                    continue
            except (ValueError, TypeError):
                pass
            for sep in ("_", " ", "."):
                if sep in ref:
                    parts = ref.split(sep, 1)
                    try:
                        tid = int(parts[0])
                        idx = int(parts[1]) + 1
                        candidate = f"{tid}-{idx}"
                        if candidate in _action_ref_map:
                            resolved.append(candidate)
                            break
                        if tid in _turn_to_canonical:
                            resolved.extend(_turn_to_canonical[tid])
                            break
                    except (ValueError, TypeError):
                        pass
            if not resolved or resolved[-1] != ref:
                for cid in _action_ref_map:
                    if cid.startswith(ref.split("-")[0].split("_")[0].split(" ")[0] + "-"):
                        resolved.append(cid)
                        break
        seen = set()
        return [r for r in resolved if not (r in seen or seen.add(r))]

    # --- Process requirement ops (req_id_map is outcome-scoped: (outcome_id, local_id) -> global_id) ---
    all_requirements = {}
    all_operations = []
    req_counter = 1
    req_id_map = {}  # (outcome_id, local_id) -> global_id
    req_version_counter = {}

    for op in requirement_ops:
        op_type = op.get("op")
        if not op_type:
            continue

        canonical_oid = op.get("bound_outcome_id", "")
        op["outcome_id"] = canonical_oid

        for key in ("creation_action_ids", "contributing_action_ids", "implementation_action_ids"):
            op[key] = _resolve_action_refs(op.get(key, []) or [])

        if op_type == "create":
            fields = op.get("fields", {})
            canonical_req_id = f"r{req_counter}"
            req_counter += 1

            local_id = op.get("req_id", "")
            if local_id:
                req_id_map[(canonical_oid, local_id)] = canonical_req_id

            origin_turn = 0
            for aid_ref in op.get("creation_action_ids", []):
                act = action_lookup.get(aid_ref)
                if act:
                    origin_turn = act.get("turn_id", 0)
                    break

            req = {
                "req_id": canonical_req_id,
                "text": fields.get("text", ""),
                "type": fields.get("type", "constraint"),
                "status": "active",
                "origin_action_ids": op.get("creation_action_ids", []),
                "creation_action_ids": op.get("creation_action_ids", []),
                "contributing_action_ids": op.get("contributing_action_ids", []),
                "implementation_action_ids": op.get("implementation_action_ids", []),
                "outcome_id": canonical_oid,
                "origin_turn_id": origin_turn,
                "created_at": origin_turn,
                "operation_type": "create",
                "related_to": [req_id_map.get((canonical_oid, r), req_id_map.get(r, r)) for r in (op.get("related_to") or [])],
                "explicit_or_implicit": op.get("explicit_or_implicit", "explicit"),
                "rationale": op.get("rationale", ""),
            }
            all_requirements[canonical_req_id] = req
            op["req_id"] = canonical_req_id

        elif op_type == "revise":
            related = op.get("related_to", [])
            if isinstance(related, str):
                related = [related]
            if not related:
                continue
            target_id = req_id_map.get((canonical_oid, related[0]), req_id_map.get(related[0], related[0]))
            base_id = re.match(r"^(.+?)(?:_\d+)?$", target_id).group(1) if target_id else target_id
            target_req = all_requirements.get(target_id) or all_requirements.get(base_id)
            if not target_req:
                continue

            ver = req_version_counter.get(base_id, 1)
            req_version_counter[base_id] = ver + 1
            new_req_id = f"{base_id}_{ver}"

            fields = op.get("fields", {})
            origin_turn = 0
            for aid_ref in op.get("creation_action_ids", []):
                act = action_lookup.get(aid_ref)
                if act:
                    origin_turn = act.get("turn_id", 0)
                    break

            new_req = {
                "req_id": new_req_id,
                "text": fields.get("text", "") or target_req.get("text", ""),
                "type": fields.get("type", target_req.get("type", "constraint")),
                "status": "active",
                "origin_action_ids": op.get("creation_action_ids", []),
                "creation_action_ids": op.get("creation_action_ids", []),
                "contributing_action_ids": op.get("contributing_action_ids", []),
                "implementation_action_ids": op.get("implementation_action_ids", []),
                "outcome_id": target_req.get("outcome_id", canonical_oid),
                "origin_turn_id": origin_turn,
                "created_at": origin_turn,
                "operation_type": "revise",
                "related_to": [target_req.get("req_id", target_id)],
                "explicit_or_implicit": op.get("explicit_or_implicit", "explicit"),
                "rationale": op.get("rationale", ""),
            }
            all_requirements[new_req_id] = new_req
            op["req_id"] = new_req_id
            op["related_to"] = [target_req.get("req_id", target_id)]

        elif op_type == "delete":
            related = op.get("related_to", [])
            if isinstance(related, str):
                related = [related]
            resolved = [req_id_map.get((canonical_oid, r), req_id_map.get(r, r)) for r in related]
            op["related_to"] = resolved
            op["req_id"] = resolved[0] if resolved else None

        all_operations.append(op)

    # --- Build thread structure ---
    threads_dict = {}
    for i, outcome in enumerate(all_outcomes, 1):
        oid = outcome["outcome_id"]
        outcome_reqs = [rid for rid, r in all_requirements.items() if r.get("outcome_id") == oid]
        threads_dict[str(i)] = {
            "thread_id": i,
            "outcome_id": oid,
            "outcome": outcome.get("description", ""),
            "created_at": outcome.get("created_at_turn", 0),
            "requirements": outcome_reqs,
            "parent_outcome_id": outcome.get("parent_outcome_id"),
            "child_outcome_ids": outcome.get("child_outcome_ids", []),
            "related_outcome_ids": outcome.get("related_outcome_ids", []),
        }
        for rid in outcome_reqs:
            if rid in all_requirements:
                all_requirements[rid]["thread_id"] = i

    # --- Build operations_log ---
    ops_by_turn = defaultdict(list)
    for op in all_operations:
        req = all_requirements.get(op.get("req_id"))
        turn_id = req.get("origin_turn_id", 0) if req else 0
        entry = dict(op)
        entry["thread_id"] = req.get("thread_id") if req else None
        ops_by_turn[turn_id].append(entry)

    operations_log = [
        {"turn_id": tid, "ops": ops}
        for tid, ops in sorted(ops_by_turn.items())
    ]

    # --- Save outputs (outcome-first: each outcome has its actions nested) ---
    meta = {
        "num_outcomes": len(all_outcomes),
        "num_outcome_versions": 0,
        "num_actions": len(all_actions),
        "model": model,
        "requirements_model": req_model if include_requirements else None,
        "step1c_requirements": include_requirements,
    }
    if extra_step05_metadata:
        meta.update(extra_step05_metadata)

    step05_output = {
        "dialogue_id": dialogue_id,
        "dialogue_summary": dialogue_summary,
        "outcomes": all_outcomes,
        "outcome_versions": [],
        "outcome_actions": outcome_actions,
        "all_actions": all_actions,
        "metadata": meta,
    }
    save_json(step05_output, os.path.join(output_dir, "step05_output.json"))

    step1_output = {
        "dialogue_id": dialogue_id,
        "requirements": all_requirements,
        "threads": threads_dict,
        "operations_log": operations_log,
        "others": [],
        "metadata": {
            "num_requirements": len(all_requirements),
            "num_threads": len(threads_dict),
            "num_operations": sum(len(e["ops"]) for e in operations_log),
            "model": model,
        },
    }
    save_json(step1_output, os.path.join(output_dir, "step1_output.json"))

    # Outcome-action map
    oam = {
        "dialogue_id": dialogue_id,
        "outcome_action_map": {
            o["outcome_id"]: {
                "outcome_id": o["outcome_id"],
                "description": o.get("description", ""),
                "created_at_turn": o.get("created_at_turn", 0),
                "actions": outcome_actions.get(o["outcome_id"], []),
            }
            for o in all_outcomes
        },
    }
    parent_dir = os.path.dirname(output_dir)
    oam_path = os.path.join(parent_dir, "outcome_action_map.json") if parent_dir else os.path.join(output_dir, "outcome_action_map.json")
    save_json(oam, oam_path)

    step1_input = _build_step1_input(step05_output, step1_output, dialogue_id, output_dir)

    print(f"  Outcomes: {len(all_outcomes)}, Actions: {len(all_actions)}, Requirements: {len(all_requirements)}")
    return step05_output, step1_output, step1_input


def run_step1(
    dialogue: dict, dialogue_id: str,
    client: LoggedLLMClient, model: str,
    output_dir: str, turn_block_size: int = 4,
    action_extraction_client: Optional[LoggedLLMClient] = None,
    action_only: bool = False,
) -> Tuple[dict, dict, dict]:
    """
    Step 1: Run unified extraction across all blocks.

    If action_only=True, only Step 1a (Action Extraction) runs; Step 1b (outcomes) and 1c (requirements) are skipped.

    Returns: (step05_output, step1_output, step1_input)
    """
    step05_file = os.path.join(output_dir, "step05_output.json")
    step1_file = os.path.join(output_dir, "step1_output.json")

    if os.path.exists(step05_file) and os.path.exists(step1_file):
        print("  Step 1: Loading cached outputs...")
        with open(step05_file) as f:
            step05_output = json.load(f)
        with open(step1_file) as f:
            step1_output = json.load(f)
        step1_input = _build_step1_input(step05_output, step1_output, dialogue_id, output_dir)
        return step05_output, step1_output, step1_input

    turns = dialogue.get("turns", [])
    if turn_block_size <= 0:
        turn_block_size = 1
    if len(turns) > 0 and turn_block_size >= len(turns):
        turn_block_size = len(turns)

    # =========================================================================
    # Step 1a: Extract actions per block (lightweight, no outcomes/reqs)
    # =========================================================================
    print("\n" + "="*80)
    print("STEP 1a: Action Extraction (per block)")
    print("="*80)

    all_actions = []
    MAX_BLOCK_RETRIES = 3
    for block_start in range(0, len(turns), turn_block_size):
        block_end = min(block_start + turn_block_size, len(turns))
        print(f"  Block turns {block_start}-{block_end-1}")

        step1a_client = action_extraction_client if action_extraction_client else client
        step1a_model = step1a_client.model if action_extraction_client else model

        for attempt in range(1, MAX_BLOCK_RETRIES + 1):
            try:
                raw_actions = extract_block_actions(turns, block_start, block_end, step1a_client, step1a_model)

                block_actions = []
                for a in raw_actions:
                    try:
                        a = dict(a)
                    except (ValueError, TypeError):
                        print(f"  Warning: skipping malformed action (got {type(a).__name__}): {repr(a)[:80]}")
                        continue
                    turn_id = a.get("turn_id", block_start)
                    a["turn_id"] = turn_id
                    a["role"] = _normalize_role(a.get("role", ""))
                    if 0 <= turn_id < len(turns):
                        a["speaker"] = turns[turn_id].get("speaker", "unknown")
                    else:
                        a["speaker"] = "unknown"
                    block_actions.append(a)

                all_actions.extend(block_actions)
                break
            except Exception as e:
                if attempt < MAX_BLOCK_RETRIES:
                    print(f"  ⚠ Block turns {block_start}-{block_end-1} failed (attempt {attempt}/{MAX_BLOCK_RETRIES}): {e}")
                    print(f"    Retrying...")
                else:
                    print(f"  ✗ Block turns {block_start}-{block_end-1} failed after {MAX_BLOCK_RETRIES} attempts: {e}")
                    raise

    # Assign action_ids globally
    turn_actions_map = defaultdict(list)
    for a in all_actions:
        turn_actions_map[a["turn_id"]].append(a)
    for tid, alist in turn_actions_map.items():
        for idx, a in enumerate(alist, start=1):
            a["action_id"] = f"{tid}-{idx}"

    print(f"  Total actions: {len(all_actions)}")

    dialogue_summary = dialogue.get("dialogue_summary", "")

    if action_only:
        # Step 1a only: save actions only, no outcomes/requirements
        step05_output = {
            "dialogue_id": dialogue_id,
            "dialogue_summary": dialogue_summary,
            "outcomes": [],
            "outcome_versions": [],
            "outcome_actions": {},
            "all_actions": all_actions,
            "metadata": {
                "num_outcomes": 0,
                "num_outcome_versions": 0,
                "num_actions": len(all_actions),
                "model": model,
                "action_only": True,
            },
        }
        save_json(step05_output, os.path.join(output_dir, "step05_output.json"))
        step1_output = {
            "dialogue_id": dialogue_id,
            "requirements": {},
            "threads": {},
            "operations_log": [],
            "others": [],
            "metadata": {"num_requirements": 0, "num_threads": 0, "num_operations": 0, "model": model},
        }
        save_json(step1_output, os.path.join(output_dir, "step1_output.json"))
        parent_dir = os.path.dirname(output_dir)
        oam_path = os.path.join(parent_dir, "outcome_action_map.json") if parent_dir else os.path.join(output_dir, "outcome_action_map.json")
        save_json({"dialogue_id": dialogue_id, "outcome_action_map": {}}, oam_path)
        step1_input = _build_step1_input(step05_output, step1_output, dialogue_id, output_dir)
        print(f"  (Action-only mode: outcomes and requirements skipped)")
        return step05_output, step1_output, step1_input

    return _run_step1b1c_from_actions(
        dialogue, dialogue_id, all_actions, client, model, output_dir,
        extra_step05_metadata=None,
        include_requirements=True,
    )


def _build_step1_input(step05_output, step1_output, dialogue_id, output_dir):
    """Build step1_input dict (for compatibility with V3 downstream). Outcomes are outcome-first: each has 'actions' nested.

    Normalizes outcome_actions: run_outcome_req_only saves {oid: [action_id_str, ...]} but
    compute_similarity_pairs expects {oid: [action_dict, ...]}. Resolve string IDs via all_actions.
    Fallback: when outcome_actions empty, build from outcomes[].actions.
    """
    raw_outcome_actions = step05_output.get("outcome_actions", {})
    all_actions_list = step05_output.get("all_actions", [])
    action_by_id = {a.get("action_id"): a for a in all_actions_list if isinstance(a, dict) and a.get("action_id")}

    # Fallback: when outcome_actions is empty, build from outcomes[].actions (step05 with nested structure)
    if not raw_outcome_actions:
        for o in step05_output.get("outcomes", []):
            oid = o.get("outcome_id")
            if oid:
                raw_outcome_actions[oid] = o.get("actions", [])

    outcome_actions = {}
    for oid, items in raw_outcome_actions.items():
        resolved = []
        for x in (items or []):
            if isinstance(x, dict):
                resolved.append(x)
            elif isinstance(x, str) and x in action_by_id:
                resolved.append(action_by_id[x])
        outcome_actions[oid] = resolved

    # Fallback: some step05 have outcomes[].actions (full dicts) but no top-level outcome_actions
    if not outcome_actions and step05_output.get("outcomes"):
        for o in step05_output.get("outcomes", []):
            oid = o.get("outcome_id")
            if oid:
                acts = o.get("actions", [])
                outcome_actions[oid] = [a for a in acts if isinstance(a, dict)]

    step1_input = {
        "dialogue_id": dialogue_id,
        "dialogue_summary": step05_output.get("dialogue_summary", ""),
        "outcomes": step05_output.get("outcomes", []),
        "outcome_versions": step05_output.get("outcome_versions", []),
        "outcome_actions": outcome_actions,
        "metadata": {
            "num_outcomes": len(step05_output.get("outcomes", [])),
            "num_actions": len(step05_output.get("all_actions", [])),
        },
    }
    # Merge intention mapping if available
    step05b_path = os.path.join(output_dir, "step05b_output.json")
    if os.path.exists(step05b_path):
        try:
            with open(step05b_path) as f:
                step05b = json.load(f)
            step1_input["outcome_to_intention"] = step05b.get("outcome_to_intention", {})
        except Exception:
            pass
    step1_input_file = os.path.join(output_dir, "step1_input.json")
    if not os.path.exists(step1_input_file):
        save_json(step1_input, step1_input_file)
    return step1_input


# =============================================================================
# Step 1b: Intention Extraction
# =============================================================================

def run_step1b(step05_output: dict, output_dir: str, client: LoggedLLMClient, model: str) -> dict:
    """Extract intentions from outcomes (1 LLM call)."""
    step05b_file = os.path.join(output_dir, "step05b_output.json")
    if os.path.exists(step05b_file):
        print("  Step 1b: Loading cached intentions...")
        with open(step05b_file) as f:
            return json.load(f)

    print("\n" + "="*80)
    print("STEP 1b: Intention Extraction")
    print("="*80)

    outcomes = step05_output.get("outcomes", [])
    if not outcomes:
        result = {"dialogue_id": step05_output.get("dialogue_id", ""), "intentions": [], "outcome_to_intention": {}}
        save_json(result, step05b_file)
        return result

    body = json.dumps([
        {"outcome_id": o.get("outcome_id", ""), "description": o.get("description", "")}
        for o in outcomes
    ], indent=2, ensure_ascii=False)

    content = client.call(
        messages=[
            {"role": "system", "content": INTENTION_EXTRACTION_PROMPT},
            {"role": "user", "content": f"Outcomes:\n{body}\n\nList intentions and assign each outcome to one. JSON only."},
        ],
        section="Step 1b - Intention Extraction",
        temperature=1,
    )

    try:
        p = _parse_json_response(content)
        intentions = p.get("intentions", [])
        o2i_list = p.get("outcome_to_intention", [])
        o2i = {x["outcome_id"]: x["intention_id"] for x in o2i_list if x.get("outcome_id") and x.get("intention_id")}
    except Exception as e:
        print(f"  Warning: Intention extraction failed: {e}")
        intentions, o2i = [], {}

    result = {
        "dialogue_id": step05_output.get("dialogue_id", ""),
        "intentions": intentions,
        "outcome_to_intention": o2i,
        "metadata": {"num_intentions": len(intentions), "model": model},
    }
    save_json(result, step05b_file)
    print(f"  Intentions: {len(intentions)}, mappings: {len(o2i)}")
    return result


# =============================================================================
# Step 2a: Embedding-based Pair Selection (same logic as V3 Step 2)
# =============================================================================

def _truncate_text_for_embedding(text: str, max_tokens: int = 8000, model: str = "text-embedding-3-small") -> str:
    """Truncate text to fit within the embedding model's token limit."""
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
    except Exception:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def get_embeddings(texts: list, model: str = "text-embedding-3-small", batch_size: int = 100) -> np.ndarray:
    """Get embeddings using OpenAI API with automatic truncation and batch size adjustment."""
    import openai as _openai
    client = get_openai_client()
    texts = [_truncate_text_for_embedding(t) if (isinstance(t, str) and t.strip()) else " " for t in texts]
    embeddings = []
    i = 0
    cur_batch_size = batch_size
    while i < len(texts):
        batch = texts[i:i + cur_batch_size]
        if not batch:
            break
        try:
            response = client.embeddings.create(model=model, input=batch)
            embeddings.extend([item.embedding for item in response.data])
            i += cur_batch_size
        except _openai.BadRequestError as e:
            err_str = str(e).lower()
            if "maximum input length" in err_str or "too many tokens" in err_str or "max_tokens_per_request" in err_str:
                if cur_batch_size > 1:
                    cur_batch_size = max(1, cur_batch_size // 2)
                    print(f"  Embedding token limit hit, reducing batch_size to {cur_batch_size}")
                else:
                    print(f"  Warning: single text at index {i} exceeds token limit even after truncation, using placeholder")
                    texts[i] = " "
            else:
                raise
    return np.array(embeddings)


def compute_similarity_pairs(
    dialogue: dict, step1_output: dict, step1_input: dict,
    output_dir: str, similarity_threshold: float = 0.5,
) -> list:
    """Compute embedding-based similarity pairs (V3 Step 2 logic, condensed)."""
    requirements_dict = step1_output.get("requirements", {})
    requirements = list(requirements_dict.values())
    outcome_actions = step1_input.get("outcome_actions", {})
    turns = dialogue.get("turns", [])
    outcome_to_intention = step1_input.get("outcome_to_intention", {})

    # Build action lookup (outcome_actions values: list of action dicts; skip non-dicts from run_outcome_req_only format)
    all_actions = []
    for alist in outcome_actions.values():
        for a in alist or []:
            if isinstance(a, dict):
                all_actions.append(a)
            elif isinstance(a, str):
                # action_id string: should be normalized in _build_step1_input; skip if we get here
                pass
    # Defensive: only dicts (guards against any upstream format)
    all_actions = [a for a in all_actions if isinstance(a, dict)]
    action_lookup = {a.get("action_id"): a for a in all_actions if a.get("action_id")}
    turn_to_actions = defaultdict(list)
    for a in all_actions:
        turn_to_actions[a.get("turn_id", 0)].append({
            "action_id": a.get("action_id"),
            "action_text": a.get("action_text", ""),
            "role": a.get("role", "OTHER"),
        })

    # Intention-based turn filter (actions must be dicts; skip non-dicts)
    intention_turn_ids = {}
    for oid, actions in outcome_actions.items():
        iid = outcome_to_intention.get(oid)
        if iid is not None:
            for a in actions or []:
                if isinstance(a, dict):
                    intention_turn_ids.setdefault(iid, set()).add(a.get("turn_id", 0))

    # Collect texts to embed
    utterance_texts, utterance_meta = [], []
    for req in requirements:
        req_id = req.get("req_id")
        origin_action_ids = req.get("origin_action_ids") or req.get("creation_action_ids", [])
        if not origin_action_ids:
            continue
        first_action = action_lookup.get(origin_action_ids[0])
        if not first_action:
            continue
        origin_turn_id = first_action.get("turn_id", req.get("origin_turn_id", 0))

        if 0 <= origin_turn_id < len(turns):
            utterance_texts.append(turns[origin_turn_id].get("text", ""))
            utterance_meta.append((req_id, origin_turn_id, origin_turn_id, True))

            req_iid = outcome_to_intention.get(req.get("outcome_id"))
            same_intent = intention_turn_ids.get(req_iid, set()) if req_iid else None

            for prev_id in range(origin_turn_id):
                if same_intent is not None and prev_id not in same_intent:
                    continue
                utterance_texts.append(turns[prev_id].get("text", ""))
                utterance_meta.append((req_id, origin_turn_id, prev_id, False))

    if not utterance_texts:
        return []

    print(f"  Embedding {len(utterance_texts)} utterances...")
    embeddings = get_embeddings(utterance_texts)

    # Group by requirement
    req_embs = {}
    for idx, (req_id, origin_turn_id, turn_id, is_origin) in enumerate(utterance_meta):
        if req_id not in req_embs:
            req_embs[req_id] = {"origin_emb": None, "prev_embs": []}
        if is_origin:
            req_embs[req_id]["origin_emb"] = (origin_turn_id, embeddings[idx])
        else:
            req_embs[req_id]["prev_embs"].append((turn_id, embeddings[idx]))

    # Build pairs
    pair_map = {}
    for req in requirements:
        req_id = req.get("req_id")
        req_text = req.get("text", "")
        if req_id not in req_embs or req_embs[req_id]["origin_emb"] is None:
            continue
        origin_turn_id, origin_emb = req_embs[req_id]["origin_emb"]
        origin_emb = origin_emb.reshape(1, -1)

        for prev_turn_id, prev_emb in req_embs[req_id]["prev_embs"]:
            sim = cosine_similarity(origin_emb, prev_emb.reshape(1, -1))[0][0]
            if sim >= similarity_threshold:
                prev_turn = turns[prev_turn_id] if 0 <= prev_turn_id < len(turns) else None
                key = (req_id, origin_turn_id, prev_turn_id)
                pair_map[key] = {
                    "req_id": req_id, "req_text": req_text,
                    "origin_turn_id": origin_turn_id,
                    "origin_turn_text": turns[origin_turn_id].get("text", "") if 0 <= origin_turn_id < len(turns) else "",
                    "origin_turn_speaker": turns[origin_turn_id].get("speaker", "") if 0 <= origin_turn_id < len(turns) else "",
                    "origin_turn_actions": turn_to_actions.get(origin_turn_id, []),
                    "prev_turn_id": prev_turn_id,
                    "prev_turn_text": prev_turn.get("text", "") if prev_turn else "",
                    "prev_turn_speaker": prev_turn.get("speaker", "") if prev_turn else "",
                    "prev_turn_actions": turn_to_actions.get(prev_turn_id, []),
                    "similarity": float(sim),
                    "sources": ["utterance"],
                }

        # Always include adjacent previous turn
        prev_id = origin_turn_id - 1
        if prev_id >= 0:
            key = (req_id, origin_turn_id, prev_id)
            if key not in pair_map:
                sim_val = similarity_threshold
                pair_map[key] = {
                    "req_id": req_id, "req_text": req_text,
                    "origin_turn_id": origin_turn_id,
                    "origin_turn_text": turns[origin_turn_id].get("text", "") if 0 <= origin_turn_id < len(turns) else "",
                    "origin_turn_speaker": turns[origin_turn_id].get("speaker", "") if 0 <= origin_turn_id < len(turns) else "",
                    "origin_turn_actions": turn_to_actions.get(origin_turn_id, []),
                    "prev_turn_id": prev_id,
                    "prev_turn_text": turns[prev_id].get("text", "") if 0 <= prev_id < len(turns) else "",
                    "prev_turn_speaker": turns[prev_id].get("speaker", "") if 0 <= prev_id < len(turns) else "",
                    "prev_turn_actions": turn_to_actions.get(prev_id, []),
                    "similarity": sim_val,
                    "sources": ["adjacent_prev"],
                }

        # Contributing action turns
        for aid in req.get("contributing_action_ids", []):
            ca = action_lookup.get(aid)
            if not ca:
                continue
            ctid = ca.get("turn_id", 0)
            if ctid == origin_turn_id:
                continue
            key = (req_id, origin_turn_id, ctid)
            if key not in pair_map:
                ct = turns[ctid] if 0 <= ctid < len(turns) else None
                pair_map[key] = {
                    "req_id": req_id, "req_text": req_text,
                    "origin_turn_id": origin_turn_id,
                    "origin_turn_text": turns[origin_turn_id].get("text", "") if 0 <= origin_turn_id < len(turns) else "",
                    "origin_turn_speaker": turns[origin_turn_id].get("speaker", "") if 0 <= origin_turn_id < len(turns) else "",
                    "origin_turn_actions": turn_to_actions.get(origin_turn_id, []),
                    "prev_turn_id": ctid,
                    "prev_turn_text": ct.get("text", "") if ct else "",
                    "prev_turn_speaker": ct.get("speaker", "") if ct else "",
                    "prev_turn_actions": turn_to_actions.get(ctid, []),
                    "similarity": 0.0,
                    "sources": ["contributing"],
                }

    # related_to pairs (default DIRECT_CONNECTION)
    for req in requirements_dict.values():
        req_id = req.get("req_id")
        related_raw = req.get("related_to") or []
        if isinstance(related_raw, str):
            related_raw = [related_raw]
        if not related_raw:
            continue
        origin_turn_id = req.get("origin_turn_id", req.get("created_at", 0))
        for ref in related_raw:
            ref = (ref or "").strip()
            related_req = requirements_dict.get(ref)
            if not related_req:
                continue
            ptid = related_req.get("origin_turn_id", -1)
            if 0 <= ptid < len(turns) and ptid != origin_turn_id:
                key = (req_id, origin_turn_id, ptid)
                if key not in pair_map:
                    pt = turns[ptid]
                    pair_map[key] = {
                        "req_id": req_id, "req_text": req.get("text", ""),
                        "origin_turn_id": origin_turn_id,
                        "origin_turn_text": turns[origin_turn_id].get("text", "") if 0 <= origin_turn_id < len(turns) else "",
                        "origin_turn_speaker": turns[origin_turn_id].get("speaker", "") if 0 <= origin_turn_id < len(turns) else "",
                        "origin_turn_actions": turn_to_actions.get(origin_turn_id, []),
                        "prev_turn_id": ptid,
                        "prev_turn_text": pt.get("text", ""),
                        "prev_turn_speaker": pt.get("speaker", ""),
                        "prev_turn_actions": turn_to_actions.get(ptid, []),
                        "similarity": 1.0,
                        "sources": ["related_to"],
                    }

    pairs = sorted(pair_map.values(), key=lambda x: x["similarity"], reverse=True)
    print(f"  Pairs: {len(pairs)}")
    return pairs


def run_step2a(
    dialogue: dict, step1_output: dict, step1_input: dict,
    dialogue_id: str, output_dir: str,
) -> Tuple[dict, list]:
    """Step 2a: embedding-based pair selection."""
    step2_file = os.path.join(output_dir, "step2_output.json")
    if os.path.exists(step2_file):
        with open(step2_file) as f:
            step2_output = json.load(f)
        pairs = step2_output.get("pairs", [])
        # Don't use cache when pairs is empty (likely from prior buggy run); force recompute
        if pairs:
            print("  Step 2a: Loading cached pairs...")
            return step2_output, pairs

    print("\n" + "="*80)
    print("STEP 2a: Embedding-based Pair Selection")
    print("="*80)

    pairs = compute_similarity_pairs(dialogue, step1_output, step1_input, output_dir)

    step2_output = {
        "dialogue_id": dialogue_id,
        "pairs": pairs,
        "metadata": {"num_pairs": len(pairs), "model": "text-embedding-3-small"},
    }
    save_json(step2_output, step2_file)
    return step2_output, pairs


# =============================================================================
# Step 2b: Batch Relationship Labeling (1 LLM call per outcome)
# =============================================================================

def _format_actions_block(actions: list) -> str:
    if not actions:
        return "  (no actions)"
    lines = []
    for a in actions:
        text = (a.get("action_text", "") or "")[:200]
        lines.append(f"  - action_id: {a.get('action_id','')}, action_text: {text}")
    return "\n".join(lines)


def _format_preceding_block(pairs: list) -> str:
    """Format backward candidate pairs as preceding actions block."""
    if not pairs:
        return "(none)"
    lines = []
    for i, p in enumerate(pairs):
        prev_acts = p.get("prev_turn_actions", [])
        lines.append(f"--- Entry {i} ---")
        lines.append(f"Turn {p.get('prev_turn_id', 0)} ({p.get('prev_turn_speaker', '')}):")
        lines.append(f"  Text: {p.get('prev_turn_text', '')[:500]}")
        for act in prev_acts:
            aid = act.get("action_id", "")
            atext = (act.get("action_text", "") or "")[:200]
            arole = act.get("role", "OTHER")
            lines.append(f"  Action: [{aid}] {atext} (role: {arole})")
        if not prev_acts:
            lines.append(f"  Action: [{p.get('prev_turn_id', 0)}-0] (no action extracted) (role: OTHER)")
        lines.append("")
    return "\n".join(lines)


def _format_subsequent_block(actions: list) -> str:
    """Format forward candidate actions as subsequent block."""
    if not actions:
        return "(none)"
    lines = []
    for i, a in enumerate(actions):
        aid = a.get("action_id", "")
        atype = a.get("action_type", "")
        atext = (a.get("action_text", "") or "")[:200]
        speaker = a.get("speaker", "unknown")
        turn = a.get("turn_id", 0)
        role = a.get("role", "OTHER")
        lines.append(f"--- Entry {i} ---")
        lines.append(f"[{aid}] Turn {turn} ({speaker}): {atype} — {atext} (role: {role})")
        lines.append("")
    return "\n".join(lines)


def label_actions_for_requirement(
    preceding_pairs: list, subsequent_actions: list,
    req_id: str, req_text: str, req_origin_turn: int,
    outcome_desc: str, client: LoggedLLMClient, model: str,
) -> Tuple[list, list]:
    """Label both preceding (backward) and subsequent (forward) actions for one requirement."""
    preceding_block = _format_preceding_block(preceding_pairs)
    subsequent_block = _format_subsequent_block(subsequent_actions)

    prompt = REQUIREMENT_ACTION_LABELING_PROMPT.format(
        outcome_description=outcome_desc,
        req_id=req_id,
        req_text=req_text,
        req_origin_turn=req_origin_turn,
        preceding_block=preceding_block,
        subsequent_block=subsequent_block,
    )

    n_prec = len(preceding_pairs)
    n_subs = len(subsequent_actions)

    content = client.call(
        messages=[
            {"role": "system", "content": "You are an expert at analyzing dialogue action-requirement relationships."},
            {"role": "user", "content": prompt},
        ],
        section=f"Step 2b - Action Labeling ({req_id}, {n_prec} preceding + {n_subs} subsequent)",
        temperature=1,
    )

    try:
        result = _parse_json_response(content)
        prec_labels = result.get("preceding_labels", [])
        subs_labels = result.get("subsequent_labels", [])
    except Exception as e:
        print(f"  Warning: Labeling parse failed for {req_id}: {e}")
        prec_labels = []
        subs_labels = []

    # --- Process preceding labels → enrich pairs ---
    prec_by_idx = {l.get("index"): l for l in prec_labels}
    labeled_pairs = []
    for i, p in enumerate(preceding_pairs):
        lp = p.copy()
        lbl = prec_by_idx.get(i, {})
        rel_type = lbl.get("relationship_type", "NO_CONNECTION")
        rel_score = lbl.get("relationship_score")
        explanation = lbl.get("explanation", "")
        explanation_type = lbl.get("explanation_type", "")
        contrib_role = _normalize_role(lbl.get("contribution_role", "OTHER"))
        action_id = _norm_action_id(lbl.get("action_id", ""))

        lp["relationship_type"] = rel_type
        lp["relationship_score"] = rel_score
        lp["explanation"] = explanation
        lp["explanation_type"] = explanation_type
        lp["action_relationships"] = [{
            "action_id": action_id,
            "relationship_type": rel_type,
            "relationship_score": rel_score,
            "explanation": explanation,
            "explanation_type": explanation_type,
            "contribution_role": contrib_role,
        }]
        labeled_pairs.append(lp)

    # --- Process subsequent labels → forward results ---
    subs_by_idx = {l.get("index"): l for l in subs_labels}
    forward_results = []
    for i, a in enumerate(subsequent_actions):
        lbl = subs_by_idx.get(i, {})
        rel = lbl.get("relationship_type", "NO_CONNECTION")
        forward_results.append({
            "action_id": _norm_action_id(lbl.get("action_id", a.get("action_id", ""))),
            "relationship": rel,
            "relationship_score": lbl.get("relationship_score"),
            "explanation": lbl.get("explanation", ""),
            "explanation_type": lbl.get("explanation_type", ""),
            "contribution_role": _normalize_role(lbl.get("contribution_role", "OTHER")),
        })

    return labeled_pairs, forward_results


def _one_req_block(
    req_id: str, req_text: str, req_origin_turn: int, outcome_desc: str,
    preceding_block: str, subsequent_block: str, prec_count: int, subs_count: int,
) -> str:
    """One requirement's block for the batched prompt."""
    return f"""
=== OUTCOME (context) ===
{outcome_desc}
(Do not extract new requirements from this; context for interpretation only.)

=== TARGET REQUIREMENT {req_id} ===
Text (turn {req_origin_turn}): {req_text}

SECTION A: PRECEDING ACTIONS ({prec_count} entries)
{preceding_block}

SECTION B: SUBSEQUENT ACTIONS ({subs_count} entries)
{subsequent_block}
"""


def label_actions_for_requirement_batch(
    batch_items: list, client: LoggedLLMClient, model: str,
) -> Tuple[list, dict]:
    """
    batch_items: list of (rid, req_text, origin_turn, outcome_desc, llm_pairs, subsequent_actions).
    Returns (all_labeled_pairs, forward_labels_by_rid).
    """
    blocks = []
    for (rid, req_text, origin_turn, outcome_desc, llm_pairs, subsequent_actions) in batch_items:
        preceding_block = _format_preceding_block(llm_pairs)
        subsequent_block = _format_subsequent_block(subsequent_actions)
        blocks.append(_one_req_block(
            rid, req_text, origin_turn, outcome_desc,
            preceding_block, subsequent_block, len(llm_pairs), len(subsequent_actions),
        ))
    requirements_blocks = "\n".join(blocks)
    req_ids = [item[0] for item in batch_items]
    n_prec = sum(len(item[4]) for item in batch_items)
    n_subs = sum(len(item[5]) for item in batch_items)

    prompt = REQUIREMENT_ACTION_LABELING_BATCH_PROMPT.format(
        requirements_blocks=requirements_blocks,
    )
    content = client.call(
        messages=[
            {"role": "system", "content": "You are an expert at analyzing dialogue action-requirement relationships."},
            {"role": "user", "content": prompt},
        ],
        section=f"Step 2b - Action Labeling BATCH ({len(batch_items)} reqs, {n_prec} preceding + {n_subs} subsequent)",
        temperature=1,
    )

    try:
        result = _parse_json_response(content)
        results = result.get("results", [])
    except Exception as e:
        print(f"  Warning: Batch labeling parse failed: {e}")
        results = []

    all_labeled = []
    forward_labels = {}
    for idx, (rid, req_text, origin_turn, outcome_desc, llm_pairs, subsequent_actions) in enumerate(batch_items):
        res = results[idx] if idx < len(results) else {}
        if res.get("req_id") != rid:
            res = next((r for r in results if r.get("req_id") == rid), {})
        prec_labels = res.get("preceding_labels", [])
        subs_labels = res.get("subsequent_labels", [])

        prec_by_idx = {l.get("index"): l for l in prec_labels}
        labeled_pairs = []
        for i, p in enumerate(llm_pairs):
            lp = p.copy()
            lbl = prec_by_idx.get(i, {})
            rel_type = lbl.get("relationship_type", "NO_CONNECTION")
            lp["relationship_type"] = rel_type
            lp["relationship_score"] = lbl.get("relationship_score")
            lp["explanation"] = lbl.get("explanation", "")
            lp["explanation_type"] = lbl.get("explanation_type", "")
            contrib_role = _normalize_role(lbl.get("contribution_role", "OTHER"))
            action_id = _norm_action_id(lbl.get("action_id", ""))
            lp["action_relationships"] = [{
                "action_id": action_id,
                "relationship_type": rel_type,
                "relationship_score": lbl.get("relationship_score"),
                "explanation": lbl.get("explanation", ""),
                "explanation_type": lbl.get("explanation_type", ""),
                "contribution_role": contrib_role,
            }]
            labeled_pairs.append(lp)
        all_labeled.extend(labeled_pairs)

        subs_by_idx = {l.get("index"): l for l in subs_labels}
        forward_results = []
        impl_ids = []
        contrib_ids = []
        revise_ids = []
        for i, a in enumerate(subsequent_actions):
            lbl = subs_by_idx.get(i, {})
            rel = lbl.get("relationship_type", "NO_CONNECTION")
            aid = _norm_action_id(lbl.get("action_id", a.get("action_id", "")))
            forward_results.append({
                "action_id": aid,
                "relationship": rel,
                "relationship_score": lbl.get("relationship_score"),
                "explanation": lbl.get("explanation", ""),
                "explanation_type": lbl.get("explanation_type", ""),
                "contribution_role": _normalize_role(lbl.get("contribution_role", "OTHER")),
            })
            if rel not in ("DIRECT_CONNECTION", "IMPLICIT_CONNECTION"):
                if rel == "IMPLEMENTS":
                    impl_ids.append(aid)
                elif rel == "CONTRIBUTES":
                    contrib_ids.append(aid)
                elif rel == "REVISES":
                    revise_ids.append(aid)
        forward_labels[rid] = {
            "implementation_action_ids": impl_ids,
            "contributing_action_ids": contrib_ids,
            "revise_action_ids": revise_ids,
            "all_labels": forward_results,
        }

    return all_labeled, forward_labels


def run_step2b(
    similarity_pairs: list, step1_output: dict, step1_input: dict,
    step05_output: dict,
    dialogue_id: str, client: LoggedLLMClient, model: str, output_dir: str,
    step2b_req_batch_size: int = 1,
) -> Tuple[dict, list]:
    """Step 2b: unified action-requirement labeling. step2b_req_batch_size=1 → one call per requirement; N → N requirements per call."""
    step3_file = os.path.join(output_dir, "step3_output.json")
    step2c_file = os.path.join(output_dir, "step2c_output.json")
    if os.path.exists(step3_file) and os.path.exists(step2c_file):
        print("  Step 2b: Loading cached labeled pairs + forward labels...")
        with open(step3_file) as f:
            step3_output = json.load(f)
        with open(step2c_file) as f:
            step2c_output = json.load(f)
        # Re-apply forward enrichment to step1_output
        for req_id, fwd in step2c_output.get("forward_labels", {}).items():
            req = step1_output.get("requirements", {}).get(req_id)
            if req:
                existing_impl = set(req.get("implementation_action_ids", []))
                existing_contrib = set(req.get("contributing_action_ids", []))
                req["implementation_action_ids"] = list(existing_impl | set(fwd.get("implementation_action_ids", [])))
                req["contributing_action_ids"] = list(existing_contrib | set(fwd.get("contributing_action_ids", [])))
        return step3_output, step3_output.get("pairs", [])

    print("\n" + "="*80)
    print("STEP 2b: Unified Action-Requirement Labeling (per requirement)")
    print("="*80)

    requirements_dict = step1_output.get("requirements", {})
    all_actions = step05_output.get("all_actions", [])
    outcomes = step1_input.get("outcomes", [])
    outcomes_by_id = {o.get("outcome_id"): o for o in outcomes}

    # Build action-by-outcome index
    action_by_outcome = defaultdict(list)
    for a in all_actions:
        oid = a.get("bound_outcome_id", "")
        if oid:
            action_by_outcome[oid].append(a)

    # Group similarity pairs by requirement
    pairs_by_req = defaultdict(list)
    for p in similarity_pairs:
        rid = p.get("req_id", "unknown")
        pairs_by_req[rid].append(p)

    all_labeled = []
    forward_labels = {}
    enriched_counts = {"implements": 0, "contributes": 0, "revises": 0}
    step2b_req_batch_size = max(1, int(step2b_req_batch_size))

    # Iterate over ALL requirements: handle related_to (no LLM), collect work items for LLM
    all_req_ids = sorted(set(requirements_dict.keys()) | set(pairs_by_req.keys()))
    work_items = []  # list of (rid, req_text, origin_turn, outcome_desc, llm_pairs, subsequent)

    for rid in all_req_ids:
        req = requirements_dict.get(rid, {})
        if not req:
            continue
        req_text = req.get("text", "")
        oid = req.get("outcome_id", "unknown")
        origin_turn = req.get("origin_turn_id", req.get("created_at", 0))
        outcome = outcomes_by_id.get(oid, {})
        outcome_desc = outcome.get("description", oid)

        req_pairs = pairs_by_req.get(rid, [])
        llm_pairs = []
        for p in req_pairs:
            if "related_to" in (p.get("sources") or []):
                lp = p.copy()
                origin_acts = p.get("origin_turn_actions", [])
                prev_acts = p.get("prev_turn_actions", [])
                ars = []
                for a in origin_acts:
                    ars.append({"action_id": a.get("action_id", ""), "relationship_type": "NO_CONNECTION",
                                "relationship_score": None, "explanation": "Origin turn.", "contribution_role": "OTHER"})
                for a in prev_acts:
                    ars.append({"action_id": a.get("action_id", ""), "relationship_type": "DIRECT_CONNECTION",
                                "relationship_score": 5, "explanation": "Related requirement link.", "contribution_role": "SHAPER"})
                lp["action_relationships"] = ars
                lp["relationship_type"] = "DIRECT_CONNECTION"
                lp["relationship_score"] = 5
                lp["explanation"] = "Related requirement link (default)."
                all_labeled.append(lp)
            else:
                llm_pairs.append(p)

        subsequent = []
        if req.get("status") == "active":
            subsequent = [
                a for a in action_by_outcome.get(oid, [])
                if a.get("turn_id", 0) > origin_turn
            ]
        if not llm_pairs and not subsequent:
            continue
        work_items.append((rid, req_text, origin_turn, outcome_desc, llm_pairs, subsequent))

    # Chunk work items and run LLM (single or batched)
    for chunk_start in tqdm(range(0, len(work_items), step2b_req_batch_size), desc="Step 2b", unit="batch"):
        chunk = work_items[chunk_start : chunk_start + step2b_req_batch_size]
        if not chunk:
            continue

        if len(chunk) == 1 and step2b_req_batch_size == 1:
            (rid, req_text, origin_turn, outcome_desc, llm_pairs, subsequent) = chunk[0]
            req = requirements_dict.get(rid, {})
            tqdm.write(f"  {rid}: {len(llm_pairs)} preceding + {len(subsequent)} subsequent")
            labeled_pairs, fwd_results = label_actions_for_requirement(
                llm_pairs, subsequent, rid, req_text, origin_turn, outcome_desc, client, model,
            )
            all_labeled.extend(labeled_pairs)
            impl_ids = []
            contrib_ids = []
            revise_ids = []
            for lbl in fwd_results:
                rel = lbl.get("relationship", "NO_CONNECTION")
                aid = _norm_action_id(lbl.get("action_id", ""))
                if rel in ("DIRECT_CONNECTION", "IMPLICIT_CONNECTION"):
                    continue
                if rel == "IMPLEMENTS":
                    impl_ids.append(aid)
                    enriched_counts["implements"] += 1
                elif rel == "CONTRIBUTES":
                    contrib_ids.append(aid)
                    enriched_counts["contributes"] += 1
                elif rel == "REVISES":
                    revise_ids.append(aid)
                    enriched_counts["revises"] += 1
            forward_labels[rid] = {
                "implementation_action_ids": impl_ids,
                "contributing_action_ids": contrib_ids,
                "revise_action_ids": revise_ids,
                "all_labels": fwd_results,
            }
            existing_impl = set(req.get("implementation_action_ids", []))
            existing_contrib = set(req.get("contributing_action_ids", []))
            existing_revise = set(req.get("revise_action_ids", []))
            req["implementation_action_ids"] = list(existing_impl | set(impl_ids))
            req["contributing_action_ids"] = list(existing_contrib | set(contrib_ids))
            req["revise_action_ids"] = list(existing_revise | set(revise_ids))
        else:
            batch_labeled, batch_forward = label_actions_for_requirement_batch(chunk, client, model)
            all_labeled.extend(batch_labeled)
            for (rid, _rtext, _oturn, _odesc, _lp, _sub) in chunk:
                req = requirements_dict.get(rid, {})
                fl = batch_forward.get(rid, {})
                impl_ids = fl.get("implementation_action_ids", [])
                contrib_ids = fl.get("contributing_action_ids", [])
                revise_ids = fl.get("revise_action_ids", [])
                enriched_counts["implements"] += len(impl_ids)
                enriched_counts["contributes"] += len(contrib_ids)
                enriched_counts["revises"] += len(revise_ids)
                forward_labels[rid] = fl
                existing_impl = set(req.get("implementation_action_ids", []))
                existing_contrib = set(req.get("contributing_action_ids", []))
                existing_revise = set(req.get("revise_action_ids", []))
                req["implementation_action_ids"] = list(existing_impl | set(impl_ids))
                req["contributing_action_ids"] = list(existing_contrib | set(contrib_ids))
                req["revise_action_ids"] = list(existing_revise | set(revise_ids))

    # --- Save outputs ---
    step3_output = {
        "dialogue_id": dialogue_id,
        "pairs": all_labeled,
        "metadata": {"num_pairs": len(all_labeled), "model": model},
    }
    save_json(step3_output, step3_file)

    step2c_output = {
        "dialogue_id": dialogue_id,
        "forward_labels": forward_labels,
        "enriched_counts": enriched_counts,
        "metadata": {"model": model},
    }
    save_json(step2c_output, step2c_file)

    # Re-save enriched step1_output
    step1_file = os.path.join(output_dir, "step1_output.json")
    save_json(step1_output, step1_file)

    print(f"  Total labeled pairs: {len(all_labeled)}")
    print(f"  Forward: +{enriched_counts['implements']} implements, +{enriched_counts['contributes']} contributes, +{enriched_counts['revises']} revises")
    return step3_output, all_labeled


# =============================================================================
# Step 3: Contribution Analysis (no LLM, same math as V3 Step 4)
# =============================================================================

def run_step3_contributions(
    dialogue: dict, step1_output: dict, step3_output: dict,
    step05_output: dict, output_dir: str,
    alpha_direct: Optional[float] = None,
    alpha_indirect: Optional[float] = None,
    origin_shaper_score: float = 5.0,
) -> None:
    """Compute contributions: legacy scaling for non-SHAPER; SHAPER uses chain-distance decay."""
    ad = float(CONTRIB_ALPHA_DIRECT if alpha_direct is None else alpha_direct)
    ai = float(CONTRIB_ALPHA_INDIRECT if alpha_indirect is None else alpha_indirect)
    print("\n" + "="*80)
    print("STEP 3: Contribution Analysis")
    print("="*80)
    print(f"  SHAPER chain decay: alpha_direct={ad}, alpha_indirect={ai}, origin_SHAPER_score={origin_shaper_score}")

    requirements_dict = step1_output.get("requirements", {})
    threads_dict = step1_output.get("threads", {})
    operations_log = step1_output.get("operations_log", [])
    turns = dialogue.get("turns", [])
    pairs = step3_output.get("pairs", [])

    # Build lookups
    all_actions = step05_output.get("all_actions", [])
    action_lookup = {a.get("action_id"): a for a in all_actions}
    turn_to_actions = defaultdict(list)
    for a in all_actions:
        turn_to_actions[a.get("turn_id", 0)].append(a)

    requirements = list(requirements_dict.values())
    req_by_id = {r["req_id"]: r for r in requirements}
    turn_by_id = {t["turn_id"]: t for t in turns}
    turn_to_speaker = {t["turn_id"]: t.get("speaker", "unknown") for t in turns}

    # Map req to pairs
    req_to_pairs = defaultdict(list)
    for p in pairs:
        rid = p.get("req_id")
        if rid and rid in req_by_id:
            req_to_pairs[rid].append(p)

    # Map req to create op
    req_to_create_op = {}
    for entry in operations_log:
        for op in entry.get("ops", []):
            if op.get("op") == "create" and "req_id" in op:
                req_to_create_op.setdefault(op["req_id"], op)

    def influence_from_rel(rel_type, rel_score):
        s = (rel_score or 0) if rel_score is not None else 0
        norm = s / 5.0 if s > 0 else 0.0
        if rel_type == "DIRECT_CONNECTION":
            return (norm, 0.0)
        if rel_type == "IMPLICIT_CONNECTION":
            return (0.0, norm)
        return (0.0, 0.0)

    def shaper_chain_influence(rel_type, rel_score, rel_distance: int) -> Tuple[float, float]:
        s = float(rel_score) if rel_score is not None else 0.0
        if rel_type == "DIRECT_CONNECTION":
            return (s * (ad ** rel_distance), 0.0)
        if rel_type == "IMPLICIT_CONNECTION":
            return (0.0, s * (ai ** rel_distance))
        return (0.0, 0.0)

    req_contributions = {}

    for req_id, req in req_by_id.items():
        origin_turn_id = req.get("origin_turn_id", req.get("created_at", 0))
        origin_speaker = turn_to_speaker.get(origin_turn_id, "unknown")

        speaker_contribs = {}
        role_contribs = {}
        origin_action_ids_set = set(req.get("origin_action_ids", []))
        contributing_action_ids_set = set(req.get("contributing_action_ids", []))
        implementation_action_ids_set = set(req.get("implementation_action_ids", []))

        # Origin contribution (req-creating turn; SHAPER actions get score * alpha_direct^0)
        if origin_turn_id in turn_by_id:
            create_op = req_to_create_op.get(req_id, {})
            speaker_contribs.setdefault(origin_speaker, {
                "direct_influence": 0.0, "indirect_influence": 0.0,
                "total_influence": 0.0, "influential_utterances": [],
            })
            origin_dir_sum = 0.0
            origin_ind_sum = 0.0
            creation_ids = {_norm_action_id(x) for x in (req.get("creation_action_ids") or []) if x}
            for aid in req.get("origin_action_ids", []):
                action = action_lookup.get(aid)
                if not action:
                    continue
                agent = action.get("speaker", origin_speaker)
                role = _normalize_role(action.get("role", "OTHER"))
                an = _norm_action_id(aid)
                # Requirement-creating action (explicit list) or SHAPER: full direct score * alpha^0
                if an in creation_ids or role == "SHAPER":
                    d_add = float(origin_shaper_score) * (ad ** 0)
                    ind_add = 0.0
                else:
                    d_add, ind_add = 1.0, 0.0
                origin_dir_sum += d_add
                origin_ind_sum += ind_add
                speaker_contribs[origin_speaker]["direct_influence"] += d_add
                speaker_contribs[origin_speaker]["indirect_influence"] += ind_add
                speaker_contribs[origin_speaker]["total_influence"] += d_add + ind_add
                role_contribs.setdefault(agent, {}).setdefault(role, {"M_dir": 0.0, "M_ind": 0.0, "M_total": 0.0, "count": 0})
                role_contribs[agent][role]["M_dir"] += d_add
                role_contribs[agent][role]["M_ind"] += ind_add
                role_contribs[agent][role]["M_total"] += d_add + ind_add
                role_contribs[agent][role]["count"] += 1

            if origin_dir_sum == 0.0 and origin_ind_sum == 0.0:
                speaker_contribs[origin_speaker]["direct_influence"] += 1.0
                speaker_contribs[origin_speaker]["total_influence"] += 1.0
                origin_dir_sum = 1.0

            speaker_contribs[origin_speaker]["influential_utterances"].append({
                "turn_id": origin_turn_id, "turn_text": turn_by_id[origin_turn_id].get("text", ""),
                "relationship_type": "DIRECT_CONNECTION", "relationship_score": 5,
                "direct_influence": origin_dir_sum, "indirect_influence": origin_ind_sum,
                "explanation": create_op.get("rationale", "Origin turn."), "is_origin": True,
            })

        # Pairs: influential turns strictly before origin; relative distance along sorted chain (1 = closest to req).
        prev_to_pairs: Dict[int, list] = defaultdict(list)
        for pair in req_to_pairs.get(req_id, []):
            ptid = pair.get("prev_turn_id")
            if ptid is None or ptid not in turn_by_id:
                continue
            if ptid >= origin_turn_id:
                continue
            prev_to_pairs[ptid].append(pair)

        chain_turns = sorted(prev_to_pairs.keys())
        n_chain = len(chain_turns)

        for idx, prev_turn_id in enumerate(chain_turns):
            rel_distance = n_chain - idx
            pairs_here = prev_to_pairs[prev_turn_id]
            action_rel_lookup = {}
            for p in pairs_here:
                for ar in p.get("action_relationships", []) or []:
                    aid = _norm_action_id(ar.get("action_id") or "")
                    if aid:
                        action_rel_lookup[aid] = ar
            pair_rt = pairs_here[-1].get("relationship_type", "NO_CONNECTION")
            pair_rs = pairs_here[-1].get("relationship_score")

            speaker = turn_to_speaker.get(prev_turn_id, "unknown")
            I_dir_total, I_ind_total = 0.0, 0.0

            if prev_turn_id in turn_to_actions:
                for action in turn_to_actions[prev_turn_id]:
                    aid = _norm_action_id(action.get("action_id") or "")
                    ar = action_rel_lookup.get(aid)
                    if ar:
                        rt = ar.get("relationship_type", "NO_CONNECTION")
                        rs = ar.get("relationship_score")
                        contrib_role = _normalize_role(ar.get("contribution_role") or action.get("role", "OTHER"))
                    else:
                        rt = pair_rt
                        rs = pair_rs
                        contrib_role = _normalize_role(action.get("role", "OTHER"))

                    if contrib_role == "SHAPER":
                        d, ind = shaper_chain_influence(rt, rs, rel_distance)
                    else:
                        if ar:
                            d, ind = influence_from_rel(rt, rs)
                        else:
                            d, ind = influence_from_rel(pair_rt, pair_rs)

                    I_dir_total += d
                    I_ind_total += ind

                    if aid in origin_action_ids_set:
                        continue
                    full_action = action_lookup.get(aid, {})
                    agent = full_action.get("speaker", speaker) if full_action else speaker
                    if aid in contributing_action_ids_set:
                        role = _normalize_role(action.get("role"))
                    else:
                        role = _normalize_role((ar or {}).get("contribution_role") or action.get("role", "OTHER"))
                    role_contribs.setdefault(agent, {}).setdefault(role, {"M_dir": 0.0, "M_ind": 0.0, "M_total": 0.0, "count": 0})
                    role_contribs[agent][role]["M_dir"] += d
                    role_contribs[agent][role]["M_ind"] += ind
                    role_contribs[agent][role]["M_total"] += d + ind
                    role_contribs[agent][role]["count"] += 1
            else:
                I_dir_total, I_ind_total = influence_from_rel(pair_rt, pair_rs or 0)

            if I_dir_total > 0 or I_ind_total > 0:
                speaker_contribs.setdefault(speaker, {
                    "direct_influence": 0.0, "indirect_influence": 0.0,
                    "total_influence": 0.0, "influential_utterances": [],
                })
                speaker_contribs[speaker]["direct_influence"] += I_dir_total
                speaker_contribs[speaker]["indirect_influence"] += I_ind_total
                speaker_contribs[speaker]["total_influence"] += I_dir_total + I_ind_total
                speaker_contribs[speaker]["influential_utterances"].append({
                    "turn_id": prev_turn_id, "turn_text": turn_by_id[prev_turn_id].get("text", ""),
                    "relationship_type": pair_rt, "relationship_score": pair_rs,
                    "direct_influence": I_dir_total, "indirect_influence": I_ind_total,
                    "explanation": pairs_here[-1].get("explanation", ""), "is_origin": False,
                })

        # Implementation actions
        for aid in implementation_action_ids_set:
            if aid in origin_action_ids_set:
                continue
            full_action = action_lookup.get(aid)
            if not full_action:
                continue
            agent = full_action.get("speaker", "unknown")
            role = _normalize_role(full_action.get("role"))
            turn_id = full_action.get("turn_id", 0)
            speaker_contribs.setdefault(agent, {
                "direct_influence": 0.0, "indirect_influence": 0.0,
                "total_influence": 0.0, "influential_utterances": [],
            })
            speaker_contribs[agent]["direct_influence"] += 1.0
            speaker_contribs[agent]["total_influence"] += 1.0
            role_contribs.setdefault(agent, {}).setdefault(role, {"M_dir": 0.0, "M_ind": 0.0, "M_total": 0.0, "count": 0})
            role_contribs[agent][role]["M_dir"] += 1.0
            role_contribs[agent][role]["M_total"] += 1.0
            role_contribs[agent][role]["count"] += 1

        req_contributions[req_id] = {
            "req_id": req_id, "req_text": req.get("text", ""),
            "thread_id": req.get("thread_id"),
            "thread_outcome": threads_dict.get(str(req.get("thread_id", "")), {}).get("outcome", ""),
            "speaker_contributions": speaker_contribs,
            "role_contributions": role_contribs,
        }

    # Outcome-level contributions
    outcome_contributions = {}
    for tid, thread in threads_dict.items():
        oid = thread.get("outcome_id", "")
        thread_req_ids = thread.get("requirements", [])
        oc = {"thread_id": int(tid), "outcome_id": oid, "outcome": thread.get("outcome", ""),
              "speaker_contributions": {}, "role_contributions": {}, "requirements": thread_req_ids}
        for rid in thread_req_ids:
            rc = req_contributions.get(rid, {})
            for speaker, sd in rc.get("speaker_contributions", {}).items():
                oc["speaker_contributions"].setdefault(speaker, {
                    "direct_influence": 0.0, "indirect_influence": 0.0,
                    "total_influence": 0.0, "requirement_count": 0,
                })
                oc["speaker_contributions"][speaker]["direct_influence"] += sd["direct_influence"]
                oc["speaker_contributions"][speaker]["indirect_influence"] += sd["indirect_influence"]
                oc["speaker_contributions"][speaker]["total_influence"] += sd["total_influence"]
                oc["speaker_contributions"][speaker]["requirement_count"] += 1
            for agent, roles in rc.get("role_contributions", {}).items():
                oc["role_contributions"].setdefault(agent, {})
                for role, data in roles.items():
                    oc["role_contributions"][agent].setdefault(role, {"M_dir": 0.0, "M_ind": 0.0, "M_total": 0.0, "count": 0})
                    oc["role_contributions"][agent][role]["M_dir"] += data["M_dir"]
                    oc["role_contributions"][agent][role]["M_ind"] += data["M_ind"]
                    oc["role_contributions"][agent][role]["M_total"] += data["M_total"]
                    oc["role_contributions"][agent][role]["count"] += data["count"]
        outcome_contributions[tid] = oc

    contribution_dir = os.path.join(output_dir, "contribution_analysis")
    os.makedirs(contribution_dir, exist_ok=True)
    save_json(req_contributions, os.path.join(contribution_dir, "requirement_contributions.json"))
    save_json(outcome_contributions, os.path.join(contribution_dir, "outcome_contributions.json"))
    print(f"  Requirements: {len(req_contributions)}, Outcomes: {len(outcome_contributions)}")


# =============================================================================
# Main Pipeline Runner
# =============================================================================

# Default model for Step 1a (Action Extraction); overridden by action_model arg if provided.
DEFAULT_ACTION_EXTRACTION_MODEL = "gpt-5-mini-2025-08-07"


def run_test(
    input_file: str, output_dir: str,
    model: str = "gpt-5.2", turn_block_size: int = 4,
    provider: str = None,
    step2b_req_batch_size: int = 1,
    action_model: str = None,
    action_provider: str = None,
) -> None:
    """Run the full V4 pipeline."""
    os.makedirs(output_dir, exist_ok=True)
    provider, model = get_provider_and_model(provider=provider, model=model)

    raw_log = os.path.join(output_dir, "raw_log.txt")
    client = LoggedLLMClient(provider=provider, model=model, log_file=raw_log)

    # Step 1a (Action Extraction): use action_model if provided, else default
    _act_model = action_model or DEFAULT_ACTION_EXTRACTION_MODEL
    _act_provider = action_provider or "openai"
    _action_provider, _act_model = get_provider_and_model(provider=_act_provider, model=_act_model)
    action_extraction_client = LoggedLLMClient(
        provider=_action_provider, model=_act_model, log_file=raw_log
    )

    # Step 0
    dialogue, dialogue_id = run_step0(input_file, output_dir)

    # Step 1: Unified extraction (Step 1a uses action_extraction_client, rest use client)
    step05_output, step1_output, step1_input = run_step1(
        dialogue, dialogue_id, client, model, output_dir, turn_block_size,
        action_extraction_client=action_extraction_client,
    )

    # Step 1b: Intention extraction
    step05b_output = run_step1b(step05_output, output_dir, client, model)
    step1_input["outcome_to_intention"] = step05b_output.get("outcome_to_intention", {})

    # Step 2a: Embedding pairs
    step2_output, similarity_pairs = run_step2a(
        dialogue, step1_output, step1_input, dialogue_id, output_dir
    )

    # Step 2b: Unified action-requirement labeling (preceding + subsequent)
    step3_output, labeled_pairs = run_step2b(
        similarity_pairs, step1_output, step1_input, step05_output,
        dialogue_id, client, model, output_dir,
        step2b_req_batch_size=step2b_req_batch_size,
    )

    # Step 3: Contribution analysis
    run_step3_contributions(dialogue, step1_output, step3_output, step05_output, output_dir)

    # Merge action-extraction log entries into main client so save_logs writes one unified log
    client.log_entries.extend(action_extraction_client.log_entries)
    client.log_entries.sort(key=lambda e: e.get("timestamp", ""))

    # Save logs
    client.save_logs(output_dir)

    # Summary
    print("\n" + "="*80)
    print("V4 PIPELINE SUMMARY")
    print("="*80)
    print(f"  Outcomes: {len(step05_output.get('outcomes', []))}")
    print(f"  Actions: {len(step05_output.get('all_actions', []))}")
    print(f"  Requirements: {len(step1_output.get('requirements', {}))}")
    print(f"  Similarity pairs: {len(similarity_pairs)}")
    print(f"  Labeled pairs: {len(labeled_pairs)}")
    print(f"  LLM calls: {len(client.log_entries)}")
    print(f"  Output: {output_dir}")


def run_test_up_to_step1(
    input_file: str, output_dir: str,
    model: str = "gpt-5.2", turn_block_size: int = 4,
    provider: str = None,
    action_model: str = None,
    action_provider: str = None,
) -> None:
    """Step 0 + Step 1 (1a outcomes/actions + 1b/1c requirements). Stops before Step 1b intention / Step 2 / Step 3."""
    os.makedirs(output_dir, exist_ok=True)
    provider, model = get_provider_and_model(provider=provider, model=model)

    raw_log = os.path.join(output_dir, "raw_log.txt")
    client = LoggedLLMClient(provider=provider, model=model, log_file=raw_log)

    _act_model = action_model or DEFAULT_ACTION_EXTRACTION_MODEL
    _act_provider = action_provider or "openai"
    _action_provider, _act_model = get_provider_and_model(provider=_act_provider, model=_act_model)
    action_extraction_client = LoggedLLMClient(
        provider=_action_provider, model=_act_model, log_file=raw_log
    )

    dialogue, dialogue_id = run_step0(input_file, output_dir)

    step05_output, step1_output, step1_input = run_step1(
        dialogue, dialogue_id, client, model, output_dir, turn_block_size,
        action_extraction_client=action_extraction_client,
    )

    client.log_entries.extend(action_extraction_client.log_entries)
    client.log_entries.sort(key=lambda e: e.get("timestamp", ""))
    client.save_logs(output_dir)

    print("\n" + "="*80)
    print("V4 PIPELINE (UP TO STEP 1 — OUTCOMES + REQUIREMENTS, NO STEP 2/3)")
    print("="*80)
    print(f"  Outcomes: {len(step05_output.get('outcomes', []))}")
    print(f"  Actions: {len(step05_output.get('all_actions', []))}")
    print(f"  Requirements: {len(step1_output.get('requirements', {}))}")
    print(f"  LLM calls: {len(client.log_entries)}")
    print(f"  Output: {output_dir}")


def _hybrid_turn_kind(turn: dict) -> str:
    return (turn.get("meta") or {}).get("hybrid_kind", "tool")


def _hybrid_turn_use_llm_extraction(turn: dict) -> bool:
    """Teammate NL and START(task_description=...) use ShareChat block LLM; tools use rule CallTool."""
    return _hybrid_turn_kind(turn) in ("teammate_message", "start_task")


def _hybrid_rule_tool_action(turns: list, i: int, raw: str) -> Optional[dict]:
    """Rule-based actions for editor handshake tools (no CallTool row). Returns None if no match."""
    if not raw:
        return None
    t = turns[i]
    sp = t.get("speaker", "unknown")
    r0 = raw.strip()
    rup = r0.upper()

    if rup.startswith("ACCEPT_CONFIRMATION") and "editor_update" in r0.lower().replace(" ", ""):
        return {
            "turn_id": i,
            "action_type": "Accept",
            "action_text": "Accept confirmation for editor_update",
            "role": _normalize_role("SHAPER" if sp == "user" else "OTHER"),
            "evidence_quote": raw[:500],
            "speaker": sp,
        }
    if rup.startswith("REQUEST_TEAMMATE_CONFIRM"):
        return {
            "turn_id": i,
            "action_type": "RequestConfirm",
            "action_text": "Request teammate confirmation for proposed editor update",
            "role": _normalize_role("EXECUTOR"),
            "evidence_quote": raw[:500],
            "speaker": sp,
        }
    return None


def _hybrid_tool_synthetic_action(turns: list, i: int) -> dict:
    t = turns[i]
    meta = t.get("meta") or {}
    raw = meta.get("raw_action")
    if not isinstance(raw, str):
        raw = ""
    raw = raw.strip()
    if raw.startswith("Calling Tool:"):
        raw = raw[len("Calling Tool:") :].strip()
    ruled = _hybrid_rule_tool_action(turns, i, raw)
    if ruled is not None:
        return ruled
    action_text = raw if raw else "(empty)"
    line = f"Calling Tool: {raw}" if raw else "Calling Tool: (empty)"
    return {
        "turn_id": i,
        "action_type": "CallTool",
        "action_text": action_text,
        "role": _normalize_role("EXECUTOR"),
        "evidence_quote": line[:500],
        "speaker": t.get("speaker", "unknown"),
    }


def _is_placeholder_env_start_turn(turn: dict) -> bool:
    """True when a start_task environment turn has only placeholder text like '...'."""
    if (turn.get("speaker") or "") != "environment":
        return False
    meta = turn.get("meta") or {}
    raw = (meta.get("raw_action") or "").strip()
    is_start = (meta.get("hybrid_kind") == "start_task") or raw.startswith("START(")
    if not is_start:
        return False
    text = (turn.get("text") or "").strip()
    return text in ("", "...")


def _extract_env_task_description(dialogue: dict) -> str:
    """Pick best task description text from environment start-task turns."""
    turns = dialogue.get("turns") or []
    # Prefer explicit start_task environment text (non-placeholder)
    for t in turns:
        meta = t.get("meta") or {}
        raw = (meta.get("raw_action") or "").strip()
        is_start = (meta.get("hybrid_kind") == "start_task") or raw.startswith("START(")
        if (t.get("speaker") == "environment") and is_start:
            text = (t.get("text") or "").strip()
            if text and text != "...":
                return text
            # Fallback: parse START(...) fields when text is placeholder.
            if raw.startswith("START("):
                m = re.search(r"query\s*=\s*([^,\)]+(?:\.[^,\)]*)?)", raw)
                if m:
                    q = m.group(1).strip().strip("'\"")
                    if q and q != "...":
                        return q
                m = re.search(r"task_description\s*=\s*([^,\)]+(?:\.[^,\)]*)?)", raw)
                if m:
                    td = m.group(1).strip().strip("'\"")
                    if td and td != "...":
                        return td
    # Fallback: first non-placeholder environment text
    for t in turns:
        if t.get("speaker") == "environment":
            text = (t.get("text") or "").strip()
            if text and text != "...":
                return text
    return ""


def _prepend_env_task_description_to_actions(dialogue: dict, all_actions: List[dict]) -> bool:
    """Insert synthetic env action before other turn-0 actions; renumber turn 0 to 0-0, 0-1, …"""
    env_text = _extract_env_task_description(dialogue)
    if not env_text:
        return False
    block = {
        "turn_id": 0,
        "action_type": "Provide",
        "action_text": f"start and provide task description: {env_text}",
        "role": "EXECUTOR",
        "evidence_quote": env_text,
        "speaker": "environment",
        "bound_outcome_id": "",
    }
    ins = next((i for i, a in enumerate(all_actions) if a.get("turn_id") == 0), None)
    if ins is not None:
        all_actions.insert(ins, block)
    else:
        all_actions.insert(0, block)
    idxs = [i for i, a in enumerate(all_actions) if a.get("turn_id") == 0]
    for j, li in enumerate(idxs):
        all_actions[li]["action_id"] = f"0-{j}"
    return True


def run_step1_hybrid_teammate_tool(
    dialogue: dict, dialogue_id: str,
    client: LoggedLLMClient, model: str,
    output_dir: str, turn_block_size: int = 4,
    action_extraction_client: Optional[LoggedLLMClient] = None,
    action_only: bool = True,
    teammate_batch_single_llm: bool = True,
    with_requirements: bool = False,
    requirements_client: Optional[LoggedLLMClient] = None,
    requirements_model: Optional[str] = None,
    prepend_env_task_action: bool = False,
) -> Tuple[dict, dict, dict]:
    """
    Hybrid Step 1a: BLOCK_ACTION_EXTRACTION (ShareChat variant) on turns with
    meta.hybrid_kind in ('teammate_message', 'start_task'); START uses task_description text only.
    Other turns: rule-based actions; ACCEPT_CONFIRMATION(editor_update) / REQUEST_TEAMMATE_CONFIRM
    use fixed action types (Accept / RequestConfirm), remainder use CallTool with raw opcode text.

    If teammate_batch_single_llm=True (default), all such turns are sent in **one** LLM call.
    If False, start_task is one block; teammate_message runs are chunked by turn_block_size.

    If action_only=True (default), stops after Step 1a (empty outcomes). If action_only=False,
    runs Step 1b (outcomes). Step 1c (requirements) only if with_requirements=True (default False for hybrid).
    """
    step05_file = os.path.join(output_dir, "step05_output.json")
    step1_file = os.path.join(output_dir, "step1_output.json")

    if os.path.exists(step05_file) and os.path.exists(step1_file):
        print("  Step 1 (hybrid): Loading cached outputs...")
        with open(step05_file) as f:
            step05_output = json.load(f)
        with open(step1_file) as f:
            step1_output = json.load(f)
        step1_input = _build_step1_input(step05_output, step1_output, dialogue_id, output_dir)
        return step05_output, step1_output, step1_input

    turns = dialogue.get("turns", [])
    if turn_block_size <= 0:
        turn_block_size = 1

    print("\n" + "="*80)
    batch_note = "single LLM batch (teammate + START task_description)" if teammate_batch_single_llm else f"chunked LLM (block size {turn_block_size})"
    print(f"STEP 1a (HYBRID): NL / START task → LLM ({batch_note}); other tools → Calling Tool:")
    print("="*80)

    all_actions: List[dict] = []
    step1a_client = action_extraction_client if action_extraction_client else client
    step1a_model = step1a_client.model if action_extraction_client else model

    n = len(turns)
    llm_batch_indices = [
        i for i in range(n)
        if _hybrid_turn_use_llm_extraction(turns[i]) and not _is_placeholder_env_start_turn(turns[i])
    ]
    valid_llm_turns = set(llm_batch_indices)

    if teammate_batch_single_llm:
        llm_by_turn: Dict[int, List[dict]] = defaultdict(list)
        if llm_batch_indices:
            print(f"  Hybrid LLM single block: turns {llm_batch_indices} (teammate_message + start_task)")
            raw_actions = extract_block_actions(
                turns, 0, 0, step1a_client, step1a_model,
                use_sharechat_block_prompt=True,
                turn_indices=llm_batch_indices,
            )
            for a in raw_actions:
                try:
                    a = dict(a)
                except (ValueError, TypeError):
                    print(f"  Warning: skipping malformed action (got {type(a).__name__}): {repr(a)[:80]}")
                    continue
                tid = a.get("turn_id", llm_batch_indices[0])
                try:
                    tid = int(tid)
                except (TypeError, ValueError):
                    print(f"  Warning: bad turn_id on action {repr(a)[:80]}")
                    continue
                if tid not in valid_llm_turns:
                    print(f"  Warning: turn_id={tid} not in LLM turns {llm_batch_indices}; skipping")
                    continue
                a["role"] = _normalize_role(a.get("role", ""))
                a["speaker"] = turns[tid].get("speaker", "unknown")
                llm_by_turn[tid].append(a)
        for i in range(n):
            if not _hybrid_turn_use_llm_extraction(turns[i]):
                if _is_placeholder_env_start_turn(turns[i]):
                    continue
                all_actions.append(_hybrid_tool_synthetic_action(turns, i))
            else:
                all_actions.extend(llm_by_turn.get(i, []))
    else:
        i = 0
        while i < n:
            if _hybrid_turn_kind(turns[i]) == "start_task":
                if _is_placeholder_env_start_turn(turns[i]):
                    i += 1
                    continue
                print(f"  Hybrid LLM block START task_description turn {i}")
                raw_actions = extract_block_actions(
                    turns, i, i + 1, step1a_client, step1a_model,
                    use_sharechat_block_prompt=True,
                )
                for a in raw_actions:
                    try:
                        a = dict(a)
                    except (ValueError, TypeError):
                        print(f"  Warning: skipping malformed action (got {type(a).__name__}): {repr(a)[:80]}")
                        continue
                    turn_id = a.get("turn_id", i)
                    try:
                        turn_id = int(turn_id)
                    except (TypeError, ValueError):
                        turn_id = i
                    a["role"] = _normalize_role(a.get("role", ""))
                    if 0 <= turn_id < len(turns):
                        a["speaker"] = turns[turn_id].get("speaker", "unknown")
                    else:
                        a["speaker"] = "unknown"
                    all_actions.append(a)
                i += 1
                continue
            if _hybrid_turn_kind(turns[i]) != "teammate_message":
                all_actions.append(_hybrid_tool_synthetic_action(turns, i))
                i += 1
                continue
            j = i
            while j < n and _hybrid_turn_kind(turns[j]) == "teammate_message":
                j += 1
            bs = i
            while bs < j:
                be = min(bs + turn_block_size, j)
                print(f"  Hybrid LLM block teammate turns {bs}-{be - 1}")
                raw_actions = extract_block_actions(
                    turns, bs, be, step1a_client, step1a_model,
                    use_sharechat_block_prompt=True,
                )
                for a in raw_actions:
                    try:
                        a = dict(a)
                    except (ValueError, TypeError):
                        print(f"  Warning: skipping malformed action (got {type(a).__name__}): {repr(a)[:80]}")
                        continue
                    turn_id = a.get("turn_id", bs)
                    try:
                        turn_id = int(turn_id)
                    except (TypeError, ValueError):
                        turn_id = bs
                    a["role"] = _normalize_role(a.get("role", ""))
                    if 0 <= turn_id < len(turns):
                        a["speaker"] = turns[turn_id].get("speaker", "unknown")
                    else:
                        a["speaker"] = "unknown"
                    all_actions.append(a)
                bs = be
            i = j

    turn_actions_map = defaultdict(list)
    for a in all_actions:
        turn_actions_map[a["turn_id"]].append(a)
    for tid, alist in turn_actions_map.items():
        for idx, a in enumerate(alist, start=1):
            a["action_id"] = f"{tid}-{idx}"

    if prepend_env_task_action and not action_only:
        if _prepend_env_task_description_to_actions(dialogue, all_actions):
            print("  Prepended env task-description action (for Step 1b/1c)")

    print(f"  Total actions: {len(all_actions)}")

    if not action_only:
        meta_extra = {
                "hybrid_teammate_tool_extraction": True,
                "action_only": False,
                "teammate_batch_single_llm": teammate_batch_single_llm,
        }
        if prepend_env_task_action:
            meta_extra["prepend_env_task_action"] = True
        return _run_step1b1c_from_actions(
            dialogue, dialogue_id, all_actions, client, model, output_dir,
            extra_step05_metadata=meta_extra,
            include_requirements=with_requirements,
            requirements_client=requirements_client,
            requirements_model=requirements_model,
        )

    dialogue_summary = dialogue.get("dialogue_summary", "")
    step05_output = {
        "dialogue_id": dialogue_id,
        "dialogue_summary": dialogue_summary,
        "outcomes": [],
        "outcome_versions": [],
        "outcome_actions": {},
        "all_actions": all_actions,
        "metadata": {
            "num_outcomes": 0,
            "num_outcome_versions": 0,
            "num_actions": len(all_actions),
            "model": model,
            "action_only": True,
            "hybrid_teammate_tool_extraction": True,
            "teammate_batch_single_llm": teammate_batch_single_llm,
        },
    }
    save_json(step05_output, step05_file)
    step1_output = {
        "dialogue_id": dialogue_id,
        "requirements": {},
        "threads": {},
        "operations_log": [],
        "others": [],
        "metadata": {"num_requirements": 0, "num_threads": 0, "num_operations": 0, "model": model},
    }
    save_json(step1_output, step1_file)
    parent_dir = os.path.dirname(output_dir)
    oam_path = (
        os.path.join(parent_dir, "outcome_action_map.json")
        if parent_dir
        else os.path.join(output_dir, "outcome_action_map.json")
    )
    save_json({"dialogue_id": dialogue_id, "outcome_action_map": {}}, oam_path)
    step1_input = _build_step1_input(step05_output, step1_output, dialogue_id, output_dir)
    return step05_output, step1_output, step1_input


def run_test_up_to_standard_step1(
    input_file: str, output_dir: str,
    model: str = "gpt-5.2", turn_block_size: int = 4,
    provider: str = None,
    action_model: str = None,
    action_provider: str = None,
    with_outcomes: bool = True,
) -> None:
    """Step 0 + standard run_step1: block LLM on the full dialogue (default block prompt, not ShareChat)."""
    os.makedirs(output_dir, exist_ok=True)
    provider, model = get_provider_and_model(provider=provider, model=model)

    raw_log = os.path.join(output_dir, "raw_log.txt")
    client = LoggedLLMClient(provider=provider, model=model, log_file=raw_log)

    _act_model = action_model or DEFAULT_ACTION_EXTRACTION_MODEL
    _act_provider = action_provider or "openai"
    _action_provider, _act_model = get_provider_and_model(provider=_act_provider, model=_act_model)
    action_extraction_client = LoggedLLMClient(
        provider=_action_provider, model=_act_model, log_file=raw_log
    )

    dialogue, dialogue_id = run_step0(input_file, output_dir)

    step05_output, step1_output, _ = run_step1(
        dialogue, dialogue_id, client, model, output_dir, turn_block_size,
        action_extraction_client=action_extraction_client,
        action_only=not with_outcomes,
    )

    meta = step05_output.setdefault("metadata", {})
    meta["cogym_event_log_standard_step1"] = True
    save_json(step05_output, os.path.join(output_dir, "step05_output.json"))

    client.log_entries.extend(action_extraction_client.log_entries)
    client.log_entries.sort(key=lambda e: e.get("timestamp", ""))

    client.save_logs(output_dir)

    print("\n" + "="*80)
    label = "STANDARD STEP 1 (1a + 1b + 1c)" if with_outcomes else "STANDARD STEP 1a ONLY"
    print(f"V4 PIPELINE ({label}) — full-block extraction")
    print("="*80)
    print(f"  Actions: {len(step05_output.get('all_actions', []))}")
    if with_outcomes:
        print(f"  Outcomes: {len(step05_output.get('outcomes', []))}")
        print(f"  Requirements: {len((step1_output or {}).get('requirements', {}) or {})}")
    print(f"  LLM calls (total): {len(client.log_entries)}")
    print(f"  Output: {output_dir}")


def run_test_up_to_hybrid_action_extraction(
    input_file: str, output_dir: str,
    model: str = "gpt-5.2", turn_block_size: int = 4,
    provider: str = None,
    action_model: str = None,
    action_provider: str = None,
    with_outcomes: bool = True,
    teammate_batch_single_llm: bool = True,
    with_requirements: bool = False,
    requirements_model: str = None,
    requirements_provider: str = None,
    prepend_env_task_action: bool = False,
) -> None:
    """Step 0 + hybrid Step 1a; optionally Step 1b; Step 1c only if with_requirements."""
    os.makedirs(output_dir, exist_ok=True)
    provider, model = get_provider_and_model(provider=provider, model=model)

    raw_log = os.path.join(output_dir, "raw_log.txt")
    client = LoggedLLMClient(provider=provider, model=model, log_file=raw_log)

    _act_model = action_model or DEFAULT_ACTION_EXTRACTION_MODEL
    _act_provider = action_provider or "openai"
    _action_provider, _act_model = get_provider_and_model(provider=_act_provider, model=_act_model)
    action_extraction_client = LoggedLLMClient(
        provider=_action_provider, model=_act_model, log_file=raw_log
    )

    req_llm_client = None
    req_model_resolved = None
    if requirements_model:
        _req_provider = requirements_provider or "openai"
        _req_provider, req_model_resolved = get_provider_and_model(provider=_req_provider, model=requirements_model)
        req_llm_client = LoggedLLMClient(provider=_req_provider, model=req_model_resolved, log_file=raw_log)

    dialogue, dialogue_id = run_step0(input_file, output_dir)

    step05_output, step1_output, _ = run_step1_hybrid_teammate_tool(
        dialogue, dialogue_id, client, model, output_dir, turn_block_size,
        action_extraction_client=action_extraction_client,
        action_only=not with_outcomes,
        teammate_batch_single_llm=teammate_batch_single_llm,
        with_requirements=with_requirements if with_outcomes else False,
        requirements_client=req_llm_client,
        requirements_model=req_model_resolved,
        prepend_env_task_action=prepend_env_task_action,
    )

    client.log_entries.extend(action_extraction_client.log_entries)
    if req_llm_client:
        client.log_entries.extend(req_llm_client.log_entries)
    client.log_entries.sort(key=lambda e: e.get("timestamp", ""))

    client.save_logs(output_dir)

    print("\n" + "="*80)
    if not with_outcomes:
        label = "HYBRID STEP 1a ONLY"
    elif with_requirements:
        label = "HYBRID STEP 1 (1a + 1b + 1c)"
    else:
        label = "HYBRID STEP 1 (1a + 1b, no 1c)"
    print(f"V4 PIPELINE ({label}) SUMMARY")
    print("="*80)
    print(f"  Actions: {len(step05_output.get('all_actions', []))}")
    if with_outcomes:
        print(f"  Outcomes: {len(step05_output.get('outcomes', []))}")
        if with_requirements:
            print(f"  Requirements: {len((step1_output or {}).get('requirements', {}) or {})}")
    print(f"  LLM calls (total): {len(client.log_entries)}")
    print(f"  Output: {output_dir}")


def run_test_up_to_action_extraction(
    input_file: str, output_dir: str,
    model: str = "gpt-5.2", turn_block_size: int = 4,
    provider: str = None,
    action_model: str = None,
    action_provider: str = None,
) -> None:
    """Run pipeline Step 0 + Step 1a only (action extraction per block). No outcomes, no requirements."""
    os.makedirs(output_dir, exist_ok=True)
    provider, model = get_provider_and_model(provider=provider, model=model)

    raw_log = os.path.join(output_dir, "raw_log.txt")
    client = LoggedLLMClient(provider=provider, model=model, log_file=raw_log)

    _act_model = action_model or DEFAULT_ACTION_EXTRACTION_MODEL
    _act_provider = action_provider or "openai"
    _action_provider, _act_model = get_provider_and_model(provider=_act_provider, model=_act_model)
    action_extraction_client = LoggedLLMClient(
        provider=_action_provider, model=_act_model, log_file=raw_log
    )

    # Step 0
    dialogue, dialogue_id = run_step0(input_file, output_dir)

    # Step 1a only (action extraction per block; no Step 1b outcomes, no Step 1c requirements)
    step05_output, step1_output, _ = run_step1(
        dialogue, dialogue_id, client, model, output_dir, turn_block_size,
        action_extraction_client=action_extraction_client,
        action_only=True,
    )

    client.log_entries.extend(action_extraction_client.log_entries)
    client.log_entries.sort(key=lambda e: e.get("timestamp", ""))

    client.save_logs(output_dir)

    print("\n" + "="*80)
    print("V4 PIPELINE (STEP 1a — ACTION EXTRACTION ONLY) SUMMARY")
    print("="*80)
    print(f"  Actions: {len(step05_output.get('all_actions', []))}")
    print(f"  LLM calls: {len(client.log_entries)}")
    print(f"  Output: {output_dir}")
