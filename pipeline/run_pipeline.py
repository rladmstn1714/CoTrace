#!/usr/bin/env python3
"""
Run pipeline and write outputs in parsed format.

  - utterance_list.json
  - requirement_relations.jsonl
  - requirements_outputs_lists.json
  - requirement_output_dependency.json
  - requirement_contributions.json
  - output_contributions.json
  - requirement_action_map.json
  - action_utterance_map.json
  - requirement_forward_labels.json (step2c forward_labels, when present)
  - requirement_status.json
  - intent_outcome_map.json
"""

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from collections import defaultdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from utils.helpers import is_influence_action_after_req_origin, is_preceding_turn_for_req

def sanitize_folder_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "chat_source"
    s = re.sub(r"[^\w\-.]", "_", s)
    return s[:200] if len(s) > 200 else s


_DEFAULT_CHAT_OUTPUT_ROOT = Path.cwd() / "outputs"
POE_SHARE_URL_RE = re.compile(r"^https?://(?:www\.)?poe\.com/s/([A-Za-z0-9_-]+)(?:[/?#].*)?$")


def parse_poe_share_code(url: str) -> str:
    m = POE_SHARE_URL_RE.match((url or "").strip())
    if not m:
        raise ValueError(
            f"Expected a Poe share URL like https://poe.com/s/CODE, got: {url!r}"
        )
    return m.group(1)


def _fetch_poe_share_html(url: str) -> str:
    req = urllib.request.Request(
        url.strip(),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_poe_share_to_step0(url: str) -> dict:
    """
    Download a Poe shared chat page and build a step0-style dialogue dict
    (dialogue_id, turns with turn_id, speaker, text, meta).

    Poe embeds the thread in __NEXT_DATA__; messages are under
    pageProps.data.mainQuery.chatShare.messages.
    """
    html_text = _fetch_poe_share_html(url)
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html_text,
        re.DOTALL,
    )
    if not m:
        raise ValueError(
            "Could not find __NEXT_DATA__ on the Poe page (layout may have changed)."
        )
    data = json.loads(m.group(1))
    share = (
        data.get("props", {})
        .get("pageProps", {})
        .get("data", {})
        .get("mainQuery", {})
        .get("chatShare")
    )
    if not share:
        raise ValueError("Poe page did not contain chatShare data.")
    messages = share.get("messages") or []
    if not messages:
        raise ValueError("Poe share has no messages.")

    messages = sorted(messages, key=lambda x: x.get("creationTime") or 0)
    share_code = share.get("shareCode") or parse_poe_share_code(url)
    dialogue_id = f"poe_share_{share_code}"

    turns = []
    for i, msg in enumerate(messages):
        author = (msg.get("author") or "").strip()
        ab = msg.get("authorBot") or {}
        display = (ab.get("displayName") or ab.get("nickname") or "").strip()
        if author == "human" or display.lower() == "human":
            speaker = "user"
        else:
            speaker = "assistant"
        text = msg.get("text") or ""
        text = html.unescape(text)
        turns.append({
            "turn_id": i,
            "speaker": speaker,
            "text": text,
            "meta": {
                "source": "poe_share",
                "poe_author": author,
                "message_id": msg.get("messageId"),
            },
        })

    return {
        "dialogue_id": dialogue_id,
        "turns": turns,
        "metadata": {
            "source": "poe_share",
            "input_diag": url.strip(),
            "share_code": share_code,
        },
    }


def _looks_like_url(value: str) -> bool:
    p = urlparse((value or "").strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def _discover_chat_file(chat_path: Path) -> Path:
    if chat_path.is_file():
        if chat_path.suffix.lower() not in (".json", ".jsonl"):
            raise ValueError(f"Chat file must be .json or .jsonl, got: {chat_path}")
        return chat_path
    if not chat_path.is_dir():
        raise ValueError(f"Chat path does not exist: {chat_path}")

    preferred = ["input_dialogue.json", "dialogue.json", "step0_input.json"]
    for name in preferred:
        cand = chat_path / name
        if cand.is_file():
            return cand

    files = sorted(list(chat_path.glob("*.json")) + list(chat_path.glob("*.jsonl")))
    if not files:
        raise ValueError(
            f"No .json/.jsonl dialogue file found in {chat_path}. "
            "Add one dialogue file or pass --input_dir for batch processing."
        )
    if len(files) > 1:
        names = ", ".join(f.name for f in files[:5])
        if len(files) > 5:
            names += ", ..."
        raise ValueError(
            f"Found multiple dialogue files in {chat_path}: {names}. "
            "Keep one file in the directory, pass a specific file path via --input_diag, "
            "or use --input_dir for batch runs."
        )
    return files[0]


def prepare_chat_input(input_diag: str, output_dir: Path) -> tuple[Path, str]:
    """Resolve --input_diag into an input dialogue file and a folder-name hint."""
    value = (input_diag or "").strip()
    if _looks_like_url(value):
        share_code = parse_poe_share_code(value)
        dialogue = fetch_poe_share_to_step0(value)
        prefetched_input_path = output_dir / "chat_dialogue_input.json"
        save_json(prefetched_input_path, dialogue)
        print(
            f"Chat URL → {len(dialogue['turns'])} turns, dialogue_id={dialogue['dialogue_id']!r}, "
            f"wrote {prefetched_input_path}"
        )
        return prefetched_input_path, share_code

    chat_path = Path(value).expanduser().resolve()
    chat_file = _discover_chat_file(chat_path)
    folder_hint = chat_path.name if chat_path.is_dir() else chat_file.stem
    return chat_file, folder_hint


def default_chat_folder_name(input_diag: str) -> str:
    value = (input_diag or "").strip()
    if _looks_like_url(value):
        return parse_poe_share_code(value)
    chat_path = Path(value).expanduser().resolve()
    if chat_path.is_dir():
        return chat_path.name
    if chat_path.is_file():
        return chat_path.stem
    return "chat"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def save_jsonl(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


OP_TO_RELATION = {
    "create": "CREATE", "revise": "REVISE", "delete": "DELETE",
    "split": "SPLIT", "merge": "MERGE", "deprecate": "DEPRECATE",
    "satisfy": "SATISFY", "fail": "FAIL",
}


def _requirement_outcome_id(step1: dict, thread_to_outcome: dict, req_id: str, thread_id) -> str:
    """Prefer explicit requirement.outcome_id (outcome-first); else thread map; else default."""
    r = (step1.get("requirements") or {}).get(req_id) or {}
    oid = r.get("outcome_id")
    if oid:
        return oid
    tid = thread_id if thread_id is not None else r.get("thread_id", 1)
    return thread_to_outcome.get(str(tid), f"outcome_{tid}")


def _op_to_relation_type(op_type: str) -> str:
    if not op_type:
        return "NONE"
    return OP_TO_RELATION.get(op_type, op_type.upper())


# =============================================================================
# Conversion functions (same output format as V3)
# =============================================================================

def to_utterance_list(step0: dict) -> dict:
    utterances = []
    for t in step0.get("turns", []):
        utterances.append({
            "turn_id": t["turn_id"],
            "speaker": t.get("speaker", "user"),
            "utterance": t.get("text", ""),
        })
    return {"dialogue_id": step0.get("dialogue_id", "unknown"), "utterances": utterances}


def to_requirement_relations(step1: dict) -> list:
    threads = step1.get("threads", {})
    thread_to_outcome = {tid: t.get("outcome_id", f"outcome_{tid}") for tid, t in threads.items()}
    rows = []
    revise_ver = defaultdict(int)

    for entry in step1.get("operations_log", []):
        turn_id = entry.get("turn_id", 0)
        for op in entry.get("ops", []):
            req_id = op.get("req_id")
            if not req_id:
                continue
            op_type = op.get("op", "create")
            relation_type = _op_to_relation_type(op_type)
            thread_id = op.get("thread_id", 1)
            outcome_id = _requirement_outcome_id(step1, thread_to_outcome, req_id, thread_id)

            if op_type == "revise" and req_id and not re.match(r"^.+_\d+$", req_id):
                revise_ver[req_id] += 1
                req_id = f"{req_id}_{revise_ver[req_id]}"

            related = None
            if op_type in ("revise", "deprecate", "delete", "merge"):
                related = op.get("related_to")
            elif op_type == "split":
                related = (op.get("related_to") or [None])[0] if op.get("related_to") else None

            rows.append({
                "t": turn_id,
                "requirement_id": req_id,
                "operation_type": relation_type,
                "related_prev_requirement": related,
                "outcome_id": outcome_id,
            })

    if not rows and step1.get("requirements"):
        for req_id, r in step1["requirements"].items():
            turn_id = r.get("origin_turn_id", r.get("created_at", 0))
            thread_id = r.get("thread_id", 1)
            outcome_id = _requirement_outcome_id(step1, thread_to_outcome, req_id, thread_id)
            rows.append({
                "t": turn_id, "requirement_id": req_id, "operation_type": "ADD",
                "related_prev_requirement": None, "outcome_id": outcome_id,
            })
    return rows


def to_requirements_outputs_lists(step1: dict, dialogue_id: str, step05: dict = None) -> dict:
    requirements = []
    for req_id, r in step1.get("requirements", {}).items():
        requirements.append({
            "id": req_id,
            "content": r.get("text", ""),
            "turn_id": r.get("origin_turn_id", r.get("created_at", 0)),
            "explicit_or_implicit": r.get("explicit_or_implicit", "explicit"),
        })
    requirements.sort(key=lambda x: (x["turn_id"], x["id"]))

    outputs = []
    valid_outcome_ids = set()
    outcome_versions = (step05 or {}).get("outcome_versions") or []
    threads_by_outcome = {t.get("outcome_id"): t for t in step1.get("threads", {}).values()}
    # Map outcome_id -> outcome dict for parent/child (from step05.outcomes)
    outcomes_by_id = {}
    for o in (step05 or {}).get("outcomes") or []:
        oid = o.get("outcome_id")
        if oid:
            outcomes_by_id[oid] = o

    if outcome_versions:
        by_oid = defaultdict(list)
        for v in outcome_versions:
            oid = v.get("outcome_id") or v.get("original_outcome_id")
            if oid:
                by_oid[oid].append(v)
        for oid in sorted(by_oid.keys()):
            valid_outcome_ids.add(oid)
            versions = sorted(by_oid[oid], key=lambda x: (x.get("turn_id", 0), x.get("block_start", 0)))
            related_ids = (threads_by_outcome.get(oid) or {}).get("related_outcome_ids") or []
            o_outcome = outcomes_by_id.get(oid, {})
            parent_oid = o_outcome.get("parent_outcome_id")
            child_ids = o_outcome.get("child_outcome_ids") or []
            for idx, v in enumerate(versions):
                outputs.append({
                    "id": f"{oid}_{idx}",
                    "content": v.get("outcome", ""),
                    "turn_id": v.get("turn_id", 0),
                    "related_outcome_ids": related_ids,
                    "parent_outcome_id": parent_oid,
                    "child_outcome_ids": child_ids,
                })
    elif (step05 or {}).get("outcomes"):
        # Outcome-first: threads may be empty (e.g. some pipelines); use step05.outcomes for outputs list
        for o in sorted(
            (step05 or {}).get("outcomes") or [],
            key=lambda x: (x.get("created_at_turn", x.get("turn_id", 0)), str(x.get("outcome_id") or "")),
        ):
            oid = o.get("outcome_id")
            if not oid:
                continue
            valid_outcome_ids.add(oid)
            outputs.append({
                "id": f"{oid}_0",
                "content": o.get("description", o.get("outcome", oid)),
                "turn_id": o.get("created_at_turn", o.get("turn_id", 0)),
                "related_outcome_ids": o.get("related_outcome_ids") or [],
                "parent_outcome_id": o.get("parent_outcome_id"),
                "child_outcome_ids": o.get("child_outcome_ids") or [],
            })
    else:
        for tid, t in step1.get("threads", {}).items():
            oid = t.get("outcome_id", f"outcome_{tid}")
            valid_outcome_ids.add(oid)
            # Prefer step05.outcomes for parent/child; fallback to thread
            o_outcome = outcomes_by_id.get(oid, t)
            parent_oid = o_outcome.get("parent_outcome_id")
            child_ids = o_outcome.get("child_outcome_ids") or []
            outputs.append({
                "id": f"{oid}_0",
                "content": t.get("outcome", oid),
                "turn_id": t.get("created_at", 0),
                "related_outcome_ids": t.get("related_outcome_ids") or [],
                "parent_outcome_id": parent_oid,
                "child_outcome_ids": child_ids,
            })

    # Outcome-first: dialogue_summary + outcomes each with nested actions (then next outcome)
    outcome_first = None
    if step05:
        outcome_first = {
            "dialogue_summary": step05.get("dialogue_summary", ""),
            "outcomes": (step05.get("outcomes") or []),
        }

    return {
        "dialogue_id": dialogue_id,
        "requirements": requirements,
        "outputs": outputs,
        "dialogue_summary": outcome_first["dialogue_summary"] if outcome_first else "",
        "outcomes": outcome_first["outcomes"] if outcome_first else [],
    }


def to_requirement_output_dependency(step1: dict, step0: dict, dialogue_id: str) -> dict:
    req_to_outcome = {}
    threads = step1.get("threads", {})
    for req_id, r in step1.get("requirements", {}).items():
        oid = r.get("outcome_id")
        if oid:
            req_to_outcome[req_id] = oid
            continue
        thread_id = r.get("thread_id", 1)
        t = threads.get(str(thread_id), {})
        req_to_outcome[req_id] = t.get("outcome_id", f"outcome_{thread_id}")
    turns = step0.get("turns", [])
    final_turn = max((t["turn_id"] for t in turns), default=0) if turns else 0
    return {"dialogue_id": dialogue_id, "final_turn": final_turn, "requirement_to_outcome": req_to_outcome}


def _role_contributions_to_rates(raw_rc: dict) -> tuple:
    all_roles = set()
    for agent_roles in (raw_rc or {}).values():
        all_roles.update(agent_roles.keys())
    role_contributions = {}
    for role in sorted(all_roles):
        user_m = raw_rc.get("user", {}).get(role, {})
        asst_m = raw_rc.get("assistant", {}).get(role, {})
        m_user = user_m.get("M_total", 0.0)
        m_asst = asst_m.get("M_total", 0.0)
        total = m_user + m_asst
        role_contributions[role] = {
            "user": {"rate": m_user / total if total > 0 else 0.0, "M_total": m_user,
                     "count": user_m.get("count", 0), "M_dir": user_m.get("M_dir", 0.0), "M_ind": user_m.get("M_ind", 0.0)},
            "assistant": {"rate": m_asst / total if total > 0 else 0.0, "M_total": m_asst,
                          "count": asst_m.get("count", 0), "M_dir": asst_m.get("M_dir", 0.0), "M_ind": asst_m.get("M_ind", 0.0)},
        }
    m_user_total = sum((raw_rc.get("user") or {}).get(r, {}).get("M_total", 0.0) for r in all_roles)
    m_asst_total = sum((raw_rc.get("assistant") or {}).get(r, {}).get("M_total", 0.0) for r in all_roles)
    total = m_user_total + m_asst_total
    overall = {
        "user": {"rate": m_user_total / total if total > 0 else 0.0, "M_total": m_user_total,
                 "count": sum((raw_rc.get("user") or {}).get(r, {}).get("count", 0) for r in all_roles)},
        "assistant": {"rate": m_asst_total / total if total > 0 else 0.0, "M_total": m_asst_total,
                      "count": sum((raw_rc.get("assistant") or {}).get(r, {}).get("count", 0) for r in all_roles)},
    }
    return role_contributions, overall


def to_requirement_contributions_parsed(req_contributions: dict, step1: dict) -> dict:
    result = {}
    for req_id, c in req_contributions.items():
        r = step1.get("requirements", {}).get(req_id, {})
        origin_turn_id = r.get("origin_turn_id", r.get("created_at", 0))
        created_by = "user"
        inf_utts = []
        for speaker, data in c.get("speaker_contributions", {}).items():
            for u in data.get("influential_utterances", []):
                if u.get("turn_id") == origin_turn_id and u.get("is_origin"):
                    created_by = speaker
                inf_utts.append({
                    "turn_id": u["turn_id"],
                    "relationship_type": u.get("relationship_type", "IMPLICIT_CONNECTION"),
                    "relationship_score": u.get("relationship_score", 0) or 0,
                })
        seen = set()
        unique_inf = []
        for u in inf_utts:
            if u["turn_id"] not in seen:
                seen.add(u["turn_id"])
                unique_inf.append(u)
        unique_inf.sort(key=lambda x: x["turn_id"])
        raw_rc = c.get("role_contributions", {})
        _, overall = _role_contributions_to_rates(raw_rc)
        result[req_id] = {
            "created_by": created_by, "turn_id": origin_turn_id,
            "influential_utterances": unique_inf, "role_contributions": raw_rc, "overall": overall,
        }
    return result


def output_contributions_from_requirements(req_contributions: dict, req_to_outcome: dict, step1: dict) -> dict:
    oid_to_reqs = defaultdict(list)
    for rid, oid in req_to_outcome.items():
        if oid:
            oid_to_reqs[oid].append(rid)

    result = {}
    for oid, req_ids in oid_to_reqs.items():
        agg = defaultdict(lambda: defaultdict(lambda: {"M_dir": 0.0, "M_ind": 0.0, "M_total": 0.0, "count": 0}))
        for rid in req_ids:
            raw_rc = req_contributions.get(rid, {}).get("role_contributions", {})
            for agent in ("user", "assistant"):
                for role, data in (raw_rc.get(agent) or {}).items():
                    agg[agent][role]["M_dir"] += data.get("M_dir", 0.0)
                    agg[agent][role]["M_ind"] += data.get("M_ind", 0.0)
                    agg[agent][role]["M_total"] += data.get("M_total", 0.0)
                    agg[agent][role]["count"] += data.get("count", 0)
        raw_rc_dict = {"user": dict(agg["user"]), "assistant": dict(agg["assistant"])}
        role_c, overall = _role_contributions_to_rates(raw_rc_dict)
        result[oid] = {"role_contributions": role_c, "overall": overall}

    for tid, thread in step1.get("threads", {}).items():
        oid = thread.get("outcome_id", f"outcome_{tid}")
        if oid not in result:
            rc, overall = _role_contributions_to_rates({})
            result[oid] = {"role_contributions": rc, "overall": overall}
    return result


def to_requirement_status(step1: dict) -> list:
    dismissed = {}
    for entry in step1.get("operations_log", []):
        turn_id = entry.get("turn_id")
        for op in entry.get("ops", []):
            if op.get("op", "").lower() == "delete":
                rid = op.get("req_id")
                if rid and rid not in dismissed:
                    dismissed[rid] = {"action_ids": op.get("creation_action_ids", []), "turn": turn_id}

    statuses = []
    for req_id, r in step1.get("requirements", {}).items():
        d = dismissed.get(req_id, {})
        statuses.append({
            "id": req_id,
            "is_dismissed": req_id in dismissed,
            "dismissed_by_action_ids": d.get("action_ids", []),
            "dismissed_at_turn": d.get("turn"),
            "is_executed": len(r.get("implementation_action_ids", [])) > 0,
        })
    return statuses


def to_action_utterance_map(step05: dict) -> dict:
    result = {}
    for a in step05.get("all_actions", []):
        aid = a.get("action_id")
        if aid:
            result[aid] = {"evidence_quote": a.get("evidence_quote", ""), "turn_id": a.get("turn_id", 0)}
    return result


def _norm_action_id(aid: str) -> str:
    """Normalize action_id: strip brackets so \"[0-1]\" matches step05 \"0-1\"."""
    if not isinstance(aid, str) or not aid:
        return aid or ""
    return aid.strip().strip("[]").strip()


def _req_origin_action_ids(req: dict) -> list:
    """Requirement actions that created the req: step1 uses creation_action_ids; V3-style uses origin_action_ids."""
    o = req.get("origin_action_ids")
    if o:
        return list(o)
    c = req.get("creation_action_ids")
    return list(c) if c else []


def _subsequent_implicit_action_ids_by_req(step2c: dict) -> dict:
    """Per req_id, return set of action_ids that are subsequent and labeled IMPLICIT_CONNECTION/DIRECT_CONNECTION (exclude from requirement_action_map)."""
    out = {}
    for req_id, fwd in (step2c.get("forward_labels") or {}).items():
        exclude = set()
        for lbl in fwd.get("all_labels", []):
            if lbl.get("relationship") in ("IMPLICIT_CONNECTION", "DIRECT_CONNECTION"):
                aid = _norm_action_id(lbl.get("action_id") or "")
                if aid:
                    exclude.add(aid)
        out[req_id] = exclude
    return out


def _merge_step2c_into_related(req_id: str, step1: dict, step2c: dict, action_lookup: dict, req_to_related: dict) -> None:
    """Add related_actions from step2c forward all_labels when not already present from step3 pairs."""
    if not step2c:
        return
    req = step1.get("requirements", {}).get(req_id) or {}
    origin_turn = req.get("origin_turn_id", req.get("created_at", 0))
    origin_ids = {_norm_action_id(a) for a in _req_origin_action_ids(req) if _norm_action_id(a)}
    existing = {r["action_id"] for r in req_to_related.get(req_id, [])}
    fwd = (step2c.get("forward_labels") or {}).get(req_id) or {}
    for lbl in fwd.get("all_labels", []):
        rel = lbl.get("relationship") or "NO_CONNECTION"
        if rel == "NO_CONNECTION":
            continue
        aid = _norm_action_id(lbl.get("action_id") or "")
        if not aid or aid in origin_ids or aid in existing:
            continue
        if rel in ("DIRECT_CONNECTION", "IMPLICIT_CONNECTION") and is_influence_action_after_req_origin(
            aid, origin_turn, action_lookup
        ):
            continue
        existing.add(aid)
        info = action_lookup.get(aid, {})
        role = (lbl.get("contribution_role") or info.get("role") or "OTHER").strip() or "OTHER"
        influence = "direct" if rel == "DIRECT_CONNECTION" else "indirect"
        req_to_related[req_id].append({
            "action_id": aid, "role": role, "action_text": info.get("action_text", ""),
            "relationship_type": rel,
            "influence": influence, "relationship_score": lbl.get("relationship_score"),
            "explanation": lbl.get("explanation", ""),
        })


def to_requirement_action_map(step1: dict, step3: dict, step05: dict, step2c: dict = None) -> dict:
    action_lookup = {}
    for a in step05.get("all_actions", []):
        aid = a.get("action_id")
        if aid:
            action_lookup[aid] = {"role": a.get("role", "OTHER"), "action_text": a.get("action_text", "")}

    subsequent_implicit_by_req = _subsequent_implicit_action_ids_by_req(step2c) if step2c else {}

    req_to_related = defaultdict(list)
    for pair in step3.get("pairs", []):
        req_id = pair.get("req_id")
        if not req_id:
            continue
        req = step1.get("requirements", {}).get(req_id) or {}
        origin_turn = pair.get("origin_turn_id", req.get("origin_turn_id", req.get("created_at", 0)))
        if not is_preceding_turn_for_req(pair.get("prev_turn_id", origin_turn), origin_turn):
            continue
        origin_ids = set()
        for r in step1.get("requirements", {}).values():
            if r.get("req_id") == req_id:
                origin_ids = {_norm_action_id(a) for a in _req_origin_action_ids(r) if _norm_action_id(a)}
                break
        for ar in pair.get("action_relationships", []):
            if ar.get("relationship_type") == "NO_CONNECTION":
                continue
            aid = _norm_action_id(ar.get("action_id") or "")
            if not aid or aid in origin_ids:
                continue
            rel_type = ar.get("relationship_type", "NO_CONNECTION")
            if rel_type in ("DIRECT_CONNECTION", "IMPLICIT_CONNECTION") and is_influence_action_after_req_origin(
                aid, origin_turn, action_lookup
            ):
                continue
            info = action_lookup.get(aid, {})
            role = (ar.get("contribution_role") or info.get("role") or "OTHER").strip() or "OTHER"
            influence = "direct" if ar.get("relationship_type") == "DIRECT_CONNECTION" else "indirect"
            req_to_related[req_id].append({
                "action_id": aid, "role": role, "action_text": info.get("action_text", ""),
                "relationship_type": ar.get("relationship_type", "NO_CONNECTION"),
                "influence": influence, "relationship_score": ar.get("relationship_score"),
                "explanation": ar.get("explanation", ""),
            })

    if step2c:
        for req_id in step1.get("requirements", {}):
            _merge_step2c_into_related(req_id, step1, step2c, action_lookup, req_to_related)

    result = {}
    for req_id, req in step1.get("requirements", {}).items():
        exclude_ids = subsequent_implicit_by_req.get(req_id, set())

        def _actions(ids, exclude_subsequent_implicit=False):
            out = []
            for aid in ids:
                nid = _norm_action_id(aid)
                if exclude_subsequent_implicit and nid in exclude_ids:
                    continue
                info = action_lookup.get(nid, {})
                out.append({"action_id": nid, "role": info.get("role", "OTHER"), "action_text": info.get("action_text", "")})
            return out

        seen = set()
        deduped = []
        for r in sorted(req_to_related.get(req_id, []), key=lambda x: (x.get("relationship_score") or 0), reverse=True):
            if r["action_id"] not in seen:
                seen.add(r["action_id"])
                deduped.append(r)
        result[req_id] = {
            "origin_actions": _actions(_req_origin_action_ids(req)),
            "contributing_actions": _actions(req.get("contributing_action_ids", []), exclude_subsequent_implicit=True),
            "implementation_actions": _actions(req.get("implementation_action_ids", []), exclude_subsequent_implicit=True),
            "revise_actions": _actions(req.get("revise_action_ids", []), exclude_subsequent_implicit=True),
            "related_actions": deduped,
        }

    classified_ids = set()
    for data in result.values():
        for x in data.get("origin_actions", []) + data.get("contributing_actions", []) + data.get("implementation_actions", []) + data.get("revise_actions", []) + data.get("related_actions", []):
            if x.get("action_id"):
                classified_ids.add(x["action_id"])
    others = [{"action_id": a.get("action_id"), "role": a.get("role", "OTHER"), "action_text": a.get("action_text", "")}
              for a in step05.get("all_actions", []) if a.get("action_id") and a["action_id"] not in classified_ids]
    result["others"] = others
    return result


# =============================================================================
# Convert and Save
# =============================================================================

def convert_and_save(run_dir: Path, output_dir: Path):
    """Load pipeline outputs from run_dir and write parsed-format files to output_dir."""
    run_dir, output_dir = Path(run_dir), Path(output_dir)

    step0 = load_json(run_dir / "step0_input.json")
    step1 = load_json(run_dir / "step1_output.json")
    req_contrib = load_json(run_dir / "contribution_analysis" / "requirement_contributions.json")
    step05 = load_json(run_dir / "step05_output.json") if (run_dir / "step05_output.json").exists() else None

    dialogue_id = step0.get("dialogue_id", "unknown")

    save_json(output_dir / "utterance_list.json", to_utterance_list(step0))
    save_jsonl(output_dir / "requirement_relations.jsonl", to_requirement_relations(step1))
    save_json(output_dir / "requirements_outputs_lists.json",
              to_requirements_outputs_lists(step1, dialogue_id, step05=step05))

    req_dep = to_requirement_output_dependency(step1, step0, dialogue_id)
    save_json(output_dir / "requirement_output_dependency.json", req_dep)

    save_json(output_dir / "requirement_contributions.json",
              to_requirement_contributions_parsed(req_contrib, step1))

    req_to_outcome = req_dep.get("requirement_to_outcome", {})
    save_json(output_dir / "output_contributions.json",
              output_contributions_from_requirements(req_contrib, req_to_outcome, step1))

    step3_path = run_dir / "step3_output.json"
    step05_path = run_dir / "step05_output.json"
    step2c_path = run_dir / "step2c_output.json"
    step2c_data = load_json(step2c_path) if step2c_path.exists() else None
    if step2c_data is not None:
        save_json(output_dir / "requirement_forward_labels.json", step2c_data)

    if step05_path.exists():
        step05_data = load_json(step05_path)
        if step3_path.exists():
            step3 = load_json(step3_path)
            # Normalize action_id in step3 (e.g. "[0-1]" -> "0-1") and add action_text so viewers see it
            action_lookup_05 = {a.get("action_id"): a for a in step05_data.get("all_actions", []) if a.get("action_id")}
            for pair in step3.get("pairs", []):
                for ar in pair.get("action_relationships", []):
                    aid = ar.get("action_id")
                    nid = _norm_action_id(aid or "")
                    ar["action_id"] = nid
                    if nid and nid in action_lookup_05:
                        ar["action_text"] = action_lookup_05[nid].get("action_text", "")
            save_json(step3_path, step3)
        else:
            step3 = {"pairs": []}
        save_json(output_dir / "requirement_action_map.json",
                  to_requirement_action_map(step1, step3, step05_data, step2c=step2c_data))
        save_json(output_dir / "action_utterance_map.json", to_action_utterance_map(step05_data))

    save_json(output_dir / "requirement_status.json",
              {"dialogue_id": dialogue_id, "requirements": to_requirement_status(step1)})

    step05b_path = run_dir / "step05b_output.json"
    intent_out_path = output_dir / "intent_outcome_map.json"
    if step05b_path.exists():
        save_json(intent_out_path, load_json(step05b_path))
    elif intent_out_path.exists():
        intent_out_path.unlink()

    print(f"Parsed outputs written to {output_dir}")


def convert_and_save_requirements_only(run_dir: Path, output_dir: Path):
    """Write only requirement-related parsed files (no contributions / step2 / step3)."""
    run_dir, output_dir = Path(run_dir), Path(output_dir)

    step0 = load_json(run_dir / "step0_input.json")
    step1 = load_json(run_dir / "step1_output.json")
    step05 = load_json(run_dir / "step05_output.json") if (run_dir / "step05_output.json").exists() else None

    dialogue_id = step0.get("dialogue_id", "unknown")

    save_json(output_dir / "utterance_list.json", to_utterance_list(step0))
    save_jsonl(output_dir / "requirement_relations.jsonl", to_requirement_relations(step1))
    save_json(
        output_dir / "requirements_outputs_lists.json",
        to_requirements_outputs_lists(step1, dialogue_id, step05=step05),
    )
    req_dep = to_requirement_output_dependency(step1, step0, dialogue_id)
    save_json(output_dir / "requirement_output_dependency.json", req_dep)
    save_json(
        output_dir / "requirement_status.json",
        {"dialogue_id": dialogue_id, "requirements": to_requirement_status(step1)},
    )
    if step05:
        save_json(output_dir / "action_utterance_map.json", to_action_utterance_map(step05))

    print(f"Requirement-only parsed outputs written to {output_dir}")


# =============================================================================
# Input normalization
# =============================================================================

def normalize_dialogue(data: dict) -> dict:
    def _text(u):
        return u.get("utterance", u.get("text", u.get("content", "")))

    if "turns" in data and data["turns"]:
        turns = []
        for i, t in enumerate(data["turns"]):
            nt = {
                "turn_id": t.get("turn_id", t.get("t", i)),
                "speaker": t.get("speaker", "user"),
                "text": _text(t),
            }
            if "meta" in t and t["meta"] is not None:
                nt["meta"] = t["meta"]
            turns.append(nt)
        out = {"dialogue_id": data.get("dialogue_id", "unknown"), "turns": turns}
        if data.get("metadata") is not None:
            out["metadata"] = data["metadata"]
        return out

    if "utterances" in data and data["utterances"]:
        turns = []
        for i, u in enumerate(data["utterances"]):
            nt = {
                "turn_id": u.get("turn_id", u.get("t", i)),
                "speaker": u.get("speaker", "user"),
                "text": _text(u),
            }
            if "meta" in u and u["meta"] is not None:
                nt["meta"] = u["meta"]
            turns.append(nt)
        out = {"dialogue_id": data.get("dialogue_id", "unknown"), "turns": turns}
        if data.get("metadata") is not None:
            out["metadata"] = data["metadata"]
        return out
    ev = data.get("event_log")
    if isinstance(ev, list) and ev:
        from utils.cogym_hybrid_turns import build_hybrid_dialogue_from_event_log

        did = data.get("dialogue_id") or "unknown"
        out = build_hybrid_dialogue_from_event_log(data, did)
        if data.get("task_performance") is not None:
            out["task_performance"] = data["task_performance"]
        return out

    return data


def count_turns(input_file: Path) -> int:
    """Return number of turns in the dialogue (after normalization)."""
    raw = load_json(input_file)
    normalized = normalize_dialogue(raw)
    return len(normalized.get("turns", []))


def run_pipeline(input_file: Path, run_dir: Path, model: str = "gpt-5.2",
                 turn_block_size: int = 4, provider: str = None,
                 step2b_req_batch_size: int = 1, stop_at: str = None,
                 action_model: str = None, action_provider: str = None,
                 with_intentions: bool = False):
    """Run full pipeline. stop_at: action_extraction (1a only), step1 (outcomes+requirements, no step2/3)."""
    run_dir.mkdir(parents=True, exist_ok=True)

    raw = load_json(input_file)
    normalized = normalize_dialogue(raw)
    normalized_path = run_dir / "input_dialogue.json"
    save_json(normalized_path, normalized)

    if stop_at == "action_extraction":
        from pipeline import run_test_up_to_action_extraction
        run_test_up_to_action_extraction(
            input_file=str(normalized_path), output_dir=str(run_dir),
            model=model, turn_block_size=turn_block_size, provider=provider,
            action_model=action_model, action_provider=action_provider,
        )
    elif stop_at == "step1":
        from pipeline import run_test_up_to_step1
        run_test_up_to_step1(
            input_file=str(normalized_path), output_dir=str(run_dir),
            model=model, turn_block_size=turn_block_size, provider=provider,
            action_model=action_model, action_provider=action_provider,
        )
    else:
        from pipeline import run_test
        run_test(
            input_file=str(normalized_path), output_dir=str(run_dir),
            model=model, turn_block_size=turn_block_size, provider=provider,
            step2b_req_batch_size=step2b_req_batch_size,
            action_model=action_model, action_provider=action_provider,
            with_intentions=with_intentions,
        )


# =============================================================================
# CLI
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="Run pipeline")
    ap.add_argument("--input_dir", metavar="DIR", help="Run on every dialogue JSON in DIR")
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="With --input_dir: discover *.json / *.jsonl under all subdirs. "
        "Output layout mirrors paths under --input_dir (e.g. lazy_sim/user_model/agent_model/topic/file.json → "
        "-o/out/user_model/agent_model/topic/file/).",
    )
    ap.add_argument("--output_dir", "-o", help="Output directory")
    ap.add_argument("--run_dir", help="Run directory (default: output_dir/run)")
    ap.add_argument("--model", "-m", default="gpt-5.2", help="Model name (default: gpt-5.2; steps after action extraction)")
    ap.add_argument("--provider", choices=("openai", "google", "openrouter", "gateway"), default=None)
    ap.add_argument("--turn_block_size", "-b", type=int, default=4, help="Turns per block")
    ap.add_argument("--step2b_req_batch_size", type=int, default=1,
                    help="Step 2b (Req–action influence labeling): requirements per LLM call; default 1 = one call per requirement")
    ap.add_argument(
        "--with-intentions",
        action="store_true",
        help="Run Step 1b intention extraction (1 LLM call). Off by default.",
    )
    ap.add_argument(
        "--stop_at",
        choices=("action_extraction", "step1"),
        default=None,
        help="action_extraction: Step 1a only (per-block actions). "
        "step1: outcomes + requirements (Step 1), then requirement-only parsed/; no Step 2/3.",
    )
    ap.add_argument("--max_turns", type=int, default=0,
                    help="With --input_dir: skip files with more than this many turns (0 = no limit)")
    ap.add_argument("--skip_run", action="store_true", help="Only convert existing run_dir")
    ap.add_argument("--batch", metavar="DIR", help="Convert all results under DIR")
    ap.add_argument("--contributions_only", action="store_true", help="Only recompute contributions")
    ap.add_argument("--action_model", default=None,
                    help="Model for Step 1a action extraction (default: gpt-5-mini)")
    ap.add_argument("--action_provider", choices=("openai", "google", "openrouter", "gateway"), default=None,
                    help="Provider for action extraction model")
    ap.add_argument(
        "--input_diag",
        help=(
            "Chat source: Chat URL (https://poe.com/s/CODE) OR local chat file/dir path. "
            "Directory must contain exactly one .json/.jsonl dialogue file or one of "
            "input_dialogue.json, dialogue.json, step0_input.json."
        ),
    )
    ap.add_argument(
        "--folder_name",
        help="Subfolder under --chat_output_root when using --input_diag without --output_dir.",
    )
    ap.add_argument(
        "--chat_output_root",
        default=str(_DEFAULT_CHAT_OUTPUT_ROOT),
        help=f"Base directory when --output_dir is omitted for --input_diag (default: {_DEFAULT_CHAT_OUTPUT_ROOT})",
    )
    args = ap.parse_args()

    if args.batch:
        batch_dir = Path(args.batch)
        for step1_path in sorted(batch_dir.rglob("step1_output.json")):
            rd = step1_path.parent
            od = rd.parent
            print(f"\nConverting: {rd}")
            try:
                convert_and_save(rd, od)
            except Exception as e:
                print(f"  Error: {e}")
        return

    prefetched_input_path: Optional[Path] = None
    if args.input_diag:
        if args.input_dir:
            ap.error("Use either --input_diag or --input_dir, not both.")
        try:
            default_folder = default_chat_folder_name(args.input_diag)
        except ValueError as e:
            ap.error(str(e))
        folder = sanitize_folder_name(args.folder_name or default_folder)
        if args.output_dir:
            output_dir = Path(args.output_dir)
        else:
            output_dir = Path(args.chat_output_root) / folder
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            prefetched_input_path, _ = prepare_chat_input(args.input_diag, output_dir)
        except (urllib.error.URLError, ValueError, json.JSONDecodeError, KeyError, OSError) as e:
            ap.error(f"Chat source parse failed: {e}")
    elif not args.output_dir:
        ap.error("--output_dir (-o) is required unless --batch or --input_diag")

    if not args.input_diag:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    if args.input_dir:
        input_dir = Path(args.input_dir).resolve()
        output_dir = output_dir.resolve()
        if args.recursive:
            files = sorted(
                set(input_dir.rglob("*.json")) | set(input_dir.rglob("*.jsonl"))
            )
        else:
            files = sorted(
                list(input_dir.glob("*.json")) + list(input_dir.glob("*.jsonl"))
            )
        print(f"Found {len(files)} input files in {input_dir}" + (" (recursive)" if args.recursive else ""))
        if args.max_turns > 0:
            print(f"Excluding files with > {args.max_turns} turns")
        stop_at = getattr(args, "stop_at", None)
        with_intentions = getattr(args, "with_intentions", False)
        for f in files:
            if args.max_turns > 0:
                try:
                    n = count_turns(f)
                    if n > args.max_turns:
                        print(f"Skipping {f.name} (too long: {n} turns)")
                        continue
                except Exception as e:
                    print(f"Skipping {f.name} (failed to count turns: {e})")
                    continue
            stem = f.stem
            if args.recursive:
                rel = f.resolve().relative_to(input_dir)
                od = output_dir / rel.with_suffix("")
            else:
                od = output_dir / stem
            rd = od / "run"
            print(f"\n{'='*60}\nProcessing: {f.name}\n{'='*60}")
            try:
                run_pipeline(f, rd, model=args.model, turn_block_size=args.turn_block_size,
                             provider=args.provider, step2b_req_batch_size=args.step2b_req_batch_size,
                             stop_at=stop_at,
                             action_model=args.action_model, action_provider=args.action_provider,
                             with_intentions=with_intentions)
                if stop_at == "step1":
                    convert_and_save_requirements_only(rd, od)
                elif not stop_at:
                    convert_and_save(rd, od)
            except Exception as e:
                print(f"  Error: {e}")
                import traceback
                traceback.print_exc()
        return

    run_dir = Path(args.run_dir) if args.run_dir else output_dir / "run"

    if args.contributions_only:
        from pipeline import run_step3_contributions
        import json as _json
        with open(run_dir / "step0_input.json") as f:
            dialogue = _json.load(f)
        with open(run_dir / "step1_output.json") as f:
            step1 = _json.load(f)
        with open(run_dir / "step3_output.json") as f:
            step3 = _json.load(f)
        with open(run_dir / "step05_output.json") as f:
            step05 = _json.load(f)
        run_step3_contributions(dialogue, step1, step3, step05, str(run_dir))
        convert_and_save(run_dir, output_dir)
        return

    if args.skip_run:
        convert_and_save(run_dir, output_dir)
        return

    if prefetched_input_path is not None:
        input_file = prefetched_input_path
    else:
        ap.error(
            "--input_diag is required for single-run mode (or use --input_dir/--batch/--contributions_only/--skip_run)."
        )
    if not input_file.exists():
        ap.error(f"Input file not found: {input_file}")

    stop_at = getattr(args, "stop_at", None)
    with_intentions = getattr(args, "with_intentions", False)
    run_pipeline(input_file, run_dir, model=args.model,
                 turn_block_size=args.turn_block_size, provider=args.provider,
                 step2b_req_batch_size=args.step2b_req_batch_size, stop_at=stop_at,
                 action_model=args.action_model, action_provider=args.action_provider,
                 with_intentions=with_intentions)
    if stop_at == "step1":
        convert_and_save_requirements_only(run_dir, output_dir)
    elif not stop_at:
        convert_and_save(run_dir, output_dir)


if __name__ == "__main__":
    main()
