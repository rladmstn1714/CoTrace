"""
LLM Prompts 
"""

# =============================================================================
# Step 1: Unified Block Extraction
# =============================================================================
# One LLM call per block → outcomes + actions + requirement ops

# -----------------------------------------------------------------------------
# Step 1a: Action extraction (per block — lightweight, no outcomes/requirements)
# -----------------------------------------------------------------------------

BLOCK_ACTION_EXTRACTION_PROMPT = """You are analyzing **one block of a longer dialogue** to extract actions.

**ACTION** = An atomic communicative act a speaker **directly performed** in a turn.
- Atomic action = minimal unit of action the speaker directly performed.
  - e.g. "User asks to find a paper about HCI in NLP conferences" → split into "User asks to find a paper about HCI", "User asks to find a paper in NLP conferences".
- Include evidence_quote from the utterance.

**Possible action types** (generate new types if needed):
Accept, Acknowledge, Address, Allow, Analyze, Apologize, Ask,
Challenge, Clarify, Classify, Compare, Complain, Confirm, Connect,
Constrain, Critique, Decide, Define, Delegate, Describe, Draft,
Emphasize, Evaluate, Explain, Feedback, Formalize, Frame, Greet,
Hypothesize, Implement, Include, Infer, Instruct, Invite, Justify,
List, Modify, Observe, Plan, Provide, Qualify, Recommend, Refuse,
Report, Request, State, Suggest, Summarize, Warn

**Role:** **SHAPER | EXECUTOR | OTHER**
  * **SHAPER:** Creates/shapes/revises the goal/requirement by proposing new ideas, new tasks, constraints, alternatives, or directions.
  * **EXECUTOR:** Executes/achieves the desired outcome/goal/requirement (e.g., drafting text, providing requested information, coding, searching, implementing).
  * **OTHER:** Neither SHAPER nor EXECUTOR.

=== DIALOGUE BLOCK START ===
{dialogue_block}

=== DIALOGUE BLOCK END ===

Now, extract ALL actions from EVERY turn. Return JSON only:

{{
  "actions": [
    {{
      "turn_id": <turn_number>,
      "action_type": "<action_type>",
      "action_text": "<brief description in third person>",
      "role": "<SHAPER|EXECUTOR|OTHER>",
      "evidence_quote": "<quote from utterance>"
    }}
  ]
}}

CRITICAL: Respond with ONLY the JSON object. No additional text.
"""

# Step 1a variant for CoGym **teammate message** blocks (batched: all SEND_TEAMMATE_MESSAGE
# turns in one LLM call). Tool steps are still extracted rule-based as `Calling Tool: ...` elsewhere;
# this prompt aligns wording with the standard Step 1a block format (see raw_log: `[Turn t] speaker: ...`).

BLOCK_ACTION_EXTRACTION_PROMPT_SHARECHAT = """You are analyzing **one block of a longer dialogue** to extract actions.

**Context (CoGym / shared workbench):** This block may include (1) natural-language **teammate messages** (inner `message=` of `SEND_TEAMMATE_MESSAGE`) and/or (2) the **`task_description` text only** from an environment `START(task_description=..., query=...)` turn—both appear **without** a `Calling Tool:` prefix as `[Turn t] speaker: <text>`.

**How the rest of the session is represented (for your interpretation only):** In the full dialogue and in the standard per-block extractor, non-chat steps are written exactly like this—same wording you should assume when the user refers to "the editor", "confirmation", "search", etc.:

  [Turn …] environment: Calling Tool: START(task_description=…, query=…)
  [Turn …] assistant: Calling Tool: INTERNET_SEARCH(query=…)
  [Turn …] assistant: Calling Tool: REQUEST_TEAMMATE_CONFIRM(request_id=editor_update, pending_action=EDITOR_UPDATE(text="…"))
  [Turn …] user: Calling Tool: ACCEPT_CONFIRMATION(request_id=editor_update)
  [Turn …] assistant: Calling Tool: EDITOR_UPDATE(text="…")
  [Turn …] user: Calling Tool: FINISH()

Do **not** output synthetic `Calling Tool:` strings as the user's utterance for turns in **this** block—extract **their actual communicative acts** (e.g. Complain, Request, Ask) from the natural language, with **evidence_quote** taken from that text. Use the patterns above only to understand what the user is talking about.

**ACTION** = An atomic communicative act a speaker **directly performed** in a turn.
- Atomic action = minimal unit of action the speaker directly performed.
  - e.g. "User asks to find a paper about HCI in NLP conferences" → split into "User asks to find a paper about HCI", "User asks to find a paper in NLP conferences".
- Include evidence_quote from the utterance.

**Possible action types** (generate new types if needed):
Accept, Acknowledge, Address, Allow, Analyze, Apologize, Ask,
Challenge, Clarify, Classify, Compare, Complain, Confirm, Connect,
Constrain, Critique, Decide, Define, Delegate, Describe, Draft,
Emphasize, Evaluate, Explain, Feedback, Formalize, Frame, Greet,
Hypothesize, Implement, Include, Infer, Instruct, Invite, Justify,
List, Modify, Observe, Plan, Provide, Qualify, Recommend, Refuse,
Report, Request, State, Suggest, Summarize, Warn

**Role:** **SHAPER | EXECUTOR | OTHER**
  * **SHAPER:** Creates/shapes/revises the goal/requirement by proposing new ideas, new tasks, constraints, alternatives, or directions.
  * **EXECUTOR:** Executes/achieves the desired outcome/goal/requirement (e.g., drafting text, providing requested information, coding, searching, implementing).
  * **OTHER:** Neither SHAPER nor EXECUTOR.

=== DIALOGUE BLOCK START ===
{dialogue_block}

=== DIALOGUE BLOCK END ===

Now, extract ALL actions from EVERY turn. Return JSON only:

{{
  "actions": [
    {{
      "turn_id": <turn_number>,
      "action_type": "<action_type>",
      "action_text": "<brief description in third person>",
      "role": "<SHAPER|EXECUTOR|OTHER>",
      "evidence_quote": "<quote from utterance>"
    }}
  ]
}}

CRITICAL: Respond with ONLY the JSON object. No additional text.
"""

# -----------------------------------------------------------------------------
# Step 1b: Global outcome extraction (single call)
# -----------------------------------------------------------------------------

GLOBAL_OUTCOME_EXTRACTION_PROMPT = """You are given ALL actions extracted from a complete dialogue. Your task:
(1) Identify **outcomes** (purpose-linked desired deliverables)
(2) Assign every action to exactly one outcome

Every collaboration has a **purpose**. **Outcomes are purpose-linked desired deliverables** — concrete outputs that participants are actually working toward.

- **Outcomes = purpose-linked desired deliverables:** Deliverables are not limited to text or documents — they can be a **decision**, **advice**, **plan**, **clarification**, or any other concrete result. Do **not** include things only mentioned without being adopted as a goal.
- **Requested or agreed outputs:** Any concrete output a participant **explicitly requests** or participants **agree to produce** is a deliverable.
- **Primary output:** The main thing the dialogue aims to produce must appear as an outcome. Sub-deliverables are children.
- **Phrasing:** e.g. "decision for X", "advice for X", "draft for X", "plan for X".
- **Hierarchy:** **parent** = abstract/general, **child** = specific/concrete. Use parent_outcome_id / child_outcome_ids.
- **Granularity:** Prefer consolidated, task-level outcomes. Example: "Workshop plan (date, venue, agenda)" NOT separate "Decide date", "Decide time", etc.
- **No duplicates.** Each distinct deliverable exactly once.

=== DIALOGUE CONTEXT ===
{dialogue_summary}

=== ALL ACTIONS ===
{actions_block}

Return JSON only:

{{
  "dialogue_summary": "<collaboration purpose, 1-2 sentences>",
  "outcomes": [
    {{
      "outcome_id": "outcome_1",
      "outcome": "<purpose-linked desired deliverable description>",
      "turn_id": <turn where this outcome first appears>, # first turn where the outcome is mentioned/shaped, not the turn where the outcome is exectued
      "parent_outcome_id": null,
      "child_outcome_ids": [],
      "related_outcome_ids": [],
      "confidence": 0.8
    }}
  ],
  "action_to_outcome": {{
    "<action_id>": "<outcome_id>"
  }}
}}

IMPORTANT: action_to_outcome must map EVERY action_id to an outcome_id. No action left unassigned.
CRITICAL: Respond with ONLY the JSON object. No additional text.
"""

# -----------------------------------------------------------------------------
# Step 1c: Requirement extraction (one call per outcome)
# -----------------------------------------------------------------------------

OUTCOME_REQUIREMENT_EXTRACTION_PROMPT = """You are given ONE outcome and the actions bound to it. Extract **requirements** (binding success conditions) for this outcome only.

=== OUTCOME ===
{outcome_id}: {outcome_description}

Do **not** use this outcome description to invent, extract, or add new requirements. It is **background only** for interpreting how the listed actions relate to the **TARGET REQUIREMENT** below.

=== ACTIONS FOR THIS OUTCOME ===
{actions_block}

**REQUIREMENT** = An atomic, externally verifiable SUCCESS CONDITION for an outcome.
- Binary testable (pass/fail).
- Must be explicitly stated or adopted as binding in the dialogue.

**NOT a requirement:** content itself, advice, implementation methods, internal reasoning, example outputs, one-off decisions.

Extract ONLY if ALL pass:
1) NECESSITY — framed as mandatory (must/need/required/cannot, numeric constraints, explicit include/exclude)
2) GROUNDING — directly stated, no inference
3) REPLACEABILITY — cannot be swapped without violating success
4) BINARY TESTABILITY — reviewer can judge pass/fail

Operations:
- **create**: New requirement. Check for revise BEFORE create.
- **revise**: Modify existing requirement (contradicts/tightens/relaxes/replaces). Use EXACT existing req_id in related_to (within this outcome).
- **delete**: Explicitly cancels a previously binding condition.

Edge cases:
- Advice itself is NOT a requirement. Only constraints ON the advice.
- "Try to / ideally / could / maybe" → not binding → not a requirement.

Return JSON only (bound_outcome_id must be "{outcome_id}"):

{{
  "requirement_ops": [
    {{
      "op": "create",
      "req_id": "req_1",
      "bound_outcome_id": "{outcome_id}",
      "fields": {{
        "text": "<requirement text>",
        "type": "<constraint|preference|ranking|task|other>"
      }},
      "creation_action_ids": ["<action_id>"],
      "contributing_action_ids": [],
      "implementation_action_ids": [],
      "related_to": [],
      "explicit_or_implicit": "<explicit|implicit>",
      "rationale": "<why extracted>"
    }}
  ]
}}

Use action_ids (e.g. "3-1") in creation_action_ids / contributing_action_ids / implementation_action_ids.
CRITICAL: Respond with ONLY the JSON object. No additional text.
"""

# Step 1c (batched): N outcomes in one call
OUTCOME_REQUIREMENT_EXTRACTION_BATCH_PROMPT = """You are given {n_outcomes} outcomes and the actions bound to each. For EACH outcome, extract **requirements** (binding success conditions) for that outcome only. Output one combined requirement_ops list; every op MUST have "bound_outcome_id" set to the outcome it belongs to.

{outcomes_blocks}


========================
INSTRUCTIONS

Do **not** use outcome descriptions to invent, extract, or add new requirements. It is **background only** for interpreting how the listed actions relate to the **TARGET REQUIREMENT** below.

**REQUIREMENT** = An atomic, externally verifiable SUCCESS CONDITION for an outcome.
- Binary testable (pass/fail).
- Must be explicitly stated or adopted as binding in the dialogue.

**NOT a requirement:** content itself, advice, implementation methods, internal reasoning, example outputs, one-off decisions.

Extract ONLY if ALL pass:
1) NECESSITY — framed as mandatory (must/need/required/cannot, numeric constraints, explicit include/exclude)
2) GROUNDING — directly stated, no inference
3) REPLACEABILITY — cannot be swapped without violating success
4) BINARY TESTABILITY — reviewer can judge pass/fail

Operations:
- **create**: New requirement. Check for revise BEFORE create.
- **revise**: Modify existing requirement (contradicts/tightens/relaxes/replaces). Use EXACT existing req_id in related_to (within this outcome).
- **delete**: Explicitly cancels a previously binding condition.

Edge cases:
- Advice itself is NOT a requirement. Only constraints ON the advice.
- "Try to / ideally / could / maybe" → not binding → not a requirement.

Return JSON only:

{{
  "requirement_ops": [
    {{
      "op": <create|revise|delete>,
      "req_id": "<req_id>", (e.g. "req_1", "req_2", "req_3")
      "bound_outcome_id": "<outcome_id>", (e.g. "outcome_1", "outcome_2", "outcome_3")
      "fields": {{
        "text": "<requirement text>",
        "type": "<constraint|preference|ranking|task|other>"
      }},
      "creation_action_ids": ["<action_id>"],
      "contributing_action_ids": [],
      "implementation_action_ids": [],
      "related_to": [],
      "explicit_or_implicit": "<explicit|implicit>",
      "rationale": "<why extracted>"
    }}
  ]
}}

Use action_ids (e.g. "3-1") in creation_action_ids / contributing_action_ids / implementation_action_ids.


CRITICAL: Respond with ONLY the JSON object. No additional text.
"""

# =============================================================================
# Step 1b: Intention Extraction (post-hoc grouping, lightweight)
# =============================================================================

INTENTION_EXTRACTION_PROMPT = """You are given a list of outcomes from a dialogue. (1) Identify distinct **intentions** (high-level goals or purposes) that these outcomes serve. (2) Assign each outcome to exactly one intention.

Output JSON only:
{
  "intentions": [
    {"intention_id": "I1", "intention": "short label"},
    {"intention_id": "I2", "intention": "another label"}
  ],
  "outcome_to_intention": [{"outcome_id": "outcome_1", "intention_id": "I1"}, ...]
}
- Every outcome_id appears exactly once in outcome_to_intention. Use intention_id from the intentions list."""

# =============================================================================
# Step 2b: Unified Action-Requirement Labeling (one call per requirement)
# =============================================================================
# For each requirement, label BOTH:
#   - Backward: candidate previous utterances (from embedding similarity)
#   - Forward: subsequent actions in the same outcome

REQUIREMENT_ACTION_LABELING_PROMPT = """You are analyzing how actions relate to a single requirement — both actions BEFORE and AFTER it was established.

=== OUTCOME (context) ===
{outcome_description}

=== TARGET REQUIREMENT ===
{req_id}: {req_text}
(Created at turn {req_origin_turn})

=== SECTION A: PRECEDING ACTIONS (before the requirement) ===
These are candidate utterances from BEFORE the requirement was established.
{preceding_block}

=== SECTION B: SUBSEQUENT ACTIONS (after the requirement, same outcome) ===
These actions occurred AFTER the requirement was created, within the same outcome.
{subsequent_block}

========================
TASK
========================

For EVERY entry in both sections, label the relationship to the requirement.

**relationship_type:**
- **DIRECT_CONNECTION**: Action explicitly operates on the requirement — creates, revises, tightens, relaxes, replaces, deletes, requests, evaluates, or fulfills it. The requirement is the OBJECT of the action.
- **IMPLICIT_CONNECTION**: Action provides context that influences, motivates, or triggers the requirement. Not directly about the requirement itself.
- **IMPLEMENTS**: (Section B only) Action directly executes, fulfills, or produces output satisfying this requirement.
- **CONTRIBUTES**: Action provides partial work, context, or progress toward this requirement.
- **NO_CONNECTION**: No meaningful relationship.

**relationship_score** (required for DIRECT/IMPLICIT/IMPLEMENTS/CONTRIBUTES):
- 1-3 for IMPLICIT_CONNECTION or CONTRIBUTES (1=weak, 2=medium, 3=strong)
- 4-5 for DIRECT_CONNECTION or IMPLEMENTS (4=explicit, 5=state mutation / full fulfillment)
- null for NO_CONNECTION

**explanation_type**:concise explanation for the relationship type, could be one of the following:
Feedback-Adopt | prior feedback, suggestion, or criticism is taken up and turned into a request

Option-Select | one option is chosen from alternatives offered earlier

Preference-Accumulate | repeated preferences build up and continue shaping the current request

Failure-Triggered-Requirement-Add | a new requirement is added after a prior attempt turns out unsatisfactory or misaligned

Preference-Realize | the user realizes a previously unstated preference after seeing an odd or unsatisfying output

Intent-Reveal | an implicit intention becomes explicit as a request

**contribution_role**: SHAPER | EXECUTOR | OTHER

Default to NO_CONNECTION unless clear semantic evidence.

========================
OUTPUT FORMAT
========================

Return JSON only:
{{
  "preceding_labels": [
    {{
      "index": 0,
      "action_id": "<e.g. 4-1>",
      "relationship_type": "DIRECT_CONNECTION",
      "relationship_score": 5,
      "explanation": "...",
      "contribution_role": "SHAPER"
    }}
  ],
  "subsequent_labels": [
    {{
      "index": 0,
      "action_id": "<e.g. 8-1>",
      "relationship_type": "IMPLEMENTS",
      "relationship_score": 5,
      "explanation": "...",
      "contribution_role": "EXECUTOR"
    }}
  ]
}}

Include one entry for EVERY index in both sections.
Provide ONLY the JSON object. No additional text.
"""

# Batched version: N requirements in one call. {requirements_blocks} is a concatenation
# of N blocks; output must be one JSON with "results": [{"req_id", "preceding_labels", "subsequent_labels"}, ...]
# in the same order as the blocks.
REQUIREMENT_ACTION_LABELING_BATCH_PROMPT = """You are analyzing how actions relate to MULTIPLE requirements — for each requirement block below, label both actions BEFORE and AFTER that requirement was established.

Each block includes an **OUTCOME (context)** line. Do **not** use outcome context to invent, extract, or add new requirements. Use it only as background; label relationships only for the **TARGET REQUIREMENT** in that same block.

{requirements_blocks}

========================
TASK
========================

For EVERY entry in both sections (SECTION A and SECTION B) of EACH requirement above, label the relationship to **that block's TARGET REQUIREMENT only**.

**relationship_type:**
- **DIRECT_CONNECTION**: Action explicitly operates on the requirement — creates, revises, tightens, relaxes, replaces, deletes, requests, evaluates, or fulfills it. The requirement is the OBJECT of the action.
- **IMPLICIT_CONNECTION**: Action provides context that influences, motivates, or triggers the requirement. Not directly about the requirement itself.
- **IMPLEMENTS**: (Section B only) Action directly executes, fulfills, or produces output satisfying this requirement.
- **CONTRIBUTES**: Action provides partial work, context, revision,or progress toward this requirement.
- **NO_CONNECTION**: No meaningful relationship.

**relationship_score** (required for DIRECT/IMPLICIT):
- 1-3 for IMPLICIT_CONNECTION (1=Weak, 2=Supportive, 3=Necessary)
  - score 3: Necessary: Without this action, the requirement would likely not be established in its current form. 
  - score 2: Supportive: This action meaningfully supports the establishment of the requirement, but the requirement could still be established without it.
  - score 1: Weak: This action has only a minor or background connection; the requirement would likely still be established in a similar form without it.

- 4-5 for DIRECT_CONNECTION(4=explicit, 5=state mutation / full fulfillment)
- null for NO_CONNECTION

**explanation_type**:concise explanation for the relationship type, could be one of the following:
Feedback-Adopt | prior feedback, suggestion, or criticism is taken up and turned into a request

Option-Select | one option is chosen from alternatives offered earlier

Preference-Accumulate | repeated preferences build up and continue shaping the current request

Failure-Triggered-Requirement-Add | a new requirement is added after a prior attempt turns out unsatisfactory or misaligned

Preference-Realize | the user realizes a previously unstated preference after seeing an odd or unsatisfying output

Intent-Reveal | an implicit intention becomes explicit as a request

Other | other explanation types not listed above, please generate your own.
**contribution_role**: SHAPER | EXECUTOR | OTHER

Default to NO_CONNECTION unless clear semantic evidence.

========================
OUTPUT FORMAT
========================

Return JSON only (same order as requirements above):
{{
  "results": [
    {{
      "req_id": "<first requirement's req_id>",
      "preceding_labels": [{{ "index": 0, "action_id": "...", "relationship_type": "...", "relationship_score": ... or null, "explanation": "...", "explanation_type": "...", "contribution_role": "..." }}, ...],
      "subsequent_labels": [{{ "index": 0, "action_id": "...", "relationship_type": "...", "relationship_score": ... or null, "explanation": "...", "explanation_type": "...", "contribution_role": "..." }}, ...]
    }},
    ...
  ]
}}

One object per requirement, in the same order as in the prompt. Include one label entry for every index in each section.
Provide ONLY the JSON object. No additional text.
"""

# =============================================================================
# Deliverable Extraction (kept from V3)
# =============================================================================

DELIVERABLE_EXTRACTION_PROMPT = """You are analyzing a dialogue to identify the final concrete deliverable produced for an outcome.

OUTCOME: {outcome_id}
Description: {outcome_description}

RELEVANT DIALOGUE TURNS:
{dialogue_turns}

TASK:
Determine whether a concrete, structured deliverable (e.g., code, written plan, itinerary, table, document, list, or other tangible artifact) was produced in these turns for the given outcome.

A deliverable IS a tangible output that can be evaluated against requirements. Examples:
- A block of code or script
- A final itinerary or schedule
- A written document, report, or plan with specific content
- A filled table or structured list

A deliverable is NOT:
- A general discussion or agreement to create something
- Vague conversational exchanges
- A description of what will be done in the future

If a deliverable exists, extract its COMPLETE verbatim text from the dialogue.
If multiple versions exist (e.g., revised code), take the FINAL/MOST RECENT version.

Respond ONLY with a JSON object (no additional text):
{{
  "has_deliverable": true or false,
  "deliverable_text": "<complete verbatim text of the deliverable, or null if none>",
  "deliverable_type": "code | plan | itinerary | document | list | other | null",
  "source_turn_ids": [<list of integer turn_ids where the deliverable appears>]
}}
"""


REQUIREMENT_DELIVERABLE_EVALUATION_PROMPT = """You are evaluating whether a set of requirements are reflected/satisfied in a final deliverable.

DELIVERABLE:
{deliverable_text}

REQUIREMENTS TO EVALUATE:
{requirements}

TASK:
For each requirement, determine whether it is explicitly reflected or satisfied in the deliverable above.

A requirement IS reflected if:
- The deliverable clearly addresses or fulfills the requirement's criteria
- Clear evidence of the requirement's satisfaction can be found in the deliverable text

A requirement is NOT reflected if:
- The deliverable ignores or contradicts the requirement
- What the requirement asks for is absent from the deliverable

Respond ONLY with a JSON object (no additional text):
{{
  "evaluations": [
    {{
      "req_id": "<req_id>",
      "is_reflected": true or false,
      "explanation": "<brief explanation citing specific evidence from the deliverable>"
    }}
  ]
}}

Include one entry for EVERY requirement listed above.
"""
