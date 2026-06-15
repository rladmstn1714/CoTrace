"""
Utility functions for loading dialogues from various formats.
"""

import json
from typing import Dict, List, Optional

def _coerce_cogym_event_log_to_turns(data: Dict) -> Dict:
    """CoGym ``_converted_event_log.json``: if there are no usable turns but ``event_log`` exists, build hybrid turns (same as run_pipeline.normalize_dialogue)."""
    turns = data.get("turns")
    if isinstance(turns, list) and len(turns) > 0:
        return data
    ev = data.get("event_log")
    if not isinstance(ev, list) or not ev:
        return data
    from utils.cogym_hybrid_turns import build_hybrid_dialogue_from_event_log

    did = data.get("dialogue_id") or "unknown"
    out = build_hybrid_dialogue_from_event_log(data, did)
    if data.get("task_performance") is not None:
        out["task_performance"] = data["task_performance"]
    return out


def convert_wildchat_to_dialogue(wildchat_item: Dict) -> Dict:
    """
    Convert wildchat conversation format to dialogue format.
    
    Args:
        wildchat_item: Item from wildchat dataset with 'id' and 'conversation' fields
        
    Returns:
        Dialogue dict with turns format
    """
    dialogue_id = wildchat_item.get('id', 'unknown')
    conversation = wildchat_item.get('conversation', [])
    
    turns = []
    turn_id = 0
    
    for msg in conversation:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        
        # Map role: user -> user, assistant -> assistant
        speaker = 'user' if role == 'user' else 'assistant'
        
        turns.append({
            "turn_id": turn_id,
            "speaker": speaker,
            "text": content,
            "meta": {}
        })
        turn_id += 1
    
    return {
        "dialogue_id": dialogue_id,
        "metadata": {"source": "wildchat"},
        "turns": turns
    }


def load_dialogues(dialogue_file: str) -> List[Dict]:
    """
    Load dialogues from JSON or JSONL file.
    Converts action_log format to turns format if needed.
    
    Args:
        dialogue_file: Path to input file
        
    Returns:
        List of dialogue objects with standardized format
    """
    if dialogue_file.endswith('.jsonl'):
        # JSONL format: each line is a separate JSON object
        dialogues = []
        with open(dialogue_file, 'r', encoding='utf-8') as f:
            for line_idx, line in enumerate(f):
                line = line.strip()
                if line:
                    data = json.loads(line)
                    dialogue = convert_action_log_to_dialogue(data, f"dialogue_{line_idx}")
                    dialogues.append(_coerce_cogym_event_log_to_turns(dialogue))
        return dialogues
    else:
        # JSON format: could be array or single object
        with open(dialogue_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, list):
            dialogues = []
            for i, d in enumerate(data):
                if "action_log" in d:
                    dialogues.append(convert_action_log_to_dialogue(d, f"dialogue_{i}"))
                elif "conversation" in d and "id" in d:
                    # Wildchat format
                    dialogues.append(convert_wildchat_to_dialogue(d))
                elif "turns" in d:
                    dialogues.append(d)
                else:
                    dialogues.append(d)
            return [_coerce_cogym_event_log_to_turns(d) for d in dialogues]
        else:
            if "action_log" in data:
                out = convert_action_log_to_dialogue(data, data.get("dialogue_id", "dialogue_0"))
                return [_coerce_cogym_event_log_to_turns(out)]
            elif "conversation" in data and "id" in data:
                # Wildchat format
                return [_coerce_cogym_event_log_to_turns(convert_wildchat_to_dialogue(data))]
            elif "turns" in data or "utterances" in data:
                return [_coerce_cogym_event_log_to_turns(data)]
            return [_coerce_cogym_event_log_to_turns(data)]
def convert_action_log_to_dialogue(data: Dict, dialogue_id: str = None) -> Dict:
    """
    Convert action_log format to turns format.
    
    Args:
        data: Data with action_log or turns
        dialogue_id: Dialogue ID
        
    Returns:
        Dialogue dict with turns format
    """
    if "turns" in data:
        # Already in turns format
        return data
    
    if "action_log" not in data:
        # Unknown format
        return data
    
    # Convert action_log to turns
    turns = []
    turn_id = 0
    
    for action in data.get("action_log", []):
        if action.get("type") == "message":
            message = action.get("message", {})
            if message.get("type") == "utterance":
                player_id = action.get("player", 0)
                speaker = "user" if player_id == 0 else "assistant"
                
                turns.append({
                    "turn_id": turn_id,
                    "speaker": speaker,
                    "text": message.get("data", ""),
                    "meta": {}
                })
                turn_id += 1
    
    return {
        "dialogue_id": dialogue_id or "dialogue_0",
        "metadata": data.get("metadata", {"source": "dialogue"}),
        "turns": turns
    }

