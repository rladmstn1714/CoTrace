"""
Utility functions for requirement-based dialogue analysis.
"""

import json
import re
from typing import Dict, List, Any, Optional
import numpy as np


def load_dialogue(filepath: str) -> Dict:
    """Load dialogue from JSON file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(data: Any, filepath: str, indent: int = 2) -> None:
    """Save data to JSON file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)


def load_embeddings(filepath: str) -> np.ndarray:
    """Load embeddings from numpy file."""
    return np.load(filepath)


def save_embeddings(embeddings: np.ndarray, filepath: str) -> None:
    """Save embeddings to numpy file."""
    np.save(filepath, embeddings)


def compute_cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """
    Compute cosine similarity between two vectors.
    
    Args:
        vec1: First vector
        vec2: Second vector
        
    Returns:
        Cosine similarity score (0-1)
    """
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
    
    return float(dot_product / (norm1 * norm2))


def norm_action_id(aid: str) -> str:
    """Normalize action_id: strip brackets so \"[0-1]\" and \"0-1\" match."""
    if not isinstance(aid, str) or not aid:
        return aid or ""
    return aid.strip().strip("[]").strip()


def turn_id_from_action_id(action_id: str, action_lookup: Optional[Dict[str, Dict]] = None) -> int:
    """Resolve turn_id from action lookup or action_id prefix (e.g. 19-7 → 19)."""
    aid = norm_action_id(action_id)
    if action_lookup and aid in action_lookup:
        return int(action_lookup[aid].get("turn_id", 0))
    if not aid:
        return 0
    head = aid.split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0


def is_preceding_turn_for_req(prev_turn_id: int, origin_turn_id: int) -> bool:
    """True when prev_turn_id is strictly before the requirement origin turn."""
    return prev_turn_id < origin_turn_id


def is_influence_action_after_req_origin(
    action_id: str, origin_turn_id: int, action_lookup: Optional[Dict[str, Dict]] = None,
) -> bool:
    """True when action occurs after the requirement was created (exclude from indirect/direct)."""
    return turn_id_from_action_id(action_id, action_lookup) > origin_turn_id


def format_turn_context(turns: List[Dict], start_idx: int = 0, end_idx: Optional[int] = None,
                        max_turns: int = 10) -> str:
    """
    Format dialogue turns for LLM context.
    
    Args:
        turns: List of turn dictionaries
        start_idx: Starting turn index
        end_idx: Ending turn index (exclusive)
        max_turns: Maximum number of turns to include
        
    Returns:
        Formatted string of dialogue turns
    """
    if end_idx is None:
        end_idx = len(turns)
    
    # Limit to max_turns
    if end_idx - start_idx > max_turns:
        start_idx = end_idx - max_turns
    
    lines = []
    for i in range(start_idx, end_idx):
        turn = turns[i]
        speaker = turn.get("speaker", "unknown")
        text = turn.get("text", "")
        lines.append(f"[Turn {i}] {speaker}: {text}")
    
    return "\n".join(lines)


def format_turn_context_for_indices(turns: List[Dict], indices: List[int]) -> str:
    """Format only the listed turn indices (order preserved), for one LLM block over non-contiguous turns."""
    lines = []
    for i in indices:
        if 0 <= i < len(turns):
            turn = turns[i]
            speaker = turn.get("speaker", "unknown")
            text = turn.get("text", "")
            lines.append(f"[Turn {i}] {speaker}: {text}")
    return "\n".join(lines)


def parse_llm_json_response(response_text: str) -> Dict:
    """
    Parse JSON from LLM response, handling markdown code blocks.
    
    Args:
        response_text: Raw LLM response text
        
    Returns:
        Parsed JSON dictionary
    """
    # Remove markdown code blocks if present
    if "```json" in response_text:
        match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
        if match:
            response_text = match.group(1)
    elif "```" in response_text:
        match = re.search(r"```\s*(.*?)\s*```", response_text, re.DOTALL)
        if match:
            response_text = match.group(1)
    
    # Clean up response
    response_text = response_text.strip()
    
    # Try to find JSON object
    if not response_text.startswith('{'):
        # Look for first { and last }
        start = response_text.find('{')
        end = response_text.rfind('}')
        if start != -1 and end != -1:
            response_text = response_text[start:end+1]
    
    # Try to parse JSON
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        # Print full response for debugging
        print(f"\n!!! JSON Parse Error !!!")
        print(f"Error: {e}")
        print(f"Full response ({len(response_text)} chars):")
        print(response_text[:1000])
        print("...")
        raise ValueError(f"Failed to parse JSON from LLM response: {e}\nResponse: {response_text[:500]}")


def extract_requirements_from_text(text: str) -> List[str]:
    """
    Extract requirement-like statements from text using simple heuristics.
    This is a fallback for when LLM is not available.
    
    Args:
        text: Input text
        
    Returns:
        List of extracted requirements
    """
    requirements = []
    
    # Look for numeric constraints
    numeric_patterns = [
        r"(\d+)\s+(papers?|articles?|results?)",
        r"(at least|at most|exactly|up to)\s+(\d+)",
    ]
    
    for pattern in numeric_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            for match in matches:
                requirements.append(f"Numeric constraint: {' '.join(match)}")
    
    # Look for "must" statements
    must_pattern = r"must\s+([^.!?]+)"
    matches = re.findall(must_pattern, text, re.IGNORECASE)
    for match in matches:
        requirements.append(f"Constraint: must {match.strip()}")
    
    # Look for "should" statements
    should_pattern = r"should\s+([^.!?]+)"
    matches = re.findall(should_pattern, text, re.IGNORECASE)
    for match in matches:
        requirements.append(f"Preference: should {match.strip()}")
    
    return requirements


def format_requirement_for_display(req: Dict) -> str:
    """
    Format a requirement dictionary for human-readable display.
    
    Args:
        req: Requirement dictionary
        
    Returns:
        Formatted string
    """
    status_emoji = {
        "active": "🔵",
        "satisfied": "✅",
        "failed": "❌",
        "obsolete": "⚫"
    }
    
    emoji = status_emoji.get(req.get("status", "active"), "❓")
    req_id = req.get("req_id", "unknown")
    text = req.get("text", "")
    req_type = req.get("type", "generic")
    
    return f"{emoji} [{req_id}] {text} (type: {req_type})"


def group_requirements_by_outcome(requirements: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group requirements by their outcome thread.
    
    Args:
        requirements: List of requirement dictionaries
        
    Returns:
        Dictionary mapping outcome descriptions to requirement lists
    """
    grouped = {}
    
    for req in requirements:
        # Assuming requirements have an 'outcome' field or we use thread_id
        key = req.get("outcome", f"Thread {req.get('thread_id', 'unknown')}")
        
        if key not in grouped:
            grouped[key] = []
        
        grouped[key].append(req)
    
    return grouped


def create_requirement_similarity_pairs(
    requirements: List[Dict],
    utterances: List[Dict],
    req_embeddings: np.ndarray,
    utterance_embeddings: np.ndarray,
    threshold: float = 0.5,
    top_k: int = 5
) -> List[Dict]:
    """
    Create requirement-utterance pairs based on embedding similarity.
    
    Args:
        requirements: List of requirement dictionaries with 'req_id'
        utterances: List of utterance/turn dictionaries with 'turn_id'
        req_embeddings: Embeddings for requirements (N x D)
        utterance_embeddings: Embeddings for utterances (M x D)
        threshold: Minimum similarity threshold
        top_k: Maximum number of similar utterances per requirement
        
    Returns:
        List of similarity pair dictionaries
    """
    pairs = []
    
    for req_idx, req in enumerate(requirements):
        req_emb = req_embeddings[req_idx]
        
        # Compute similarities with all utterances
        similarities = []
        for utt_idx, utt in enumerate(utterances):
            utt_emb = utterance_embeddings[utt_idx]
            sim = compute_cosine_similarity(req_emb, utt_emb)
            
            if sim >= threshold:
                similarities.append({
                    "utterance_idx": utt_idx,
                    "similarity": sim
                })
        
        # Sort by similarity and take top_k
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        
        for sim_info in similarities[:top_k]:
            utt_idx = sim_info["utterance_idx"]
            utt = utterances[utt_idx]
            
            pairs.append({
                "req_id": req["req_id"],
                "req_text": req["text"],
                "req_thread_id": req.get("thread_id"),
                "req_origin_turn": req.get("origin_turn_id"),
                "turn_id": utt.get("turn_id", utt_idx),
                "turn_text": utt.get("text", ""),
                "turn_speaker": utt.get("speaker", "unknown"),
                "similarity": sim_info["similarity"]
            })
    
    return pairs


def filter_pairs_by_temporal_constraint(
    pairs: List[Dict],
    allow_future: bool = False
) -> List[Dict]:
    """
    Filter requirement-utterance pairs based on temporal constraints.
    
    By default, only keep pairs where the utterance comes before or at the
    same time as the requirement origin.
    
    Args:
        pairs: List of similarity pair dictionaries
        allow_future: If True, allow utterances from after requirement creation
        
    Returns:
        Filtered list of pairs
    """
    filtered = []
    
    for pair in pairs:
        req_origin = pair.get("req_origin_turn", 0)
        turn_id = pair.get("turn_id", 0)
        
        if allow_future or turn_id <= req_origin:
            filtered.append(pair)
    
    return filtered


def build_requirement_lineage_tree(requirements: List[Dict]) -> Dict[str, List[str]]:
    """
    Build a tree of requirement lineage based on parent-child relationships.
    
    Args:
        requirements: List of requirement dictionaries with 'req_id' and 'parents'
        
    Returns:
        Dictionary mapping req_id to list of child req_ids
    """
    tree = {}
    
    # Initialize all requirements in tree
    for req in requirements:
        req_id = req["req_id"]
        tree[req_id] = []
    
    # Build parent-child relationships
    for req in requirements:
        req_id = req["req_id"]
        parents = req.get("parents", [])
        
        for parent_id in parents:
            if parent_id in tree:
                tree[parent_id].append(req_id)
    
    return tree


def get_requirement_ancestors(req_id: str, requirements: List[Dict]) -> List[str]:
    """
    Get all ancestor requirement IDs for a given requirement.
    
    Args:
        req_id: Target requirement ID
        requirements: List of all requirements
        
    Returns:
        List of ancestor req_ids (from immediate parent to root)
    """
    req_dict = {r["req_id"]: r for r in requirements}
    
    if req_id not in req_dict:
        return []
    
    ancestors = []
    visited = set()
    queue = list(req_dict[req_id].get("parents", []))
    
    while queue:
        parent_id = queue.pop(0)
        
        if parent_id in visited:
            continue
        
        visited.add(parent_id)
        ancestors.append(parent_id)
        
        if parent_id in req_dict:
            queue.extend(req_dict[parent_id].get("parents", []))
    
    return ancestors

