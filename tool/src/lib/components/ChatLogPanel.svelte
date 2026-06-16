<script lang="ts">
	import { marked } from 'marked';
	import { tick } from 'svelte';
	import { boldSecondWord } from '$lib/utils';
	import {
		isAssistantSpeakerId,
		utteranceAvatarInitial,
		utteranceDisplayName
	} from '$lib/speakerUtils';
	import {
		getTurnIdFromActionId,
		getOriginTurnFromEntry,
		getRequirementEntryById as findRequirementEntryById,
		filteredImplementationActions,
		collectActionIds,
		collectActionIdsWithDirect,
		extractActionVerb,
		groupedAnnotationLabel,
		groupedReasonLabel,
		normalizeRole,
		roleEmoji,
		roleRankForMerge,
		type TurnActionAnnotation,
		type GroupedTurnActionAnnotation
	} from '$lib/chatLogUtils';
	import { isEunsuSpeakerName } from '$lib/contribPeerBar';
	import type { RequirementActionMap, UtteranceListData, ActionUtteranceMap, OutcomeActionItem } from '$lib/data/dataLoader';

	marked.setOptions({ gfm: true, breaks: true });

	function renderMarkdown(text: string): string {
		if (!text || typeof text !== 'string') return '';
		return marked.parse(text) as string;
	}


	let {
		requirementId = null,
		primaryRequirementVersionIds = [],
		secondaryRequirementVersionIds = [],
		requirementCreationTurn = null,
		focusedTurnId = null,
		allRequirementIds = [],
		childGoalStarts = [],
		utteranceList = null,
		requirementActionMap = {},
		actionUtteranceMap = {},
		selectedActionId = null,
		hoveredActionId = null,
		hoveredPanelTurnId = null,
		onActionHover,
		onActionClick,
		outcomeTurnIdWhenNoReqs = null,
		outcomeActionsFromMap = null,
		hideActions = false
	}: {
		requirementId: string | null;
		/** Requirement version ids shown with full action highlight. */
		primaryRequirementVersionIds?: string[];
		/** Other chain versions shown toned down when non-empty. */
		secondaryRequirementVersionIds?: string[];
		requirementCreationTurn?: number | null;
		focusedTurnId?: number | null;
		allRequirementIds?: string[];
		childGoalStarts?: Array<{ goalId: string; goalTitle: string; turn: number }>;
		utteranceList: UtteranceListData | null;
		requirementActionMap: RequirementActionMap;
		actionUtteranceMap: ActionUtteranceMap;
		selectedActionId?: string | null;
		hoveredActionId?: string | null;
		/** Requirements panel row hover — highlight this turn like action hover. */
		hoveredPanelTurnId?: number | null;
		onActionHover?: (actionId: string | null) => void;
		onActionClick?: (actionId: string | null) => void;
		/** When outcome has no requirements, include actions with turn_id <= this (fallback when no outcome_action_map) */
		outcomeTurnIdWhenNoReqs?: number | null;
		/** When outcome has no requirements and outcome_action_map.json exists, use these actions (preferred) */
		outcomeActionsFromMap?: OutcomeActionItem[] | null;
		/** Hide action icons/annotations (intent-level related-only mode). */
		hideActions?: boolean;
	} = $props();

	function getRequirementEntryById(reqId: string | null | undefined) {
		return findRequirementEntryById(requirementActionMap, reqId);
	}

	const primaryReqIds = $derived.by((): string[] => {
		if (primaryRequirementVersionIds.length > 0) return primaryRequirementVersionIds;
		if (requirementId != null) return [requirementId];
		return [];
	});

	const secondaryReqIds = $derived(secondaryRequirementVersionIds ?? []);

	const activeReqIds = $derived.by((): string[] => {
		if (primaryReqIds.length > 0 || secondaryReqIds.length > 0) {
			return [...primaryReqIds, ...secondaryReqIds];
		}
		if (requirementId != null) return [requirementId];
		return allRequirementIds;
	});

	function collectTurnsForReqIds(
		reqIds: string[],
		collect: (actionId: string) => void
	) {
		for (const reqId of reqIds) {
			const entry = getRequirementEntryById(reqId);
			if (entry) collectActionIds(entry as unknown as Record<string, unknown>, null, collect);
		}
	}

	const combinedOutcomeActions = $derived.by((): OutcomeActionItem[] | null => {
		const base = outcomeActionsFromMap ?? [];
		return base.length > 0 ? base : null;
	});

	// Default = gray. Related utterances: user = blue, assistant = green. Indirect = same colors + hatch. Selected/hovered = blue border.
	const selectedTurnIdFromAction = $derived.by(() => {
		if (!selectedActionId) return null;
		const fromMap = actionUtteranceMap[selectedActionId];
		if (fromMap) return fromMap.turn_id;
		const fromOutcome = combinedOutcomeActions?.find((a) => a.action_id === selectedActionId);
		return fromOutcome?.turn_id ?? null;
	});
	/** When user clicks any chat (including ones with no action), show that turn as selected */
	let selectedTurnIdLocal = $state<number | null>(null);
	const selectedTurnId = $derived(selectedTurnIdFromAction ?? selectedTurnIdLocal);
	$effect(() => {
		if (selectedTurnIdFromAction != null) selectedTurnIdLocal = null;
	});
	// When requirement is selected, select the "Created here" turn so chat scrolls to it (existing scroll effect handles scroll)
	$effect(() => {
		if (requirementId == null || requirementCreationTurn == null) return;
		selectedTurnIdLocal = requirementCreationTurn;
	});
	$effect(() => {
		if (focusedTurnId == null) return;
		selectedTurnIdLocal = focusedTurnId;
	});
	const hoveredTurnId = $derived.by(() => {
		if (!hoveredActionId) return null;
		const fromMap = actionUtteranceMap[hoveredActionId];
		if (fromMap) return fromMap.turn_id;
		const fromOutcome = combinedOutcomeActions?.find((a) => a.action_id === hoveredActionId);
		return fromOutcome?.turn_id ?? null;
	});

	/** turn_id -> first action_id for this requirement (for hover sync from chat to action/timeline) */
	const turnToActionId = $derived.by((): Map<number, string> => {
		const map = new Map<number, string>();
		const setTurn = (actionId: string) => {
			const ev = actionUtteranceMap[actionId];
			if (ev != null && !map.has(ev.turn_id)) map.set(ev.turn_id, actionId);
		};
		if (primaryReqIds.length > 0) {
			collectTurnsForReqIds(primaryReqIds, setTurn);
		} else if (activeReqIds.length > 0) {
			collectTurnsForReqIds(activeReqIds, setTurn);
		} else if (selectedActionId) {
			setTurn(selectedActionId);
		} else if (allRequirementIds.length > 0) {
			for (const reqId of allRequirementIds) {
				const entry = getRequirementEntryById(reqId);
				if (entry) collectActionIds(entry as unknown as Record<string, unknown>, null, setTurn);
			}
		} else if (allRequirementIds.length === 0 && combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) {
				if (a.turn_id != null && !map.has(a.turn_id)) map.set(a.turn_id, a.action_id);
			}
		} else if (allRequirementIds.length === 0 && outcomeTurnIdWhenNoReqs != null) {
			for (const entry of Object.values(requirementActionMap)) {
				if (entry) collectActionIds(entry as unknown as Record<string, unknown>, outcomeTurnIdWhenNoReqs, setTurn);
			}
		}
		if (combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) {
				if (a.turn_id != null && !map.has(a.turn_id)) map.set(a.turn_id, a.action_id);
			}
		}
		return map;
	});

	const primaryRelatedTurnIds = $derived.by((): Set<number> => {
		const set = new Set<number>();
		if (primaryReqIds.length === 0) return set;
		collectRelatedTurnsForReqIds(primaryReqIds, set);
		return set;
	});

	const secondaryRelatedTurnIds = $derived.by((): Set<number> => {
		const set = new Set<number>();
		if (secondaryReqIds.length === 0) return set;
		collectRelatedTurnsForReqIds(secondaryReqIds, set);
		for (const turn of primaryRelatedTurnIds) set.delete(turn);
		return set;
	});

	const relatedTurnIds = $derived.by((): Set<number> => {
		if (primaryReqIds.length > 0 || secondaryReqIds.length > 0) {
			const set = new Set<number>(primaryRelatedTurnIds);
			for (const turn of secondaryRelatedTurnIds) set.add(turn);
			return set;
		}
		const set = new Set<number>();
		const validTurns = utteranceList?.utterances?.map((u) => u.turn_id) ?? [];
		const validTurnSet = new Set(validTurns);
		const addTurnWithFallback = (turn: number) => {
			if (validTurnSet.has(turn)) {
				set.add(turn);
				return;
			}
			if (validTurnSet.has(turn - 1)) {
				set.add(turn - 1);
				return;
			}
			if (validTurnSet.has(turn + 1)) {
				set.add(turn + 1);
				return;
			}
			if (validTurns.length === 0) return;
			let nearest = validTurns[0];
			let bestDist = Math.abs(nearest - turn);
			for (const t of validTurns) {
				const d = Math.abs(t - turn);
				if (d < bestDist) {
					bestDist = d;
					nearest = t;
				}
			}
			set.add(nearest);
		};
		const collect = (actionId: string) => {
			const ev = actionUtteranceMap[actionId];
			if (ev != null) addTurnWithFallback(ev.turn_id);
		};
		if (allRequirementIds.length > 0) {
			collectTurnsForReqIds(allRequirementIds, collect);
		} else if (allRequirementIds.length === 0 && combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) {
				if (a.turn_id != null) addTurnWithFallback(a.turn_id);
			}
		} else if (allRequirementIds.length === 0 && outcomeTurnIdWhenNoReqs != null) {
			for (const entry of Object.values(requirementActionMap)) {
				if (entry) collectActionIds(entry as unknown as Record<string, unknown>, outcomeTurnIdWhenNoReqs, collect);
			}
		} else if (selectedActionId) {
			collect(selectedActionId);
		}
		if (combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) {
				if (a.turn_id != null) addTurnWithFallback(a.turn_id);
			}
		}
		for (const child of childGoalStarts) {
			if (typeof child?.turn === 'number') addTurnWithFallback(child.turn);
		}
		return set;
	});

	function collectRelatedTurnsForReqIds(reqIds: string[], set: Set<number>) {
		const validTurns = utteranceList?.utterances?.map((u) => u.turn_id) ?? [];
		const validTurnSet = new Set(validTurns);
		const addTurnWithFallback = (turn: number) => {
			if (validTurnSet.has(turn)) {
				set.add(turn);
				return;
			}
			if (validTurnSet.has(turn - 1)) {
				set.add(turn - 1);
				return;
			}
			if (validTurnSet.has(turn + 1)) {
				set.add(turn + 1);
				return;
			}
			if (validTurns.length === 0) return;
			let nearest = validTurns[0];
			let bestDist = Math.abs(nearest - turn);
			for (const t of validTurns) {
				const d = Math.abs(t - turn);
				if (d < bestDist) {
					bestDist = d;
					nearest = t;
				}
			}
			set.add(nearest);
		};
		const collect = (actionId: string) => {
			const ev = actionUtteranceMap[actionId];
			if (ev != null) addTurnWithFallback(ev.turn_id);
		};
		collectTurnsForReqIds(reqIds, collect);
		if (reqIds.length > 0 && utteranceList?.utterances?.length) {
			const validTurns = new Set(utteranceList.utterances.map((u) => u.turn_id));
			for (const reqId of reqIds) {
				const entry = getRequirementEntryById(reqId) as unknown as { related_actions?: { action_id: string }[] };
				const _relOriginTurn2 = getOriginTurnFromEntry(entry as unknown as Record<string, unknown>);
				for (const r of entry?.related_actions ?? []) {
					if (!r?.action_id) continue;
					if (_relOriginTurn2 != null && (r as Record<string, unknown>).influence === 'indirect' && getTurnIdFromActionId(r.action_id) > _relOriginTurn2) continue;
					if (actionUtteranceMap[r.action_id] != null) continue;
					const tid = getTurnIdFromActionId(r.action_id);
					if (validTurns.has(tid)) addTurnWithFallback(tid);
				}
			}
		}
	}

	/** Indices of related utterances (for scroll strip). Position 0 = first message, 1 = second, etc. */
	const relatedIndices = $derived.by((): number[] => {
		if (!utteranceList?.utterances?.length) return [];
		const indices: number[] = [];
		utteranceList.utterances.forEach((u, i) => {
			if (relatedTurnIds.has(u.turn_id)) indices.push(i);
		});
		return indices;
	});
	const totalUtterances = $derived(utteranceList?.utterances?.length ?? 0);

	// turn_id -> evidence_quotes[] for emphasizing in utterance text
	const evidenceByTurn = $derived.by((): Map<number, string[]> => {
		const map = new Map<number, string[]>();
		for (const entry of Object.values(actionUtteranceMap)) {
			if (entry == null || typeof entry !== 'object' || !('turn_id' in entry) || !('evidence_quote' in entry)) continue;
			const turnId = (entry as { turn_id: number }).turn_id;
			const quote = (entry as { evidence_quote: string }).evidence_quote;
			if (typeof quote !== 'string' || !quote) continue;
			if (!map.has(turnId)) map.set(turnId, []);
			const list = map.get(turnId)!;
			if (!list.includes(quote)) list.push(quote);
		}
		return map;
	});

	function applyEvidenceHighlights(utterance: string, turnId: number): string {
		const quotes = evidenceByTurn.get(turnId);
		if (!quotes?.length) return utterance;
		// Sort by length descending so longer matches are replaced first
		const sorted = [...quotes].sort((a, b) => b.length - a.length);
		let text = utterance;
		for (const q of sorted) {
			if (text.includes(q)) text = text.replaceAll(q, `**${q}**`);
		}
		return text;
	}

	type StripMarker = { turnIndex: number; turnId: number; speaker: string; actionId: string; role: 'SHAPER' | 'EXECUTOR'; direct: boolean };

	/** Per-turn list of actions with relation type and evidence for the annotation popover */

	const annotationByTurn = $derived.by((): Map<number, TurnActionAnnotation[]> => {
		const map = new Map<number, TurnActionAnnotation[]>();
		const add = (turnId: number, item: TurnActionAnnotation) => {
			if (!map.has(turnId)) map.set(turnId, []);
			const list = map.get(turnId)!;
			if (!list.some((x) => x.actionId === item.actionId)) list.push(item);
		};
		// Prefer primary requirement ids, then secondary (toned down in the UI).
		const reqIds =
			primaryReqIds.length > 0 || secondaryReqIds.length > 0
				? [...primaryReqIds, ...secondaryReqIds]
				: activeReqIds;
		const resolveReqIds =
			primaryReqIds.length > 0 || secondaryReqIds.length > 0
				? [...primaryReqIds, ...secondaryReqIds]
				: reqIds;
		for (const [actionId, ev] of Object.entries(actionUtteranceMap)) {
			if (!ev || typeof ev.turn_id !== 'number') continue;
			const turnId = ev.turn_id;
			let relationType = 'Action';
			let direct = true;
			let action_text: string | undefined;
			let reason: string | undefined;
			let role: string | undefined;
			let matched = false;
			for (const reqId of resolveReqIds) {
				const entry = getRequirementEntryById(reqId) as unknown as {
					origin_actions?: { action_id: string; action_text?: string; role?: string }[];
					contributing_actions?: { action_id: string; action_text?: string; role?: string }[];
					implementation_actions?: { action_id: string; action_text?: string; role?: string }[];
					related_actions?: { action_id: string; action_text?: string; reason?: string; explanation?: string }[];
				};
				if (!entry) continue;
				const fromList = (
					list: { action_id: string; action_text?: string; role?: string }[] | undefined,
					type: string,
					d: boolean
				) => {
					const found = list?.find((a) => a.action_id === actionId);
					if (found) {
						relationType = type;
						direct = d;
						action_text = found.action_text;
						role = found.role;
						return true;
					}
					return false;
				};
				// Prefer implementation when an action appears in multiple buckets.
				if (
					fromList(
						filteredImplementationActions<{ action_id: string; action_text?: string; role?: string }>(
							entry as unknown as Record<string, unknown>
						),
						'Implementation',
						true
					)
				) {
					matched = true;
					break;
				}
				if (fromList(entry.origin_actions, 'Origin', true)) {
					matched = true;
					break;
				}
				const related = entry.related_actions ?? [];
				const rel = related.find((r) => r && typeof r === 'object' && (r as { action_id: string }).action_id === actionId) as
					| { action_id: string; action_text?: string; reason?: string; explanation?: string }
					| undefined;
				if (fromList(entry.contributing_actions, 'Contributing', true)) {
					if (rel) reason = rel.explanation ?? rel.reason;
					matched = true;
					break;
				}
				if (rel) {
					const _relOriginTurn = getOriginTurnFromEntry(entry as unknown as Record<string, unknown>);
					if ((rel as Record<string, unknown>).influence === 'indirect' && _relOriginTurn != null && turnId > _relOriginTurn) break;
					relationType = 'Related';
					direct = false;
					const r = rel as Record<string, unknown>;
					action_text = (rel.action_text ?? r.action_text ?? r.actionText) as string | undefined;
					reason = rel.explanation ?? rel.reason;
					role = (rel as { role?: string }).role;
					matched = true;
					break;
				}
			}
			// Goal-level actions (e.g. user SHAPER on outcome_1) often live only in outcome_action_map, not in a requirement bucket.
			if (relationType === 'Action' && combinedOutcomeActions != null) {
				const oam = combinedOutcomeActions.find((x) => x.action_id === actionId);
				if (oam) {
					relationType = 'Outcome';
					direct = true;
					action_text = oam.action_text;
					role = oam.role;
				}
			}
			// Only add if this action belongs to the selected requirement(s)
			if (reqIds.length > 0 && relationType !== 'Action') {
				add(turnId, {
					actionId,
					relationType,
					direct,
					evidence_quote: (ev as { evidence_quote?: string }).evidence_quote,
					action_text,
					reason,
					role,
					actionVerb: extractActionVerb(action_text)
				});
			}
		}
		// Include annotations for related_actions not in actionUtteranceMap (so Chat Log still shows them when data is present)
		if (reqIds.length > 0 && utteranceList?.utterances?.length) {
			const turnIdsFromUtterances = new Set(utteranceList.utterances.map((u) => u.turn_id));
			for (const reqId of reqIds) {
				const entry = getRequirementEntryById(reqId) as unknown as {
					related_actions?: { action_id: string; action_text?: string; explanation?: string; reason?: string; role?: string }[];
				};
				const related = entry?.related_actions ?? [];
				const _annOriginTurn = getOriginTurnFromEntry(entry as unknown as Record<string, unknown>);
				for (const r of related) {
					if (!r?.action_id) continue;
					const aid = r.action_id;
					const _aidTurn = getTurnIdFromActionId(aid);
					if (_annOriginTurn != null && (r as Record<string, unknown>).influence === 'indirect' && _aidTurn > _annOriginTurn) continue;
					if (actionUtteranceMap[aid] != null) continue;
					const tid = _aidTurn;
					if (!turnIdsFromUtterances.has(tid)) continue;
					const rr = r as Record<string, unknown>;
					add(tid, {
						actionId: aid,
						relationType: 'Related',
						direct: false,
						action_text: (r.action_text ?? rr.action_text ?? rr.actionText) as string | undefined,
						reason: r.explanation ?? r.reason,
						role: r.role,
						actionVerb: extractActionVerb((r.action_text ?? rr.action_text ?? rr.actionText) as string | undefined)
					});
				}
			}
		}
		// Outcome-level actions from outcome_action_map when there are no requirements (full list),
		// or when there are requirements, only actions missing from the utterance loop above (no duplicate actionIds).
		if (combinedOutcomeActions) {
			if (allRequirementIds.length === 0) {
				for (const a of combinedOutcomeActions) {
					if (a.turn_id == null) continue;
					add(a.turn_id, {
						actionId: a.action_id,
						relationType: 'Outcome',
						direct: true,
						evidence_quote: a.evidence_quote,
						action_text: a.action_text,
						role: a.role,
						actionVerb: extractActionVerb(a.action_text)
					});
				}
			} else {
				const seenIds = new Set<string>();
				for (const list of map.values()) {
					for (const x of list) seenIds.add(x.actionId);
				}
				for (const a of combinedOutcomeActions) {
					if (a.turn_id == null || seenIds.has(a.action_id)) continue;
					add(a.turn_id, {
						actionId: a.action_id,
						relationType: 'Outcome',
						direct: true,
						evidence_quote: a.evidence_quote,
						action_text: a.action_text,
						role: a.role,
						actionVerb: extractActionVerb(a.action_text)
					});
				}
			}
		}
		return map;
	});

	const groupedAnnotationsByTurn = $derived.by((): Map<number, GroupedTurnActionAnnotation[]> => {
		const grouped = new Map<number, GroupedTurnActionAnnotation[]>();
		for (const [turnId, annotations] of annotationByTurn) {
			const groups: GroupedTurnActionAnnotation[] = [];
			for (const ann of annotations) {
				const prev = groups[groups.length - 1];
				if (prev && prev.actionVerb && ann.actionVerb && prev.actionVerb === ann.actionVerb) {
					prev.items.push(ann);
					if (prev.role == null) prev.role = ann.role;
					else if ((prev.role ?? '') !== (ann.role ?? '')) prev.role = undefined;
					if (prev.relationType !== ann.relationType) prev.relationType = 'Mixed';
				} else {
					groups.push({
						relationType: ann.relationType,
						direct: ann.direct,
						role: ann.role,
						actionVerb: ann.actionVerb,
						items: [ann]
					});
				}
			}
			grouped.set(turnId, groups);
		}
		return grouped;
	});


	const speakerByTurn = $derived.by(() => {
		const map = new Map<number, string>();
		if (!utteranceList?.utterances) return map;
		for (const u of utteranceList.utterances) map.set(u.turn_id, u.speaker);
		return map;
	});

	const peerNames = $derived.by((): { a: string; b: string } | null => {
		const speakers = new Set<string>();
		for (const u of utteranceList?.utterances ?? []) {
			if (u.speaker && u.speaker !== 'user' && u.speaker !== 'assistant' && !isEunsuSpeakerName(u.speaker)) {
				speakers.add(u.speaker);
			}
		}
		if (speakers.size < 2) return null;
		const sorted = [...speakers].sort();
		return { a: sorted[0], b: sorted[1] };
	});

	const PEER_A_COLOR = '#7c3aed';
	const PEER_B_COLOR = '#ea580c';
	const DEFAULT_USER_COLOR = '#3b82f6';
	const DEFAULT_ASST_COLOR = '#16a34a';

	function speakerColor(speaker: string): string {
		if (!peerNames) return isAssistantSpeakerId(speaker) ? DEFAULT_ASST_COLOR : DEFAULT_USER_COLOR;
		if (speaker === peerNames.a) return PEER_A_COLOR;
		if (speaker === peerNames.b) return PEER_B_COLOR;
		return '#94a3b8';
	}

	function speakerSide(speaker: string): 'a' | 'b' | 'other' {
		if (!peerNames) return isAssistantSpeakerId(speaker) ? 'b' : 'a';
		if (speaker === peerNames.a) return 'a';
		if (speaker === peerNames.b) return 'b';
		return 'other';
	}

	const turnIndexMap = $derived.by(() => {
		const map = new Map<number, number>();
		if (!utteranceList?.utterances) return map;
		utteranceList.utterances.forEach((u, i) => map.set(u.turn_id, i));
		return map;
	});

	const actionRoleByActionId = $derived.by((): Map<string, 'SHAPER' | 'EXECUTOR'> => {
		const map = new Map<string, 'SHAPER' | 'EXECUTOR'>();
		if (allRequirementIds.length > 0) {
			for (const reqId of allRequirementIds) {
				const entry = getRequirementEntryById(reqId) as unknown as { origin_actions?: { action_id: string; role?: string }[]; contributing_actions?: { action_id: string; role?: string }[]; implementation_actions?: { action_id: string; role?: string }[] } | undefined;
				if (!entry) continue;
				const add = (list: { action_id: string; role?: string }[] | undefined) => {
					if (!Array.isArray(list)) return;
					for (const x of list) map.set(x.action_id, normalizeRole(x.role));
				};
				add(entry.origin_actions);
				add(entry.contributing_actions);
				add(
					filteredImplementationActions<{ action_id: string; role?: string }>(
						entry as unknown as Record<string, unknown>
					)
				);
			}
		}
		// outcome_action_map wins over requirement buckets for the same action_id (e.g. user SHAPER in OAM only).
		if (combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) {
				const id = a.action_id;
				const fromOam = normalizeRole(a.role);
				const prev = map.get(id);
				if (prev == null) {
					map.set(id, fromOam);
				} else {
					map.set(id, roleRankForMerge(fromOam) > roleRankForMerge(prev) ? fromOam : prev);
				}
			}
		}
		return map;
	});

	const actionDirectByActionId = $derived.by((): Map<string, boolean> => {
		const map = new Map<string, boolean>();
		if (combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) map.set(a.action_id, true);
		}
		if (allRequirementIds.length > 0) {
			for (const reqId of allRequirementIds) {
				const entry = getRequirementEntryById(reqId) as unknown as Record<string, unknown>;
				if (entry) collectActionIdsWithDirect(entry, null, (actionId, direct) => map.set(actionId, direct));
			}
		} else if (outcomeTurnIdWhenNoReqs != null) {
			for (const entry of Object.values(requirementActionMap)) {
				if (entry) collectActionIdsWithDirect(entry as unknown as Record<string, unknown>, outcomeTurnIdWhenNoReqs, (actionId, direct) => map.set(actionId, direct));
			}
		}
		return map;
	});

	const allStripMarkers = $derived.by((): StripMarker[] => {
		const seen = new Set<number>();
		const markers: StripMarker[] = [];
		const roleByActionId = actionRoleByActionId;
		const directByActionId = actionDirectByActionId;
		const process = (actionId: string, direct: boolean) => {
			const ev = actionUtteranceMap[actionId];
			if (!ev) return;
			const turnId = ev.turn_id;
			if (seen.has(turnId)) return;
			seen.add(turnId);
			const idx = turnIndexMap.get(turnId);
			if (idx == null) return;
			markers.push({
				turnIndex: idx,
				turnId,
				speaker: speakerByTurn.get(turnId) ?? 'user',
				actionId,
				role: roleByActionId.get(actionId) ?? 'EXECUTOR',
				direct
			});
		};
		const processTurnId = (turnId: number, actionId: string, role?: string) => {
			if (seen.has(turnId)) return;
			seen.add(turnId);
			const idx = turnIndexMap.get(turnId);
			if (idx == null) return;
			markers.push({
				turnIndex: idx,
				turnId,
				speaker: speakerByTurn.get(turnId) ?? 'user',
				actionId,
				role: role ? normalizeRole(role) : (roleByActionId.get(actionId) ?? 'EXECUTOR'),
				direct: directByActionId.get(actionId) ?? true
			});
		};
		if (allRequirementIds.length > 0) {
			for (const reqId of allRequirementIds) {
				const entry = getRequirementEntryById(reqId) as unknown as Record<string, unknown>;
				if (entry) collectActionIdsWithDirect(entry, null, process);
			}
		}
		if (combinedOutcomeActions != null) {
			for (const a of combinedOutcomeActions) {
				if (a.turn_id != null) processTurnId(a.turn_id, a.action_id, a.role);
			}
		} else if (allRequirementIds.length === 0 && outcomeTurnIdWhenNoReqs != null) {
			for (const entry of Object.values(requirementActionMap)) {
				const ent = entry as unknown as Record<string, unknown>;
				if (ent) collectActionIdsWithDirect(ent, outcomeTurnIdWhenNoReqs, process);
			}
		}
		markers.sort((a, b) => a.turnIndex - b.turnIndex);
		return markers;
	});
	function scrollToTurn(turnId: number) {
		if (!chatListEl) return;
		const row = chatListEl.querySelector(`[data-turn-id="${turnId}"]`);
		if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	/** When only outcome is selected (no requirement), do not show direct/indirect — just outcome-related actions. */
	const showDirectIndirect = $derived(requirementId != null);

	/** Turn IDs that have at least one direct action (for hatched bubble when only indirect). */
	const turnIdsWithDirectAction = $derived.by((): Set<number> => {
		const set = new Set<number>();
		if (!showDirectIndirect) return set;
		for (const [turnId, annotations] of annotationByTurn) {
			if (annotations.some((a) => a.direct)) set.add(turnId);
		}
		return set;
	});

	// View mode: show all utterances or only related to outcome/requirement/actions
	let viewMode = $state<'all' | 'related'>('related');
	const hasRelatedContext = $derived(
		allRequirementIds.length > 0 ||
		combinedOutcomeActions != null ||
		outcomeTurnIdWhenNoReqs != null ||
		childGoalStarts.length > 0
	);
	const displayedUtterances = $derived.by(() => {
		const list = utteranceList?.utterances ?? [];
		if (viewMode === 'all') return list;
		if (!hasRelatedContext) return list;
		return list.filter((u) => relatedTurnIds.has(u.turn_id));
	});
	// Expanded utterance turn_ids (show full text beyond 3 lines)
	let expandedTurns = $state<Set<number>>(new Set());
	let expandedGroupedAnnotationKeys = $state<Set<string>>(new Set());

	function groupedAnnotationKey(turnId: number, index: number): string {
		return `${turnId}:${index}`;
	}

	function toggleGroupedAnnotation(turnId: number, index: number) {
		const key = groupedAnnotationKey(turnId, index);
		const next = new Set(expandedGroupedAnnotationKeys);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		expandedGroupedAnnotationKeys = next;
	}

	function annotationItemLabel(item: TurnActionAnnotation): string {
		return item.action_text?.trim() || item.reason?.trim() || item.actionId;
	}

	let chatListEl = $state<HTMLDivElement | null>(null);
	// When selection changes, scroll chat so the selected message is visible
	$effect(() => {
		const turn = selectedTurnId;
		const list = chatListEl;
		if (turn == null || !list) return;
		tick().then(() => {
			const row = list.querySelector(`[data-turn-id="${turn}"]`);
			if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
		});
	});

	let overflowingTurns = $state<Set<number>>(new Set());

	function checkOverflowAction(el: HTMLDivElement, params: { turnId: number }) {
		const check = () => {
			const isOverflow = el.scrollHeight > el.clientHeight + 2;
			if (isOverflow && !overflowingTurns.has(params.turnId)) {
				overflowingTurns = new Set([...overflowingTurns, params.turnId]);
			}
		};
		tick().then(check);
		return { update(newParams: { turnId: number }) { params = newParams; tick().then(check); } };
	}

	function toggleExpand(turnId: number) {
		expandedTurns = new Set(expandedTurns);
		if (expandedTurns.has(turnId)) {
			expandedTurns.delete(turnId);
		} else {
			expandedTurns = new Set([turnId]);
			selectedTurnIdLocal = turnId;
		}
	}

	function getActionBadge(turnId: number): { label: string; kind: 'executed' | 'created' | 'contributed' | 'related' } | null {
		if (requirementId == null) return null;
		const anns = annotationByTurn.get(turnId);
		if (!anns || anns.length === 0) return null;
		if (anns.some(a => a.relationType === 'Implementation')) return { label: 'executed', kind: 'executed' };
		if (anns.some(a => a.relationType === 'Origin')) return { label: 'added requirement', kind: 'created' };
		if (anns.some(a => a.relationType === 'Contributing')) return { label: 'contributed', kind: 'contributed' };
		if (anns.some(a => a.relationType === 'Outcome')) return { label: 'executed', kind: 'executed' };
		if (anns.some(a => a.relationType === 'Related')) return { label: 'related', kind: 'related' };
		return null;
	}
</script>

<div class="panel">
	<div class="panel-title-row">
		<h3 class="panel-title">Chat Log</h3>
		{#if hasRelatedContext && utteranceList?.utterances?.length}
			<div class="chat-view-toggle" role="group" aria-label="Chat view mode">
				<button
					type="button"
					class="toggle-link"
					class:active={viewMode === 'all'}
					onclick={() => (viewMode = 'all')}
				>View all</button>
				<span class="toggle-divider">|</span>
				<button
					type="button"
					class="toggle-link"
					class:active={viewMode === 'related'}
					disabled={relatedTurnIds.size === 0}
					onclick={() => (viewMode = 'related')}
				>View related{#if relatedTurnIds.size > 0} ({relatedTurnIds.size}){/if}</button>
			</div>
		{/if}
	</div>
	{#if !utteranceList?.utterances?.length}
		<p class="hint">No chat data available.</p>
	{:else}
		<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
		<div
			class="chat-list"
			bind:this={chatListEl}
			onclick={(e) => {
				const item = (e.target as HTMLElement).closest('[data-turn-id]');
				if (!item) {
					selectedTurnIdLocal = null;
					onActionClick?.(null);
					return;
				}
				const clickedTurnId = item.getAttribute('data-turn-id');
				if (clickedTurnId != null) {
					const turnId = parseInt(clickedTurnId, 10);
					selectedTurnIdLocal = turnId;
					const actionId = turnToActionId.get(turnId) ?? null;
					onActionClick?.(actionId);
				}
			}}
			onkeydown={(e) => { if (e.key === 'Escape') onActionClick?.(null); }}
		>
			{#each displayedUtterances as u, i}
			{@const isPrimaryRelated = primaryRelatedTurnIds.has(u.turn_id)}
			{@const isSecondaryRelated = secondaryRelatedTurnIds.has(u.turn_id)}
			{@const isRelated = isPrimaryRelated || isSecondaryRelated}
			{@const isIndirectOnly = isPrimaryRelated && showDirectIndirect && !turnIdsWithDirectAction.has(u.turn_id)}
			{@const side = speakerSide(u.speaker)}
			{@const sColor = speakerColor(u.speaker)}
			{@const isSelected = selectedTurnId !== null && u.turn_id === selectedTurnId}
			{@const isHovered =
				(hoveredTurnId !== null && u.turn_id === hoveredTurnId) ||
				(hoveredPanelTurnId !== null && u.turn_id === hoveredPanelTurnId)}
			{@const isExpanded = expandedTurns.has(u.turn_id)}
			{@const actionIdForTurn = hideActions ? null : (turnToActionId.get(u.turn_id) ?? null)}
			{@const canExpand = overflowingTurns.has(u.turn_id) || isExpanded}
			{@const actionBadge = hideActions ? null : getActionBadge(u.turn_id)}
			<div
				class="chat-item"
				role="button"
				tabindex="0"
				aria-label="Chat message"
				data-turn-id={u.turn_id}
				data-speaker={u.speaker}
				data-selected={isSelected || isExpanded}
				data-hovered={isHovered}
				onmouseenter={() => onActionHover?.(actionIdForTurn ?? null)}
				onmouseleave={() => onActionHover?.(null)}
				onclick={() => {
					if (isExpanded) {
						toggleExpand(u.turn_id);
					} else {
						selectedTurnIdLocal = u.turn_id;
						onActionClick?.(actionIdForTurn ?? null);
					}
				}}
				onkeydown={(e) => {
					if (e.key === 'Enter' || e.key === ' ') {
						if (isExpanded) {
							toggleExpand(u.turn_id);
						} else {
							selectedTurnIdLocal = u.turn_id;
							onActionClick?.(actionIdForTurn ?? null);
						}
					}
				}}
			>
				<div class="msg-speaker-row">
					<span
						class="speaker-avatar"
						style="background:{speakerColor(u.speaker)}"
						title={u.speaker}
					>
						{utteranceAvatarInitial(u.speaker)}
					</span>
					<span class="speaker-name" style="color:{speakerColor(u.speaker)}">{utteranceDisplayName(u.speaker)}</span>
					{#if actionBadge}
						<span class="action-badge {actionBadge.kind}">{actionBadge.label}</span>
					{/if}
				</div>
				<div
					class="bubble"
					class:side-a={side === 'a' && isPrimaryRelated}
					class:side-b={side === 'b' && isPrimaryRelated}
					class:secondary-related={isSecondaryRelated}
					class:unrelated={!isRelated}
					class:indirect={isIndirectOnly}
					style="--sc:{sColor}"
				>
					<div
						class="utterance-content"
						class:expanded={isExpanded}
						class:expandable={canExpand}
					>
						<div class="utterance-markdown" use:checkOverflowAction={{ turnId: u.turn_id }}>{@html renderMarkdown(applyEvidenceHighlights(u.utterance, u.turn_id))}</div>
						{#if canExpand}
							<button
								type="button"
								class="expand-hint"
								onclick={(e) => { e.stopPropagation(); toggleExpand(u.turn_id); }}
								onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleExpand(u.turn_id); } }}
							>{isExpanded ? '▲ Collapse' : '▼ Expand'}</button>
						{/if}
					</div>
					{#if requirementId != null && !hideActions && (annotationByTurn.get(u.turn_id)?.length ?? 0) > 0}
						{@const groupedAnnotations = groupedAnnotationsByTurn.get(u.turn_id) ?? []}
						<div class="annotation-summary-row">
							<div class="annotation-inline annotation-summary">
								{#each groupedAnnotations as group, groupIndex}
									{@const displayText = groupedAnnotationLabel(group)}
									{@const isExpandedGroup = expandedGroupedAnnotationKeys.has(groupedAnnotationKey(u.turn_id, groupIndex))}
									{#if displayText}
										{@const role = normalizeRole(group.role)}
										<div class="annotation-group-wrap">
											<button
												type="button"
												class="annotation-item-inline annotation-item-button"
												class:direct={showDirectIndirect ? group.direct : true}
												class:indirect={showDirectIndirect && !group.direct}
												class:shaper={role === 'SHAPER'}
												class:executor={role === 'EXECUTOR'}
												class:expandable={group.items.length > 1}
												aria-expanded={group.items.length > 1 ? isExpandedGroup : undefined}
												onclick={(e) => {
													e.stopPropagation();
													if (group.items.length > 1) toggleGroupedAnnotation(u.turn_id, groupIndex);
												}}
											>
												<span class="annotation-role-emoji" title={role} aria-hidden="true">{roleEmoji(role)}</span>
												<span class="annotation-type-badge">{group.relationType}{#if group.items.length > 1} {group.items.length}{/if}</span>
												<div class="annotation-action-text">{@html boldSecondWord(displayText)}</div>
												{#if group.items.length > 1}
													<span class="annotation-expand-indicator" aria-hidden="true">{isExpandedGroup ? '▲' : '▼'}</span>
												{/if}
											</button>
											{#if isExpandedGroup}
												<div class="annotation-group-expanded">
													{#each group.items as item}
														<div class="annotation-group-expanded-item">{annotationItemLabel(item)}</div>
													{/each}
												</div>
											{/if}
										</div>
									{/if}
								{/each}
							</div>
						</div>
					{/if}
				</div>
			</div>
			{#if viewMode === 'related' && i < displayedUtterances.length - 1}
				{@const nextU = displayedUtterances[i + 1]}
				{@const gap = (nextU?.turn_id ?? u.turn_id) - u.turn_id - 1}
				{#if gap > 0}
					<div
						class="omitted-between"
						role="separator"
						aria-label="{gap} message{gap === 1 ? '' : 's'} not shown (not related to selection)"
						title="{gap} message(s) hidden in View related"
					>
						<span class="omitted-line" aria-hidden="true"></span>
						<span class="omitted-label">{gap} Omitted</span>
						<span class="omitted-line" aria-hidden="true"></span>
					</div>
				{/if}
			{/if}
			{/each}
		</div>
	{/if}
</div>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: 0;
		background: #ffffff;
		border: 1px solid #e5e7eb;
		border-radius: 18px;
		min-height: 0;
		overflow: hidden;
		box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
	}
	.panel-title-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		flex-shrink: 0;
		padding: 0.9rem 1rem;
		border-bottom: 1px solid #e5e7eb;
		background: #f8fafc;
	}
	.panel-title {
		margin: 0;
		font-size: 0.72rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: #64748b;
		flex-shrink: 0;
	}
	.hint {
		margin: 0;
		color: #9ca3af;
		font-size: 0.82rem;
		padding: 1rem;
	}
	.chat-view-toggle {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		flex-shrink: 0;
	}
	.toggle-link {
		background: none;
		border: none;
		cursor: pointer;
		font-size: 0.72rem;
		font-weight: 500;
		color: #94a3b8;
		padding: 0;
		transition: color 0.15s;
	}
	.toggle-link:hover:not(:disabled) {
		color: #475569;
	}
	.toggle-link.active {
		color: #111827;
		font-weight: 600;
	}
	.toggle-link:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
	.toggle-divider {
		font-size: 0.72rem;
		color: #d1d5db;
	}
	.chat-list {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		overflow-y: auto;
		min-height: 0;
		flex: 1 1 0;
		min-width: 0;
		padding: 0.9rem 1rem 1rem;
		scrollbar-width: thin;
		scrollbar-color: #d1d5db transparent;
	}
	.omitted-between {
		display: flex;
		align-items: center;
		gap: 0.65rem;
		width: 100%;
		padding: 0.45rem 0;
		flex-shrink: 0;
	}
	.omitted-line {
		flex: 1 1 0;
		height: 1px;
		background: linear-gradient(90deg, transparent, #cbd5e1 15%, #cbd5e1 85%, transparent);
		min-width: 0.75rem;
	}
	.omitted-label {
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.04em;
		color: #64748b;
		text-transform: none;
		white-space: nowrap;
		padding: 0.2rem 0.55rem;
		border-radius: 999px;
		background: #f1f5f9;
		border: 1px solid #e2e8f0;
	}
	.chat-item {
		min-width: 0;
		min-height: 0;
		flex-shrink: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		width: 100%;
		transition: opacity 0.15s;
	}
	.msg-speaker-row {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		padding-left: 2px;
	}
	.speaker-avatar {
		width: 26px;
		height: 26px;
		border-radius: 50%;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		font-size: 0.58rem;
		font-weight: 800;
		flex-shrink: 0;
		color: #fff;
		letter-spacing: 0.02em;
	}
	.speaker-name {
		font-size: 0.78rem;
		font-weight: 600;
	}
	.action-badge {
		font-size: 0.62rem;
		font-weight: 700;
		padding: 0.12rem 0.45rem;
		border-radius: 4px;
	}
	.action-badge.executed {
		background: #dff4e9;
		color: #2f7a57;
		border: 1px solid #9dd2b7;
	}
	.action-badge.created {
		background: #dbeafe;
		color: #1e40af;
		border: 1px solid #93c5fd;
	}
	.action-badge.contributed {
		background: #efe6fb;
		color: #6c52a2;
		border: 1px solid #cdbce9;
	}
	.action-badge.related {
		background: #f8fafc;
		color: #475569;
		border: 1px solid #dbe4ee;
	}
	.chat-item[data-selected='true'] .bubble {
		border-width: 2px;
		border-color: var(--sc, #3b82f6);
		box-shadow: 0 0 0 1px color-mix(in srgb, var(--sc, #3b82f6) 14%, transparent);
	}
	.chat-item[data-hovered='true'] .bubble {
		border-width: 2px;
		border-color: var(--sc, #3b82f6);
		box-shadow: 0 6px 18px color-mix(in srgb, var(--sc, #3b82f6) 12%, transparent);
	}
	.bubble {
		padding: 0.75rem 0.95rem;
		border-radius: 16px;
		box-sizing: border-box;
		transition: border-color 0.15s, box-shadow 0.15s;
	}
	.bubble.unrelated {
		background: #f8fafc;
		border: 1px solid #e5e7eb;
		color: #6b7280;
	}
	.bubble.unrelated .utterance-markdown,
	.bubble.unrelated .utterance-markdown :global(*) {
		color: #6b7280;
	}
	.bubble.side-a,
	.bubble.side-b {
		border: 1px solid color-mix(in srgb, var(--sc) 35%, #e5e7eb);
		background: color-mix(in srgb, var(--sc) 8%, #ffffff);
	}
	.bubble.side-a.indirect,
	.bubble.side-b.indirect {
		background:
			repeating-linear-gradient(
				-45deg,
				color-mix(in srgb, var(--sc) 18%, transparent) 0px,
				color-mix(in srgb, var(--sc) 18%, transparent) 4px,
				transparent 4px,
				transparent 8px
			),
			color-mix(in srgb, var(--sc) 8%, #ffffff);
	}
	.bubble.secondary-related {
		border: 1px dashed color-mix(in srgb, var(--sc) 22%, #cbd5e1);
		background: color-mix(in srgb, var(--sc) 4%, #f8fafc);
		opacity: 0.72;
	}
	.bubble.secondary-related .utterance-markdown,
	.bubble.secondary-related .utterance-markdown :global(*) {
		color: #94a3b8;
	}
	.bubble.indirect .utterance-markdown,
	.bubble.indirect .utterance-markdown :global(*) {
		color: #374151;
	}
	.chat-item[data-selected='true'] .bubble.unrelated {
		opacity: 1;
	}
	.utterance-content {
		cursor: default;
		outline: none;
	}
	.utterance-content.expandable {
		cursor: pointer;
	}
	.utterance-content:not(.expanded) .utterance-markdown {
		display: -webkit-box;
		-webkit-line-clamp: 4;
		line-clamp: 4;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.expand-hint {
		display: block;
		font-size: 0.62rem;
		font-weight: 500;
		color: #9ca3af;
		margin-top: 0.35em;
		transition: color 0.15s;
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		font: inherit;
		text-align: left;
	}
	.utterance-content:hover .expand-hint {
		color: #6b7280;
	}
	.utterance-markdown {
		min-width: 0;
		font-size: 0.82rem;
		line-height: 1.55;
		color: #3f3427;
		word-break: break-word;
		overflow-wrap: break-word;
		padding: 0;
	}
	.utterance-markdown :global(p) {
		margin: 0 0 0.5em 0;
	}
	.utterance-markdown :global(p:first-child) {
		margin-top: 0;
	}
	.utterance-markdown :global(p:last-child) {
		margin-bottom: 0;
	}
	.utterance-markdown :global(h1),
	.utterance-markdown :global(h2),
	.utterance-markdown :global(h3) {
		margin: 0.75em 0 0.35em 0;
		font-size: 1em;
		font-weight: 700;
	}
	.utterance-markdown :global(h1:first-child),
	.utterance-markdown :global(h2:first-child),
	.utterance-markdown :global(h3:first-child) {
		margin-top: 0;
	}
	.utterance-markdown :global(ul),
	.utterance-markdown :global(ol) {
		margin: 0.35em 0;
		padding-left: 1.25em;
	}
	.utterance-markdown :global(li) {
		margin: 0.15em 0;
	}
	.utterance-markdown :global(a) {
		color: #1976d2;
		text-decoration: underline;
	}
	.utterance-markdown :global(strong) {
		font-weight: 700;
	}
	.utterance-markdown :global(code) {
		background: rgba(0, 0, 0, 0.06);
		padding: 0.1em 0.3em;
		border-radius: 4px;
		font-size: 0.9em;
	}
	.annotation-summary-row {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.5rem;
		margin-top: 0.5rem;
		padding-top: 0.5rem;
		border-top: 1px solid rgba(94, 72, 44, 0.08);
	}
	.annotation-inline {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		font-size: 0.72rem;
	}
	.annotation-summary-row .annotation-inline.annotation-summary {
		flex: 1 1 0;
		min-width: 0;
	}
	.annotation-item-inline {
		display: flex;
		align-items: flex-start;
		gap: 0.35rem;
		padding: 0.3rem 0.45rem;
		border-radius: 6px;
		border-left: 3px solid #d5b384;
		background: rgba(255, 250, 241, 0.92);
		min-width: 0;
	}
	.annotation-item-button {
		width: 100%;
		text-align: left;
		font: inherit;
		cursor: default;
	}
	.annotation-item-button.expandable {
		cursor: pointer;
	}
	.annotation-item-inline.direct {
		border-left-color: #3c9b7d;
		background: #eef8f2;
	}
	.annotation-item-inline.indirect {
		border-left-color: #c0ac92;
		background: rgba(247, 241, 230, 0.85);
	}
	.annotation-role-emoji {
		flex-shrink: 0;
		font-size: 0.85em;
		line-height: 1.4;
	}
	.annotation-group-wrap {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.annotation-type-badge {
		flex-shrink: 0;
		font-size: 0.63rem;
		font-weight: 700;
		color: #475569;
		background: #e2e8f0;
		border-radius: 999px;
		padding: 0.08rem 0.35rem;
		line-height: 1.35;
	}
	.annotation-expand-indicator {
		flex-shrink: 0;
		font-size: 0.62rem;
		color: #64748b;
		line-height: 1.5;
		margin-left: auto;
	}
	.annotation-item-inline .annotation-action-text {
		flex: 1 1 0;
		min-width: 0;
		color: #334155;
		font-size: 0.75rem;
		line-height: 1.4;
		word-break: break-word;
	}
	.annotation-group-expanded {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		margin-left: 1.2rem;
		padding-left: 0.55rem;
		border-left: 2px solid #dbe4ee;
	}
	.annotation-group-expanded-item {
		font-size: 0.7rem;
		line-height: 1.35;
		color: #64748b;
		word-break: break-word;
	}
</style>
