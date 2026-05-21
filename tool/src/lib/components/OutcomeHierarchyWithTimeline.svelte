<script lang="ts">
	import type { OutcomeNode } from '$lib/types';
	import { isAssistantSpeakerId } from '$lib/speakerUtils';
	import { isEunsuSpeakerName } from '$lib/contribPeerBar';
	import { stripTrailingParen, outcomeSortKey, outcomeLabel } from '$lib/utils';
	import type {
		OutcomeActionMapData,
		RequirementActionMap,
		OutcomeActionItem,
		UtteranceListData,
		RequirementStatusById
	} from '$lib/data/dataLoader';

	type DepEdge = { parentId: string; childId: string };
	type TurnCounts = {
		shaper: number;
		executor: number;
		user: number;
		assistant: number;
		userShaper: number;
		userExecutor: number;
		assistantShaper: number;
		assistantExecutor: number;
	};

	function getTurnIdFromActionId(actionId: string): number {
		const parts = actionId.split('-');
		return parseInt(parts[0], 10) || 0;
	}
	function normalizeRole(role: string | undefined): 'SHAPER' | 'EXECUTOR' {
		const r = (role ?? 'EXECUTOR').toUpperCase();
		if (r === 'CREATOR' || r === 'SHAPER') return 'SHAPER';
		return 'EXECUTOR';
	}

	let {
		outcomes = [],
		outputVersions = [],
		dependencyEdges = [],
		selectedOutcomeId = null,
		requirementTextsByOutcome = new Map<string, string[]>(),
		outcomeIntentionMap = {},
		onOutcomeClick,
		onIntentClick,
		outcomeActionMap = null,
		requirementActionMap = {},
		utteranceList = null,
		finalTurn = 1,
		requirementStatusById = {}
	}: {
		outcomes: OutcomeNode[];
		outputVersions?: Array<{ id: string; content: string; turn_id: number }>;
		dependencyEdges?: DepEdge[];
		selectedOutcomeId?: string | null;
		requirementTextsByOutcome?: Map<string, string[]>;
		outcomeIntentionMap?: Record<string, string>;
		onOutcomeClick?: (outcomeId: string) => void;
		onIntentClick?: (intentLabel: string | null) => void;
		outcomeActionMap?: OutcomeActionMapData | null;
		requirementActionMap?: RequirementActionMap;
		utteranceList?: UtteranceListData | null;
		finalTurn?: number;
		requirementStatusById?: RequirementStatusById;
	} = $props();

	let expandedParentIds = $state<Set<string>>(new Set());
	let selectedIntentLabel = $state<string | null>(null);
	let intentLaneFocusActive = $state(false);
	let hierarchyListEl = $state<HTMLUListElement | null>(null);
	const hierarchyItemEls = new Map<string, HTMLLIElement>();
	/** Intent group headers that are collapsed (collapsed by default on load) */
	let collapsedIntentGroups = $state<Set<string>>(new Set());
	let lastOutcomesKey = $state(0);

	type ChartPopup = {
		label: string;
		sublabel?: string;
		series: { userShaper: number; assistantShaper: number; userExecutor: number; assistantExecutor: number }[];
		userShaper: number; assistantShaper: number;
		userExecutor: number; assistantExecutor: number;
	};
	let chartPopup = $state<ChartPopup | null>(null);

	function openChartPopup(popup: ChartPopup, e: MouseEvent) {
		e.stopPropagation();
		chartPopup = popup;
	}
	function closeChartPopup() { chartPopup = null; }

	function toggleIntentGroup(intent: string) {
		const next = new Set(collapsedIntentGroups);
		if (next.has(intent)) next.delete(intent);
		else next.add(intent);
		collapsedIntentGroups = next;
	}

	function getOutcomeNumber(outcomeId: string): number {
		return outcomeSortKey(outcomeId);
	}

function baseOutcomeId(outputId: string): string {
	const m = outputId.match(/^(.+)_\d+$/);
	return m ? m[1] : outputId;
}

	function getIntentLabel(outcomeId: string): string {
		return (outcomeIntentionMap[outcomeId] ?? 'Unmapped').trim() || 'Unmapped';
	}

	function toggleSubHierarchy(parentId: string, e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		expandedParentIds = new Set(expandedParentIds);
		if (expandedParentIds.has(parentId)) expandedParentIds.delete(parentId);
		else expandedParentIds.add(parentId);
	}

	function trackHierarchyItem(el: HTMLLIElement, params: { outcomeId: string }) {
		hierarchyItemEls.set(params.outcomeId, el);
		return {
			update(next: { outcomeId: string }) {
				if (next.outcomeId !== params.outcomeId) {
					hierarchyItemEls.delete(params.outcomeId);
					hierarchyItemEls.set(next.outcomeId, el);
				}
				params = next;
			},
			destroy() {
				hierarchyItemEls.delete(params.outcomeId);
			}
		};
	}

	// DFS-based cycle detection: drop back-edges (edges into a node currently on the stack)
	// so the result is guaranteed acyclic regardless of ID ordering.
	const acyclicEdges = $derived.by(() => {
		const ids = new Set(outcomes.map((o) => o.outcome_id));
		const validEdges = dependencyEdges.filter(
			(e) => ids.has(e.parentId) && ids.has(e.childId) && e.parentId !== e.childId
		);

		const adj = new Map<string, DepEdge[]>();
		for (const e of validEdges) {
			const list = adj.get(e.parentId) ?? [];
			list.push(e);
			adj.set(e.parentId, list);
		}

		const UNVISITED = 0, IN_STACK = 1, DONE = 2;
		const state = new Map<string, number>();
		const backEdges = new Set<DepEdge>();

		const dfs = (node: string) => {
			state.set(node, IN_STACK);
			for (const edge of adj.get(node) ?? []) {
				const s = state.get(edge.childId) ?? UNVISITED;
				if (s === IN_STACK) backEdges.add(edge);
				else if (s === UNVISITED) dfs(edge.childId);
			}
			state.set(node, DONE);
		};

		for (const o of outcomes) {
			if ((state.get(o.outcome_id) ?? UNVISITED) === UNVISITED) dfs(o.outcome_id);
		}

		return validEdges.filter((e) => !backEdges.has(e));
	});

	const parentByOutcomeId = $derived.by(() => {
		const map = new Map<string, string>();
		for (const e of acyclicEdges) {
			if (!map.has(e.childId)) map.set(e.childId, e.parentId);
		}
		return map;
	});

	const childrenByParentId = $derived.by(() => {
		const map = new Map<string, string[]>();
		for (const e of acyclicEdges) {
			const list = map.get(e.parentId) ?? [];
			if (!list.includes(e.childId)) list.push(e.childId);
			map.set(e.parentId, list);
		}
		for (const [, list] of map) {
			list.sort((a, b) => outcomes.findIndex((o) => o.outcome_id === a) - outcomes.findIndex((o) => o.outcome_id === b));
		}
		return map;
	});

	const orderedOutcomes = $derived.by(() => {
		if (outcomes.length === 0) return [];
		const added = new Set<string>();
		const result: OutcomeNode[] = [];
		const visit = (oid: string) => {
			if (added.has(oid)) return;
			added.add(oid);
			const node = outcomes.find((o) => o.outcome_id === oid);
			if (node) result.push(node);
			for (const c of childrenByParentId.get(oid) ?? []) visit(c);
		};
		const roots = outcomes.filter((o) => !parentByOutcomeId.has(o.outcome_id));
		roots.sort((a, b) => outcomes.indexOf(a) - outcomes.indexOf(b));
		for (const r of roots) visit(r.outcome_id);
		for (const o of outcomes) {
			if (!added.has(o.outcome_id)) visit(o.outcome_id);
		}
		return result;
	});

	const displayDepthByOutcomeId = $derived.by(() => {
		const displayDepth = new Map<string, number>();
		const visit = (oid: string, depth: number) => {
			displayDepth.set(oid, depth);
			for (const c of childrenByParentId.get(oid) ?? []) visit(c, depth + 1);
		};
		const roots = outcomes.filter((o) => !parentByOutcomeId.has(o.outcome_id));
		roots.sort((a, b) => outcomes.indexOf(a) - outcomes.indexOf(b));
		for (const r of roots) visit(r.outcome_id, 0);
		return displayDepth;
	});

	const visibleOutcomes = $derived.by(() => {
		const visible = new Set<string>();
		for (const o of orderedOutcomes) {
			const depth = displayDepthByOutcomeId.get(o.outcome_id) ?? 0;
			if (depth === 0) {
				visible.add(o.outcome_id);
				continue;
			}
			const parentId = parentByOutcomeId.get(o.outcome_id);
			if (parentId != null && visible.has(parentId) && expandedParentIds.has(parentId)) {
				visible.add(o.outcome_id);
			}
		}
		return orderedOutcomes.filter((o) => visible.has(o.outcome_id));
	});

	const groupedVisibleOutcomes = $derived.by(() => {
		const groups: { intent: string; outcomes: OutcomeNode[] }[] = [];
		const intentIndex = new Map<string, number>();
		for (const outcome of visibleOutcomes) {
			const intent = (outcomeIntentionMap[outcome.outcome_id] ?? 'Unmapped').trim();
			const normalizedIntent = intent.length > 0 ? intent : 'Unmapped';
			const idx = intentIndex.get(normalizedIntent);
			if (idx == null) {
				intentIndex.set(normalizedIntent, groups.length);
				groups.push({ intent: normalizedIntent, outcomes: [outcome] });
			} else {
				groups[idx].outcomes.push(outcome);
			}
		}
		return groups;
	});

	/** Groups visibleOutcomes by ROOT-ANCESTOR intent (so cross-intent children
	 *  appear inside their parent's group). Cross-intent children show their own
	 *  intent label inline.
	 */
	const hierarchyGroups = $derived.by(() => {
		const rootOf = (oid: string): string => {
			let id = oid;
			while (parentByOutcomeId.has(id)) id = parentByOutcomeId.get(id)!;
			return id;
		};
		const groups: { intent: string; outcomes: OutcomeNode[] }[] = [];
		const intentIdx = new Map<string, number>();
		for (const o of visibleOutcomes) {
			const intent = getIntentLabel(rootOf(o.outcome_id));
			if (!intentIdx.has(intent)) {
				intentIdx.set(intent, groups.length);
				groups.push({ intent, outcomes: [] });
			}
			groups[intentIdx.get(intent)!].outcomes.push(o);
		}
		return groups;
	});

	/** Per root-intent, count ALL outcomes (root + descendants), not only roots. */
	const totalOutcomesByRootIntent = $derived.by(() => {
		const rootOf = (oid: string): string => {
			let id = oid;
			while (parentByOutcomeId.has(id)) id = parentByOutcomeId.get(id)!;
			return id;
		};
		const map = new Map<string, number>();
		for (const o of orderedOutcomes) {
			const intent = getIntentLabel(rootOf(o.outcome_id));
			map.set(intent, (map.get(intent) ?? 0) + 1);
		}
		return map;
	});

	$effect(() => {
		const groups = hierarchyGroups;
		if (groups.length === 0) return;
		const dataKey = outcomes.length;
		if (dataKey !== lastOutcomesKey) {
			lastOutcomesKey = dataKey;
			collapsedIntentGroups = new Set(groups.map((g) => g.intent));
		}
	});

	$effect(() => {
		if (!selectedOutcomeId || !outcomes.some((o) => o.outcome_id === selectedOutcomeId)) return;
		const next = new Set(expandedParentIds);
		let changed = false;
		const ensureExpanded = (outcomeId: string) => {
			const children = childrenByParentId.get(outcomeId) ?? [];
			if (children.length > 0 && !next.has(outcomeId)) {
				next.add(outcomeId);
				changed = true;
			}
			for (const childId of children) ensureExpanded(childId);
		};
		// Keep selected node visible by opening all ancestors.
		let currentParentId = parentByOutcomeId.get(selectedOutcomeId);
		while (currentParentId != null) {
			if (!next.has(currentParentId)) {
				next.add(currentParentId);
				changed = true;
			}
			currentParentId = parentByOutcomeId.get(currentParentId);
		}
		// Also expand the selected subtree so all descendants become visible at once.
		ensureExpanded(selectedOutcomeId);
		if (changed) expandedParentIds = next;
	});

	$effect(() => {
		selectedOutcomeId;
		visibleOutcomes;
		const listEl = hierarchyListEl;
		if (!listEl || !selectedOutcomeId) return;
		requestAnimationFrame(() => {
			const itemEl = hierarchyItemEls.get(selectedOutcomeId);
			if (!itemEl) return;
			const listRect = listEl.getBoundingClientRect();
			const itemRect = itemEl.getBoundingClientRect();
			const currentY = itemRect.top - listRect.top;
			const middleY = listRect.height / 2;
			// Keep selected row no lower than middle: y = min(middle, originalY).
			const targetY = Math.min(middleY, currentY);
			const deltaY = currentY - targetY;
			if (Math.abs(deltaY) < 1) return;
			const maxScrollTop = Math.max(0, listEl.scrollHeight - listEl.clientHeight);
			const nextScrollTop = Math.max(0, Math.min(maxScrollTop, listEl.scrollTop + deltaY));
			if (Math.abs(nextScrollTop - listEl.scrollTop) >= 1) listEl.scrollTop = nextScrollTop;
		});
	});

	/** Detect Slack peer mode: two named speakers (excluding Eunsu & canonical user/assistant). */
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

	function classifySpeakerSide(spk: string, peers: { a: string; b: string } | null): 'user' | 'assistant' | 'skip' {
		if (peers) {
			if (spk === peers.a) return 'user';
			if (spk === peers.b) return 'assistant';
			return 'skip';
		}
		if (isAssistantSpeakerId(spk)) return 'assistant';
		return 'user';
	}

	/** Per outcome_id: for each turn 0..finalTurn, { shaper, executor, user, assistant } direct-only counts */
	const outcomeTimelineCounts = $derived.by((): Record<string, TurnCounts[]> => {
		const result: Record<string, TurnCounts[]> = {};
		const maxTurn = Math.max(0, finalTurn);
		type OriginAction = { action_id: string; role: string };
		for (const outcome of outcomes) {
			const counts: TurnCounts[] = [];
			for (let t = 0; t <= maxTurn; t++) {
				counts.push({
					shaper: 0,
					executor: 0,
					user: 0,
					assistant: 0,
					userShaper: 0,
					userExecutor: 0,
					assistantShaper: 0,
					assistantExecutor: 0
				});
			}
			/** Dedupe by action_id from requirement buckets, then overlay outcome_action_map (canonical per-goal roles). */
			const byActionId = new Map<string, { turn_id: number; role: 'SHAPER' | 'EXECUTOR'; direct: boolean }>();
			function mergeReqAction(actionId: string, turn_id: number, roleRaw: string | undefined, direct: boolean) {
				const role = normalizeRole(roleRaw);
				const prev = byActionId.get(actionId);
				if (!prev) {
					byActionId.set(actionId, { turn_id, role, direct });
					return;
				}
				const mergedRole = prev.role === 'SHAPER' || role === 'SHAPER' ? 'SHAPER' : 'EXECUTOR';
				byActionId.set(actionId, { turn_id: prev.turn_id, role: mergedRole, direct });
			}
			for (const reqId of outcome.requirements) {
				const entry = requirementActionMap[reqId];
				if (!entry) continue;
				const add = (a: OriginAction, direct: boolean) => {
					const turn_id = getTurnIdFromActionId(a.action_id);
					mergeReqAction(a.action_id, turn_id, a.role, direct);
				};
				const origin = Array.isArray(entry.origin_actions) ? entry.origin_actions : [];
				const contrib = Array.isArray((entry as { contributing_actions?: OriginAction[] }).contributing_actions) ? (entry as { contributing_actions: OriginAction[] }).contributing_actions : [];
				const impl = Array.isArray((entry as { implementation_actions?: OriginAction[] }).implementation_actions) ? (entry as { implementation_actions: OriginAction[] }).implementation_actions : [];
				for (const a of origin) add(a, true);
				/* Count contributing_actions in the timeline too: execution is often only here while
				 * `is_executed` is true; excluding them left Executor empty. */
				for (const a of contrib) add(a, true);
				for (const a of impl) add(a, true);
			}
			const oamEntry = outcomeActionMap?.outcome_action_map?.[outcome.outcome_id];
			const oamActions =
				oamEntry?.actions?.filter((a) => !a.bound_outcome_id || a.bound_outcome_id === outcome.outcome_id) ?? [];
			for (const a of oamActions) {
				const turn_id = a.turn_id ?? getTurnIdFromActionId(a.action_id);
				byActionId.set(a.action_id, { turn_id, role: normalizeRole(a.role), direct: true });
			}
			const points: { turn_id: number; role: string; direct: boolean }[] = [];
			for (const p of byActionId.values()) {
				points.push({ turn_id: p.turn_id, role: p.role, direct: p.direct });
			}
			const createdTurn = outcome.created_at ?? 0;
			const hasShaperActions = points.some(p => p.role === 'SHAPER');
			if (hasShaperActions && createdTurn >= 0 && createdTurn <= maxTurn) points.push({ turn_id: createdTurn, role: 'SHAPER', direct: true });

			const speakerMap = new Map<number, string>();
			for (const u of utteranceList?.utterances ?? []) speakerMap.set(u.turn_id, u.speaker);
			const resolveSpeaker = (turnId: number): string | undefined =>
				speakerMap.get(turnId) ?? speakerMap.get(turnId - 1) ?? speakerMap.get(turnId + 1);
			for (const { turn_id, role, direct } of points) {
				if (!direct) continue;
				if (turn_id >= 0 && turn_id < counts.length) {
					if (role === 'SHAPER') counts[turn_id].shaper += 1;
					else counts[turn_id].executor += 1;
					const spk = resolveSpeaker(turn_id);
					if (spk != null && spk !== '') {
						const side = classifySpeakerSide(spk, peerNames);
						if (side === 'skip') continue;
						if (side === 'assistant') {
							counts[turn_id].assistant += 1;
							if (role === 'SHAPER') counts[turn_id].assistantShaper += 1;
							else counts[turn_id].assistantExecutor += 1;
						} else {
							counts[turn_id].user += 1;
							if (role === 'SHAPER') counts[turn_id].userShaper += 1;
							else counts[turn_id].userExecutor += 1;
						}
					} else {
						counts[turn_id].user += 0.5;
						counts[turn_id].assistant += 0.5;
						if (role === 'SHAPER') {
							counts[turn_id].userShaper += 0.5;
							counts[turn_id].assistantShaper += 0.5;
						} else {
							counts[turn_id].userExecutor += 0.5;
							counts[turn_id].assistantExecutor += 0.5;
						}
					}
				}
			}
			result[outcome.outcome_id] = counts;
		}
		return result;
	});

	const intentTimelineCounts = $derived.by(() => {
		const result = new Map<string, TurnCounts[]>();
		const maxTurn = Math.max(0, finalTurn);
		const rootOf = (oid: string): string => {
			let id = oid;
			while (parentByOutcomeId.has(id)) id = parentByOutcomeId.get(id)!;
			return id;
		};
		for (const outcome of orderedOutcomes) {
			const rootIntent = getIntentLabel(rootOf(outcome.outcome_id));
			if (!result.has(rootIntent)) {
				result.set(
					rootIntent,
					Array.from({ length: maxTurn + 1 }, () => ({
						shaper: 0,
						executor: 0,
						user: 0,
						assistant: 0,
						userShaper: 0,
						userExecutor: 0,
						assistantShaper: 0,
						assistantExecutor: 0
					}))
				);
			}
			const counts = result.get(rootIntent)!;
			const oc = outcomeTimelineCounts[outcome.outcome_id] ?? [];
			for (let t = 0; t < oc.length && t < counts.length; t++) {
				counts[t].shaper     += oc[t].shaper;
				counts[t].executor   += oc[t].executor;
				counts[t].user       += oc[t].user;
				counts[t].assistant  += oc[t].assistant;
				counts[t].userShaper += oc[t].userShaper;
				counts[t].userExecutor += oc[t].userExecutor;
				counts[t].assistantShaper += oc[t].assistantShaper;
				counts[t].assistantExecutor += oc[t].assistantExecutor;
			}
		}
		return result;
	});

	const outcomeAggregateTimelineCounts = $derived.by(() => {
		const maxTurn = Math.max(0, finalTurn);
		const result: Record<string, TurnCounts[]> = {};
		const allDescendants = (oid: string): string[] => {
			const kids = childrenByParentId.get(oid) ?? [];
			return kids.flatMap(k => [k, ...allDescendants(k)]);
		};
		for (const outcome of orderedOutcomes) {
			const hasChildren = (childrenByParentId.get(outcome.outcome_id) ?? []).length > 0;
			if (!hasChildren) {
				result[outcome.outcome_id] = outcomeTimelineCounts[outcome.outcome_id] ?? [];
			} else {
				const counts: TurnCounts[] = Array.from({ length: maxTurn + 1 }, () => ({
					shaper: 0,
					executor: 0,
					user: 0,
					assistant: 0,
					userShaper: 0,
					userExecutor: 0,
					assistantShaper: 0,
					assistantExecutor: 0
				}));
				for (const id of [outcome.outcome_id, ...allDescendants(outcome.outcome_id)]) {
					const oc = outcomeTimelineCounts[id] ?? [];
					for (let t = 0; t < oc.length && t < counts.length; t++) {
						counts[t].shaper     += oc[t].shaper;
						counts[t].executor   += oc[t].executor;
						counts[t].user       += oc[t].user;
						counts[t].assistant  += oc[t].assistant;
						counts[t].userShaper += oc[t].userShaper;
						counts[t].userExecutor += oc[t].userExecutor;
						counts[t].assistantShaper += oc[t].assistantShaper;
						counts[t].assistantExecutor += oc[t].assistantExecutor;
					}
				}
				result[outcome.outcome_id] = counts;
			}
		}
		return result;
	});

	type ContribRatio = { userShaper: number; assistantShaper: number; userExecutor: number; assistantExecutor: number; hasShaper: boolean; hasExecutor: boolean };
	function sumContrib(counts: TurnCounts[]): ContribRatio {
		let uS = 0, aS = 0, uE = 0, aE = 0;
		for (const c of counts) { uS += c.userShaper; aS += c.assistantShaper; uE += c.userExecutor; aE += c.assistantExecutor; }
		const sT = uS + aS;
		const eT = uE + aE;
		return {
			userShaper: sT > 0 ? uS / sT : 0, assistantShaper: sT > 0 ? aS / sT : 0,
			userExecutor: eT > 0 ? uE / eT : 0, assistantExecutor: eT > 0 ? aE / eT : 0,
			hasShaper: sT > 0, hasExecutor: eT > 0
		};
	}

	function dominantLabel(userRatio: number, assistantRatio: number): string {
		if (peerNames) {
			if (userRatio >= 0.55) return `${peerNames.a}-led`;
			if (assistantRatio >= 0.55) return `${peerNames.b}-led`;
			return 'Shared';
		}
		if (userRatio >= 0.55) return 'User-led';
		if (assistantRatio >= 0.55) return 'Assistant-led';
		return 'Shared';
	}

	function outcomeContributionSummary(ratio: ContribRatio | undefined): string {
		if (!ratio) return 'No contribution data';
		const parts: string[] = [];
		if (ratio.hasShaper) {
			parts.push(`Shaper ${dominantLabel(ratio.userShaper, ratio.assistantShaper)}`);
		}
		if (ratio.hasExecutor) {
			parts.push(`Executor ${dominantLabel(ratio.userExecutor, ratio.assistantExecutor)}`);
		}
		return parts.length > 0 ? parts.join(' · ') : 'No contribution data';
	}

	const outcomeContribRatios = $derived.by((): Record<string, ContribRatio> => {
		const result: Record<string, ContribRatio> = {};
		for (const [oid, counts] of Object.entries(outcomeAggregateTimelineCounts)) {
			result[oid] = sumContrib(counts);
		}
		return result;
	});

	const intentContribRatios = $derived.by((): Map<string, ContribRatio> => {
		const result = new Map<string, ContribRatio>();
		for (const [intent, counts] of intentTimelineCounts) {
			result.set(intent, sumContrib(counts));
		}
		return result;
	});

	function miniChartSeries(
		counts: TurnCounts[],
		bins: number
	): Array<{
		userShaper: number;
		assistantShaper: number;
		userExecutor: number;
		assistantExecutor: number;
	}> {
		if (counts.length === 0) return [];
		const binsArr: {
			userShaper: number;
			assistantShaper: number;
			userExecutor: number;
			assistantExecutor: number;
		}[] = Array.from(
			{ length: bins },
			() => ({ userShaper: 0, assistantShaper: 0, userExecutor: 0, assistantExecutor: 0 })
		);
		for (let t = 0; t < counts.length; t++) {
			const b = Math.min(bins - 1, Math.floor((t / counts.length) * bins));
			binsArr[b].userShaper += counts[t].userShaper;
			binsArr[b].assistantShaper += counts[t].assistantShaper;
			binsArr[b].userExecutor += counts[t].userExecutor;
			binsArr[b].assistantExecutor += counts[t].assistantExecutor;
		}
		const maxShaper = Math.max(
			1,
			...binsArr.map((b) => Math.max(b.userShaper, b.assistantShaper))
		);
		const maxExecutor = Math.max(
			1,
			...binsArr.map((b) => Math.max(b.userExecutor, b.assistantExecutor))
		);
		return binsArr.map((b) => ({
			userShaper: b.userShaper / maxShaper,
			assistantShaper: b.assistantShaper / maxShaper,
			userExecutor: b.userExecutor / maxExecutor,
			assistantExecutor: b.assistantExecutor / maxExecutor
		}));
	}

	function linePath(values: number[], width: number, height: number): string {
		if (values.length === 0) return '';
		const hasAnyPositive = values.some((v) => v > 0);
		if (!hasAnyPositive) return '';
		const px = 1;
		const py = 1;
		const usableW = Math.max(1, width - px * 2);
		const usableH = Math.max(1, height - py * 2);
		const step = values.length > 1 ? usableW / (values.length - 1) : 0;
		let d = '';
		let i = 0;
		while (i < values.length) {
			while (i < values.length && values[i] <= 0) i += 1;
			if (i >= values.length) break;
			const start = i;
			let end = i;
			while (end + 1 < values.length && values[end + 1] > 0) end += 1;
			const idxs: number[] = [];
			if (start - 1 >= 0) idxs.push(start - 1);
			for (let k = start; k <= end; k += 1) idxs.push(k);
			if (end + 1 < values.length) idxs.push(end + 1);
			for (let j = 0; j < idxs.length; j += 1) {
				const idx = idxs[j];
				const v = Math.max(0, Math.min(1, values[idx]));
				const x = px + step * idx;
				const y = py + (1 - v) * usableH;
				d += (j === 0 ? ' M' : ' L') + x.toFixed(2) + ' ' + y.toFixed(2);
			}
			i = end + 1;
		}
		return d.trim();
	}

	function getReqStatus(reqId: string) {
		const s = requirementStatusById[reqId]
			?? requirementStatusById[reqId.replace(/^r/, '')]
			?? requirementStatusById[/^r/.test(reqId) ? reqId : `r${reqId}`];
		return s ?? null;
	}

	/** For outcomes without requirements: treat execution as binary (0% or 100%). */
	function isNoReqOutcomeExecuted(outcomeId: string): boolean {
		const entry = outcomeActionMap?.outcome_action_map?.[outcomeId];
		const actions = Array.isArray(entry?.actions) ? entry.actions : [];
		// Executed if at least one executor-side action exists.
		return actions.some((a) => normalizeRole(a.role) === 'EXECUTOR');
	}

	/** Per-intent: { executed, pending, dismissed, total } across ALL outcomes */
	const intentCompletionRates = $derived.by(() => {
		const result = new Map<string, { executed: number; pending: number; dismissed: number; total: number }>();
		const rootOf = (oid: string): string => {
			let id = oid;
			while (parentByOutcomeId.has(id)) id = parentByOutcomeId.get(id)!;
			return id;
		};
		for (const outcome of orderedOutcomes) {
			const rootIntent = getIntentLabel(rootOf(outcome.outcome_id));
			if (!result.has(rootIntent)) result.set(rootIntent, { executed: 0, pending: 0, dismissed: 0, total: 0 });
			const entry = result.get(rootIntent)!;
			if (outcome.requirements.length === 0) {
				entry.total += 1;
				if (isNoReqOutcomeExecuted(outcome.outcome_id)) entry.executed += 1;
				else entry.pending += 1;
				continue;
			}
			for (const reqId of outcome.requirements) {
				const s = getReqStatus(reqId);
				entry.total += 1;
				if (s?.is_dismissed) entry.dismissed += 1;
				else if (s?.is_executed) entry.executed += 1;
				else entry.pending += 1;
			}
		}
		return result;
	});

	/** Per-outcome (sub-intent): { executed, pending, dismissed, total } for that outcome's requirements */
	const outcomeCompletionRates = $derived.by(() => {
		const result = new Map<string, { executed: number; pending: number; dismissed: number; total: number }>();
		for (const outcome of orderedOutcomes) {
			const entry = { executed: 0, pending: 0, dismissed: 0, total: 0 };
			if (outcome.requirements.length === 0) {
				entry.total = 1;
				entry.executed = isNoReqOutcomeExecuted(outcome.outcome_id) ? 1 : 0;
				entry.pending = entry.executed === 1 ? 0 : 1;
				result.set(outcome.outcome_id, entry);
				continue;
			}
			for (const reqId of outcome.requirements) {
				const s = getReqStatus(reqId);
				entry.total += 1;
				if (s?.is_dismissed) entry.dismissed += 1;
				else if (s?.is_executed) entry.executed += 1;
				else entry.pending += 1;
			}
			if (entry.total > 0) result.set(outcome.outcome_id, entry);
		}
		return result;
	});

	function positionPercent(turn: number): number {
		if (finalTurn <= 0) return 0;
		return Math.min(100, Math.max(0, (turn / finalTurn) * 100));
	}

	// --- Vertical outcome timeline strip (creation turn → top%) ---
	const creationTurnByOutcomeId = $derived.by((): Map<string, number> => {
		const map = new Map<string, number>();
		for (const outcome of outcomes) {
			let earliest = outcome.created_at ?? 0;
			if (outcome.requirements.length === 0 && outcomeActionMap?.outcome_action_map) {
				const entry = outcomeActionMap.outcome_action_map[outcome.outcome_id];
				const actions: OutcomeActionItem[] = entry?.actions ?? [];
				for (const a of actions) {
					const t = a.turn_id ?? getTurnIdFromActionId(a.action_id);
					if (t < earliest) earliest = t;
				}
			} else {
				for (const reqId of outcome.requirements) {
					const entry = requirementActionMap[reqId] as { origin_actions?: { action_id: string }[] } | undefined;
					const origin = Array.isArray(entry?.origin_actions) ? entry.origin_actions : [];
					for (const a of origin) {
						const t = getTurnIdFromActionId(a.action_id);
						if (t < earliest) earliest = t;
					}
				}
			}
			map.set(outcome.outcome_id, earliest);
		}
		return map;
	});

const outputVersionTurnsByOutcomeId = $derived.by(() => {
	const map = new Map<string, number[]>();
	for (const out of outputVersions) {
		const oid = baseOutcomeId(out.id);
		const list = map.get(oid) ?? [];
		list.push(out.turn_id ?? 0);
		map.set(oid, list);
	}
	for (const [oid, turns] of map) {
		map.set(oid, Array.from(new Set(turns)).sort((a, b) => a - b));
	}
	return map;
});

	const selectedGroupLabel = $derived.by(() => {
		if (selectedIntentLabel) return selectedIntentLabel;
		if (!selectedOutcomeId) return null;
		return (outcomeIntentionMap[selectedOutcomeId] ?? 'Unmapped').trim() || 'Unmapped';
	});

	const speakerByTurnId = $derived.by(() => {
		const map = new Map<number, string>();
		for (const u of utteranceList?.utterances ?? []) {
			map.set(u.turn_id, u.speaker);
		}
		return map;
	});

	const intentOutcomeBubbles = $derived.by(() => {
		const maxTurn = Math.max(1, finalTurn);
		const rows: Array<{
			intent: string;
			laneIndex: number;
			outcomeId: string;
			outcomeNumber: number;
			topPct: number;
			intensity: number;
			userRatio: number;
			assistantRatio: number;
			shaperRatio: number;
			executorRatio: number;
			directRatio: number;
			indirectRatio: number;
		}> = [];
		type ActionLite = {
			action_id: string;
			turn_id?: number;
			speaker?: string;
			role?: string;
			influence?: 'direct' | 'indirect';
		};
		for (const [laneIndex, group] of groupedVisibleOutcomes.entries()) {
			for (const outcome of group.outcomes) {
				const seen = new Set<string>();
				const actions: ActionLite[] = [];
				if (outcome.requirements.length === 0 && outcomeActionMap?.outcome_action_map) {
					const entry = outcomeActionMap.outcome_action_map[outcome.outcome_id];
					for (const a of entry?.actions ?? []) {
						if (!a?.action_id || seen.has(a.action_id)) continue;
						seen.add(a.action_id);
						actions.push({
							action_id: a.action_id,
							turn_id: a.turn_id,
							speaker: a.speaker,
							role: a.role,
							influence: 'direct'
						});
					}
				} else {
					for (const reqId of outcome.requirements) {
						const entry = requirementActionMap[reqId] as
							| {
									origin_actions?: ActionLite[];
									contributing_actions?: ActionLite[];
									implementation_actions?: ActionLite[];
									related_actions?: Array<
										ActionLite & { relationship_type?: string; influence?: string }
									>;
							  }
							| undefined;
						const origin = Array.isArray(entry?.origin_actions) ? entry.origin_actions : [];
						for (const a of origin) {
							if (!a?.action_id || seen.has(a.action_id)) continue;
							seen.add(a.action_id);
							actions.push({ ...a, influence: 'direct' });
						}
						const impl = Array.isArray(entry?.implementation_actions)
							? entry.implementation_actions
							: [];
						for (const a of impl) {
							if (!a?.action_id || seen.has(a.action_id)) continue;
							seen.add(a.action_id);
							actions.push({ ...a, influence: 'direct' });
						}
					const contrib = Array.isArray(entry?.contributing_actions)
						? entry.contributing_actions
						: [];
					for (const a of contrib) {
						if (!a?.action_id || seen.has(a.action_id)) continue;
						seen.add(a.action_id);
						actions.push({ ...a, influence: 'direct' });
					}
						const related = Array.isArray(entry?.related_actions) ? entry.related_actions : [];
						for (const a of related) {
							if (!a?.action_id || seen.has(a.action_id)) continue;
							seen.add(a.action_id);
							const rel = (a.relationship_type ?? '').toUpperCase();
							const infl =
								a.influence === 'direct' ||
								rel === 'DIRECT_CONNECTION' ||
								rel === 'DIRECT'
									? 'direct'
									: 'indirect';
							actions.push({ ...a, influence: infl });
						}
					}
				}
				const turns = actions.map((a) => {
					if (typeof a.turn_id === 'number') return a.turn_id;
					return getTurnIdFromActionId(a.action_id);
				});
				const avgTurn =
					turns.length > 0
						? turns.reduce((acc, t) => acc + t, 0) / turns.length
						: (outcome.created_at ?? 0);
				let userCount = 0;
				let assistantCount = 0;
				let shaperCount = 0;
				let executorCount = 0;
				let directCount = 0;
				let indirectCount = 0;
				for (const a of actions) {
					const turn = typeof a.turn_id === 'number' ? a.turn_id : getTurnIdFromActionId(a.action_id);
					const speaker = a.speaker ?? speakerByTurnId.get(turn);
					if (speaker != null && speaker !== '') {
						const side = classifySpeakerSide(speaker, peerNames);
						if (side === 'assistant') assistantCount += 1;
						else if (side === 'user') userCount += 1;
					}
					const role = normalizeRole(a.role);
					if (role === 'SHAPER') shaperCount += 1;
					else executorCount += 1;
					if (a.influence === 'indirect') indirectCount += 1;
					else directCount += 1;
				}
				const knownTotal = userCount + assistantCount;
				const userRatio = knownTotal > 0 ? userCount / knownTotal : 0.5;
				const assistantRatio = knownTotal > 0 ? assistantCount / knownTotal : 0.5;
				const roleTotal = shaperCount + executorCount;
				const shaperRatio = roleTotal > 0 ? shaperCount / roleTotal : 0.5;
				const executorRatio = roleTotal > 0 ? executorCount / roleTotal : 0.5;
				const infTotal = directCount + indirectCount;
				const directRatio = infTotal > 0 ? directCount / infTotal : 0.7;
				const indirectRatio = infTotal > 0 ? indirectCount / infTotal : 0.3;
				const baseRow = {
					intent: group.intent,
					laneIndex,
					outcomeId: outcome.outcome_id,
					outcomeNumber: getOutcomeNumber(outcome.outcome_id),
					topPct: 0,
					intensity: Math.max(1, actions.length),
					userRatio,
					assistantRatio,
					shaperRatio,
					executorRatio,
					directRatio,
					indirectRatio
				};
				const versionTurns = outputVersionTurnsByOutcomeId.get(outcome.outcome_id) ?? [];
				if (versionTurns.length > 0) {
					for (const vt of versionTurns) {
						rows.push({
							...baseRow,
							topPct: (Math.max(0, Math.min(maxTurn, vt)) / maxTurn) * 100
						});
					}
				} else {
					rows.push({
						...baseRow,
						topPct: (Math.max(0, Math.min(maxTurn, avgTurn)) / maxTurn) * 100
					});
				}
			}
		}
		const maxIntensity = Math.max(...rows.map((r) => r.intensity), 1);
		return rows.map((r) => {
			const t = r.intensity / maxIntensity;
			const radiusPx = 8 + t * 14;
			return { ...r, radiusPx };
		});
	});

	const intentBubbles = $derived.by(() => {
		const map = new Map<
			string,
			{
				intent: string;
				groupIndex: number;
				totalIntensity: number;
				weightedX: number;
				weightedUser: number;
				weightedAssistant: number;
				weightedShaper: number;
				weightedExecutor: number;
				weightedDirect: number;
				weightedIndirect: number;
				outcomeCount: number;
			}
		>();
		for (const b of intentOutcomeBubbles) {
			const current = map.get(b.intent) ?? {
				intent: b.intent,
				groupIndex: groupedVisibleOutcomes.findIndex((g) => g.intent === b.intent) + 1,
				totalIntensity: 0,
				weightedX: 0,
				weightedUser: 0,
				weightedAssistant: 0,
				weightedShaper: 0,
				weightedExecutor: 0,
				weightedDirect: 0,
				weightedIndirect: 0,
				outcomeCount: 0
			};
			current.totalIntensity += b.intensity;
			current.weightedX += b.topPct * b.intensity;
			current.weightedUser += b.userRatio * b.intensity;
			current.weightedAssistant += b.assistantRatio * b.intensity;
			current.weightedShaper += b.shaperRatio * b.intensity;
			current.weightedExecutor += b.executorRatio * b.intensity;
			current.weightedDirect += b.directRatio * b.intensity;
			current.weightedIndirect += b.indirectRatio * b.intensity;
			current.outcomeCount += 1;
			map.set(b.intent, current);
		}
		const rows = Array.from(map.values());
		const maxTotal = Math.max(...rows.map((r) => r.totalIntensity), 1);
		return rows
			.map((r) => {
				const t = r.totalIntensity / maxTotal;
				return {
					intent: r.intent,
					groupIndex: r.groupIndex,
					xPct: r.totalIntensity > 0 ? r.weightedX / r.totalIntensity : 50,
					userRatio: r.totalIntensity > 0 ? r.weightedUser / r.totalIntensity : 0.5,
					assistantRatio: r.totalIntensity > 0 ? r.weightedAssistant / r.totalIntensity : 0.5,
					shaperRatio: r.totalIntensity > 0 ? r.weightedShaper / r.totalIntensity : 0.5,
					executorRatio: r.totalIntensity > 0 ? r.weightedExecutor / r.totalIntensity : 0.5,
					directRatio: r.totalIntensity > 0 ? r.weightedDirect / r.totalIntensity : 0.7,
					indirectRatio: r.totalIntensity > 0 ? r.weightedIndirect / r.totalIntensity : 0.3,
					totalIntensity: r.totalIntensity,
					outcomeCount: r.outcomeCount,
					radiusPx: 12 + t * 16
				};
			})
			.sort((a, b) => a.groupIndex - b.groupIndex);
	});

	const selectedIntentOutcomeBubbles = $derived.by(() => {
		if (!selectedIntentLabel) return [];
		const rows = intentOutcomeBubbles.filter((b) => b.intent === selectedIntentLabel);
		const maxI = Math.max(...rows.map((r) => r.intensity), 1);
		return rows
			.map((r) => {
				const t = r.intensity / maxI;
				return { ...r, popRadiusPx: 8 + t * 12 };
			})
			.sort((a, b) => a.topPct - b.topPct || a.outcomeNumber - b.outcomeNumber);
	});

const selectedIntentGroupForList = $derived.by(
	() => groupedVisibleOutcomes.find((g) => g.intent === selectedIntentLabel) ?? null
);

	const showIntentBubbleChart = $derived(intentBubbles.length > 0);

function firstOutcomeIdForIntent(intentLabel: string): string | null {
	const group = groupedVisibleOutcomes.find((g) => g.intent === intentLabel);
	return group?.outcomes?.[0]?.outcome_id ?? null;
}

function selectIntent(intentLabel: string | null) {
	selectedIntentLabel = intentLabel;
	intentLaneFocusActive = intentLabel != null;
	onIntentClick?.(intentLabel);
}

	function selectOutcomeAndIntent(outcomeId: string) {
		onIntentClick?.(null);
		onOutcomeClick?.(outcomeId);
	}
</script>

<div class="outcome-hierarchy-with-timeline">
	<div class="hierarchy-and-strip">
	<div class="chart-legend" data-tutorial="timeline-graph">
		<span class="legend-section">
			<span class="legend-line" style="background:{peerNames ? PEER_A_COLOR : '#3b82f6'}"></span><span class="legend-label">{peerNames ? peerNames.a : 'User'}</span>
			<span class="legend-line" style="background:{peerNames ? PEER_B_COLOR : '#16a34a'}"></span><span class="legend-label">{peerNames ? peerNames.b : 'Assistant'}</span>
		</span>
		<span class="legend-divider"></span>
		<span class="legend-section">
			<span class="legend-emoji">💡</span><span class="legend-label">Shaper</span>
			<span class="legend-emoji">🔧</span><span class="legend-label">Executor</span>
		</span>
		<span class="legend-divider"></span>
		<span class="legend-section">
			<span class="mini-ratio-bar" style="--u:0.6;--a:0.4;--cu:{peerNames ? PEER_A_COLOR : '#3b82f6'};--ca:{peerNames ? PEER_B_COLOR : '#16a34a'}"></span><span class="legend-label">Ratio</span>
		</span>
	</div>
	<ul class="hierarchy-list" role="tree" bind:this={hierarchyListEl}>
		{#each visibleOutcomes as outcome}
			{@const globalDepth = displayDepthByOutcomeId.get(outcome.outcome_id) ?? 0}
			{@const depth = globalDepth + 1}
			{@const isChildNode = globalDepth > 0}
			{@const num = outcomeLabel(outcome.outcome_id)}
			{@const children = childrenByParentId.get(outcome.outcome_id) ?? []}
			{@const isExpanded = expandedParentIds.has(outcome.outcome_id)}
			{@const outcomeRatio = outcomeContribRatios[outcome.outcome_id]}
			{@const outcomeSeries = miniChartSeries(outcomeAggregateTimelineCounts[outcome.outcome_id] ?? [], 16)}
			<li
				class="hierarchy-item"
				class:child-node={isChildNode}
				class:selected={selectedOutcomeId === outcome.outcome_id}
				class:branch-expanded={children.length > 0 && isExpanded}
				style="--depth: {depth}; --level: {globalDepth};"
				use:trackHierarchyItem={{ outcomeId: outcome.outcome_id }}
				role="treeitem"
				aria-selected={selectedOutcomeId === outcome.outcome_id}
				aria-expanded={children.length > 0 ? isExpanded : undefined}
				tabindex="0"
				onclick={() => selectOutcomeAndIntent(outcome.outcome_id)}
				onkeydown={(e) => e.key === 'Enter' && selectOutcomeAndIntent(outcome.outcome_id)}
			>
				<div class="item-header-row">
					{#if children.length > 0}
						<button
							type="button"
							class="expand-btn"
							class:expand-btn--open={isExpanded}
							aria-label={isExpanded ? `Collapse ${children.length} child goals` : `Expand ${children.length} child goals`}
							onclick={(e) => toggleSubHierarchy(outcome.outcome_id, e)}
						>
							<span class="expand-btn__chevron" aria-hidden="true">{isExpanded ? '▼' : '▶'}</span>
						</button>
					{:else}
						<span class="expand-placeholder" aria-hidden="true"></span>
					{/if}
					<span class="outcome-num depth-{depth}">{num}</span>
					<span class="outcome-title" title={outcome.outcome ?? outcome.outcome_id}>{stripTrailingParen(outcome.outcome ?? outcome.outcome_id)}</span>
				</div>
				{#if children.length > 0}
					<div class="item-detail-row">
						<span class="child-count-note" class:child-count-note--open={isExpanded}>
							{#if isExpanded}
								<span class="child-count-badge child-count-badge--open">Open</span>
							{:else}
								<span class="child-count-badge">Hidden</span>
							{/if}
							<span class="child-count-text">{children.length} child goal{children.length === 1 ? '' : 's'}</span>
						</span>
					</div>
				{/if}
				{#if outcomeRatio && (outcomeRatio.hasShaper || outcomeRatio.hasExecutor)}
					<button
						type="button"
						class="outcome-contrib-card chart-expand-btn"
						title="Click to expand chart"
						onclick={(e) => openChartPopup({
							label: stripTrailingParen(outcome.outcome ?? outcome.outcome_id),
							sublabel: num,
							series: outcomeSeries,
							userShaper: outcomeRatio.userShaper,
							assistantShaper: outcomeRatio.assistantShaper,
							userExecutor: outcomeRatio.userExecutor,
							assistantExecutor: outcomeRatio.assistantExecutor
						}, e)}
					>
						<div class="outcome-contrib-summary">{outcomeContributionSummary(outcomeRatio)}</div>
						<div class="outcome-contrib-rows">
							<div class="mini-chart-row outcome-card-chart" title="Shaper · {peerNames ? peerNames.a : 'User'} {Math.round(outcomeRatio.userShaper * 100)}% / {peerNames ? peerNames.b : 'Assistant'} {Math.round(outcomeRatio.assistantShaper * 100)}%">
								<span class="mini-role-emoji" aria-hidden="true">💡</span>
								<span class="mini-ratio-bar" style="--u:{outcomeRatio.userShaper};--a:{outcomeRatio.assistantShaper};--cu:{peerNames ? PEER_A_COLOR : '#3b82f6'};--ca:{peerNames ? PEER_B_COLOR : '#16a34a'}"></span>
								<svg class="mini-chart role-chart" viewBox="0 0 96 12" preserveAspectRatio="none" width="96" height="12">
									<rect x="0" y="0" width="96" height="12" fill="#f8fafc"/>
									<path d={linePath(outcomeSeries.map((p) => p.userShaper), 96, 12)} stroke={peerNames ? PEER_A_COLOR : '#3b82f6'} stroke-width="1.4" fill="none"/>
									<path d={linePath(outcomeSeries.map((p) => p.assistantShaper), 96, 12)} stroke={peerNames ? PEER_B_COLOR : '#16a34a'} stroke-width="1.4" fill="none"/>
								</svg>
								<span class="outcome-contrib-pct">{Math.round(outcomeRatio.userShaper * 100)}/{Math.round(outcomeRatio.assistantShaper * 100)}</span>
							</div>
							<div class="mini-chart-row outcome-card-chart" title="Executor · {peerNames ? peerNames.a : 'User'} {Math.round(outcomeRatio.userExecutor * 100)}% / {peerNames ? peerNames.b : 'Assistant'} {Math.round(outcomeRatio.assistantExecutor * 100)}%">
								<span class="mini-role-emoji" aria-hidden="true">🔧</span>
								<span class="mini-ratio-bar" style="--u:{outcomeRatio.userExecutor};--a:{outcomeRatio.assistantExecutor};--cu:{peerNames ? PEER_A_COLOR : '#3b82f6'};--ca:{peerNames ? PEER_B_COLOR : '#16a34a'}"></span>
								<svg class="mini-chart role-chart" viewBox="0 0 96 10" preserveAspectRatio="none" width="96" height="10">
									<rect x="0" y="0" width="96" height="10" fill="#f8fafc"/>
									<path d={linePath(outcomeSeries.map((p) => p.userExecutor), 96, 10)} stroke={peerNames ? PEER_A_COLOR : '#3b82f6'} stroke-width="1.2" fill="none" opacity="0.72"/>
									<path d={linePath(outcomeSeries.map((p) => p.assistantExecutor), 96, 10)} stroke={peerNames ? PEER_B_COLOR : '#16a34a'} stroke-width="1.2" fill="none" opacity="0.72"/>
								</svg>
								<span class="outcome-contrib-pct">{Math.round(outcomeRatio.userExecutor * 100)}/{Math.round(outcomeRatio.assistantExecutor * 100)}</span>
							</div>
						</div>
					</button>
				{/if}
			</li>
		{/each}
	</ul>
	</div>
</div>

{#if chartPopup}
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="chart-modal-backdrop" role="dialog" aria-modal="true" aria-label="Chart detail" tabindex="-1" onclick={closeChartPopup}>
	<div class="chart-modal" role="presentation" onclick={(e) => e.stopPropagation()}>
		<div class="chart-modal-header">
			<div class="chart-modal-title-group">
				<span class="chart-modal-title">{chartPopup.label}</span>
				{#if chartPopup.sublabel}
					<span class="chart-modal-sublabel">{chartPopup.sublabel}</span>
				{/if}
			</div>
			<button type="button" class="chart-modal-close" onclick={closeChartPopup} aria-label="Close chart">✕</button>
		</div>

		<div class="chart-modal-body">
			<div class="chart-modal-section">
				<div class="chart-modal-section-header">
					<span class="chart-modal-role-icon" aria-hidden="true">💡</span>
					<span class="chart-modal-role-label">Shaper</span>
					<span class="chart-modal-pct" style="color:{peerNames ? PEER_A_COLOR : '#3b82f6'}">{peerNames ? peerNames.a : 'User'} {Math.round(chartPopup.userShaper * 100)}%</span>
					<div class="chart-modal-ratio-bar" style="--u:{chartPopup.userShaper};--a:{chartPopup.assistantShaper};--cu:{peerNames ? PEER_A_COLOR : '#3b82f6'};--ca:{peerNames ? PEER_B_COLOR : '#16a34a'}"></div>
					<span class="chart-modal-pct" style="color:{peerNames ? PEER_B_COLOR : '#16a34a'}">{peerNames ? peerNames.b : 'Asst'} {Math.round(chartPopup.assistantShaper * 100)}%</span>
				</div>
				<svg class="chart-modal-svg" viewBox="0 0 320 60" preserveAspectRatio="none" width="320" height="60">
					<rect x="0" y="0" width="320" height="60" fill="#f1f5f9" rx="4"/>
					<path d={linePath(chartPopup.series.map((p) => p.userShaper), 320, 60)} stroke={peerNames ? PEER_A_COLOR : '#3b82f6'} stroke-width="2" fill="none"/>
					<path d={linePath(chartPopup.series.map((p) => p.assistantShaper), 320, 60)} stroke={peerNames ? PEER_B_COLOR : '#16a34a'} stroke-width="2" fill="none"/>
				</svg>
			</div>

			<div class="chart-modal-section">
				<div class="chart-modal-section-header">
					<span class="chart-modal-role-icon" aria-hidden="true">🔧</span>
					<span class="chart-modal-role-label">Executor</span>
					<span class="chart-modal-pct" style="color:{peerNames ? PEER_A_COLOR : '#3b82f6'}">{peerNames ? peerNames.a : 'User'} {Math.round(chartPopup.userExecutor * 100)}%</span>
					<div class="chart-modal-ratio-bar" style="--u:{chartPopup.userExecutor};--a:{chartPopup.assistantExecutor};--cu:{peerNames ? PEER_A_COLOR : '#3b82f6'};--ca:{peerNames ? PEER_B_COLOR : '#16a34a'}"></div>
					<span class="chart-modal-pct" style="color:{peerNames ? PEER_B_COLOR : '#16a34a'}">{peerNames ? peerNames.b : 'Asst'} {Math.round(chartPopup.assistantExecutor * 100)}%</span>
				</div>
				<svg class="chart-modal-svg" viewBox="0 0 320 60" preserveAspectRatio="none" width="320" height="60">
					<rect x="0" y="0" width="320" height="60" fill="#f8fafc" rx="4"/>
					<path d={linePath(chartPopup.series.map((p) => p.userExecutor), 320, 60)} stroke={peerNames ? PEER_A_COLOR : '#3b82f6'} stroke-width="2" fill="none" opacity="0.8"/>
					<path d={linePath(chartPopup.series.map((p) => p.assistantExecutor), 320, 60)} stroke={peerNames ? PEER_B_COLOR : '#16a34a'} stroke-width="2" fill="none" opacity="0.8"/>
				</svg>
			</div>

			<div class="chart-modal-legend">
				<span class="chart-modal-legend-dot" style="background:{peerNames ? PEER_A_COLOR : '#3b82f6'}"></span>
				<span class="chart-modal-legend-text">{peerNames ? peerNames.a : 'User'}</span>
				<span class="chart-modal-legend-dot" style="background:{peerNames ? PEER_B_COLOR : '#16a34a'}; margin-left: 0.75rem;"></span>
				<span class="chart-modal-legend-text">{peerNames ? peerNames.b : 'Assistant'}</span>
				<span class="chart-modal-legend-hint">Each point = a segment of the conversation</span>
			</div>
			<div class="chart-modal-explain">
				<div class="chart-modal-explain-item"><strong>X-axis:</strong> conversation time (earlier turns on the left, later turns on the right)</div>
				<div class="chart-modal-explain-item"><strong>Top row (💡 Shaper):</strong> framing goals, direction, and ideas</div>
				<div class="chart-modal-explain-item"><strong>Bottom row (🔧 Executor):</strong> execution and implementation actions</div>
			</div>
		</div>
	</div>
</div>
{/if}

<style>
	.chart-expand-btn {
		cursor: pointer;
		background: none;
		border: none;
		padding: 0;
		text-align: left;
		font: inherit;
		transition: filter 0.12s;
	}
	.chart-expand-btn:hover {
		filter: brightness(0.93);
		outline: 1.5px dashed #94a3b8;
		outline-offset: 2px;
		border-radius: 4px;
	}

	/* Modal */
	.chart-modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(15, 23, 42, 0.45);
		z-index: 9999;
		display: flex;
		align-items: center;
		justify-content: center;
		backdrop-filter: blur(2px);
	}
	.chart-modal {
		background: #ffffff;
		border-radius: 16px;
		box-shadow: 0 24px 56px rgba(15, 23, 42, 0.22);
		padding: 1.5rem;
		min-width: 380px;
		max-width: 460px;
		width: 100%;
		display: flex;
		flex-direction: column;
		gap: 1.1rem;
	}
	.chart-modal-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
	}
	.chart-modal-title-group {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}
	.chart-modal-title {
		font-size: 0.97rem;
		font-weight: 700;
		color: #1e293b;
		line-height: 1.3;
	}
	.chart-modal-sublabel {
		font-size: 0.74rem;
		color: #64748b;
		font-weight: 500;
	}
	.chart-modal-close {
		background: none;
		border: none;
		cursor: pointer;
		font-size: 1rem;
		color: #94a3b8;
		padding: 0.15rem 0.35rem;
		border-radius: 6px;
		flex-shrink: 0;
		line-height: 1;
		transition: background 0.1s, color 0.1s;
	}
	.chart-modal-close:hover {
		background: #f1f5f9;
		color: #334155;
	}
	.chart-modal-body {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.chart-modal-section {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
	}
	.chart-modal-section-header {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.chart-modal-role-icon {
		font-size: 0.85rem;
	}
	.chart-modal-role-label {
		font-size: 0.77rem;
		font-weight: 700;
		color: #334155;
		margin-right: 0.25rem;
	}
	.chart-modal-pct {
		font-size: 0.72rem;
		font-weight: 600;
		white-space: nowrap;
	}
	.chart-modal-ratio-bar {
		flex: 1;
		height: 6px;
		border-radius: 3px;
		overflow: hidden;
		background: linear-gradient(
			to right,
			var(--cu, #3b82f6) 0%,
			var(--cu, #3b82f6) calc(var(--u) / (var(--u) + var(--a)) * 100%),
			var(--ca, #16a34a) calc(var(--u) / (var(--u) + var(--a)) * 100%),
			var(--ca, #16a34a) 100%
		);
	}
	.chart-modal-svg {
		width: 100%;
		height: 60px;
		border-radius: 6px;
		border: 1px solid #e2e8f0;
		display: block;
	}
	.chart-modal-legend {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		padding-top: 0.25rem;
		border-top: 1px solid #f1f5f9;
	}
	.chart-modal-legend-dot {
		width: 10px;
		height: 10px;
		border-radius: 50%;
		flex-shrink: 0;
	}
	.chart-modal-legend-text {
		font-size: 0.72rem;
		font-weight: 600;
		color: #475569;
	}
	.chart-modal-legend-hint {
		margin-left: auto;
		font-size: 0.67rem;
		color: #94a3b8;
		font-style: italic;
	}
	.chart-modal-explain {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		padding-top: 0.45rem;
		border-top: 1px solid #f1f5f9;
	}
	.chart-modal-explain-item {
		font-size: 0.72rem;
		line-height: 1.35;
		color: #475569;
	}

	.outcome-hierarchy-with-timeline {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 0.85rem;
		background: #ffffff;
		border: 1px solid #e5e7eb;
		border-radius: 18px;
		min-width: 0;
		min-height: 0;
		flex: 1 1 0;
		overflow: hidden;
		box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
	}
	.hierarchy-and-strip {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		min-height: 0;
		flex: 1;
		min-width: 0;
	}
	.mini-chart {
		flex-shrink: 0;
		display: block;
		border-radius: 2px;
		overflow: visible;
		align-self: center;
		background: #f1f5f9;
		border: 1px solid #e2e8f0;
	}
	.mini-chart-stack {
		display: inline-flex;
		flex-direction: column;
		gap: 2px;
		flex-shrink: 0;
	}
	.mini-chart-row {
		display: inline-flex;
		align-items: center;
		gap: 2px;
	}
	.mini-role-emoji {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 9px;
		font-size: 0.48rem;
		line-height: 1;
		opacity: 0.82;
		flex-shrink: 0;
	}
	.mini-chart-row {
		align-self: stretch;
	}
	.mini-ratio-bar {
		display: inline-flex;
		flex-shrink: 0;
		width: 4px;
		align-self: stretch;
		min-height: 10px;
		border-radius: 1px;
		overflow: hidden;
		background: linear-gradient(
			to bottom,
			var(--cu, #3b82f6) 0%,
			var(--cu, #3b82f6) calc(var(--u) * 100%),
			var(--ca, #16a34a) calc(var(--u) * 100%),
			var(--ca, #16a34a) 100%
		);
	}
	.mini-chart.role-chart {
		border-color: #dbeafe;
		background: #f8fafc;
	}
	.chart-legend {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
		padding: 0.55rem 0.75rem;
		margin: 0;
		background: #f8fafc;
		border: 1px solid #e5e7eb;
		border-radius: 14px;
		flex-shrink: 0;
		flex-wrap: wrap;
	}
	.chart-legend::before {
		content: 'GOALS · How your goals evolve';
		font-size: 0.72rem;
		font-weight: 800;
		letter-spacing: 0.08em;
		color: #64748b;
		margin-right: auto;
	}
	.legend-section {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
	}
	.legend-line {
		display: inline-block;
		width: 14px;
		height: 2px;
		border-radius: 1px;
	}
	.legend-label {
		font-size: 0.64rem;
		font-weight: 600;
		color: #475569;
	}
	.legend-emoji {
		font-size: 0.55rem;
		line-height: 1;
	}
	.legend-divider {
		width: 1px;
		height: 12px;
		background: #cbd5e1;
		flex-shrink: 0;
	}
	.hierarchy-list {
		list-style: none;
		margin: 0;
		padding: 0;
		flex: 1 1 0;
		min-width: 0;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
		align-items: stretch;
		gap: 1rem;
	}
	.intent-group,
	.intent-group-header {
		margin: 0;
	}
	.intent-toggle-chevron {
		font-size: 0.6rem;
		flex-shrink: 0;
		color: #64748b;
	}
	.cross-intent-item {
		opacity: 0.9;
	}
	.cross-intent-subheader {
		padding: 0.25rem 0.5rem;
		padding-left: calc(0.25rem + var(--depth, 1) * 0.4rem);
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-top: 0.35rem;
		margin-bottom: 0.1rem;
	}
	.cross-sub-id {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 2rem;
		height: 1.1rem;
		padding: 0 0.35rem;
		border-radius: 999px;
		font-size: 0.62rem;
		font-weight: 700;
		color: #1e3a8a;
		background: #dbeafe;
		border: 1px solid #93c5fd;
		flex-shrink: 0;
	}
	.cross-sub-label {
		font-size: 0.72rem;
		font-weight: 700;
		color: #1e3a8a;
		word-wrap: break-word;
		overflow-wrap: break-word;
	}
	.intent-header {
		margin: 0;
		font-size: 0.76rem;
		font-weight: 700;
		line-height: 1.35;
		color: #334155;
		word-wrap: break-word;
		overflow-wrap: break-word;
		min-width: 0;
	}
	.intent-header-row {
		display: flex;
		align-items: flex-start;
		gap: 0.5rem;
		margin: 0;
	}
	.intent-detail-row {
		display: flex;
		align-items: center;
		gap: 0.45rem;
		padding: 0 0.9rem 0.8rem 2.9rem;
		flex-wrap: wrap;
	}
	.intent-header-btn {
		padding: 0.85rem 0.9rem 0.45rem;
		margin: 0;
		border: 1px solid #e5e7eb;
		background: #ffffff;
		border-radius: 16px 16px 0 0;
		cursor: pointer;
		text-align: left;
		flex: 1;
		min-width: 0;
		display: inline-flex;
		align-items: flex-start;
		gap: 0.4rem;
		box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04);
	}
	.intent-id {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 1.45rem;
		height: 1.1rem;
		padding: 0 0.35rem;
		border-radius: 999px;
		font-size: 0.64rem;
		font-weight: 700;
		color: #1e3a8a;
		background: #dbeafe;
		border: 1px solid #93c5fd;
		flex-shrink: 0;
	}
	.intent-header-btn:hover {
		background: #f8fafc;
	}
	.intent-header-btn.selected {
		background: #eff6ff;
		border-color: #93c5fd;
	}
	.intent-count {
		font-size: 0.68rem;
		font-weight: 600;
		color: #64748b;
		white-space: nowrap;
	}
	.intent-outcome-list {
		list-style: none;
		margin: 0;
		padding: 0.55rem 0.75rem 0.8rem;
		display: flex;
		flex-direction: column;
		gap: 0.55rem;
	}
	.hierarchy-item {
		position: relative;
		/* Narrow deeper nodes to make hierarchy depth obvious at a glance. */
		--node-offset: min(calc(var(--level, 0) * 0.6rem), 1.8rem);
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0.8rem 0.85rem;
		padding-left: calc(0.75rem + var(--depth, 0) * 0.55rem);
		margin-left: var(--node-offset);
		margin-top: 0;
		margin-right: 0;
		margin-bottom: 0;
		flex-shrink: 0;
		width: calc(100% - var(--node-offset));
		min-width: 0;
		box-sizing: border-box;
		/* Never let flex compress cards when many goals share a short viewport — scroll instead. */
		min-height: 4.5rem;
		border-radius: 14px;
		cursor: pointer;
		border: 1px solid #e5e7eb;
		border-left: 4px solid #cbd5e1;
		background: #ffffff;
		box-shadow: 0 4px 12px rgba(15, 23, 42, 0.04);
		transition:
			border-color 0.15s ease,
			box-shadow 0.15s ease,
			background 0.15s ease;
	}
	.hierarchy-item.child-node {
		padding-left: calc(0.75rem + var(--depth, 0) * 0.62rem);
	}
	.hierarchy-item.child-node::before {
		content: '';
		position: absolute;
		left: calc(0.62rem + var(--level, 1) * 0.62rem - 0.42rem);
		top: -0.45rem;
		bottom: 0;
		width: 1px;
		background: #dbeafe;
		pointer-events: none;
	}
	.hierarchy-item.child-node .item-header-row::before {
		content: '';
		display: inline-block;
		width: 0.36rem;
		height: 1px;
		background: #bfdbfe;
		flex-shrink: 0;
		margin-right: 0.04rem;
	}
	.item-header-row {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		min-width: 0;
	}
	.item-detail-row {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		padding-left: 1.2rem;
		flex-wrap: wrap;
	}
	.outcome-contrib-card {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin-left: 1.2rem;
		padding: 0.5rem 0.6rem;
		border: 1px solid #e5e7eb;
		border-radius: 12px;
		background: #f8fafc;
		flex-shrink: 0;
		align-self: stretch;
	}
	.outcome-contrib-summary {
		font-size: 0.68rem;
		font-weight: 600;
		color: #475569;
		line-height: 1.35;
	}
	.outcome-contrib-rows {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.outcome-card-chart {
		gap: 0.3rem;
	}
	.outcome-card-chart .mini-chart {
		width: 96px;
		flex: 0 0 auto;
	}
	.outcome-contrib-pct {
		font-size: 0.62rem;
		font-weight: 700;
		color: #64748b;
		min-width: 2.3rem;
		text-align: right;
	}
	.mini-dot {
		position: absolute;
		top: 50%;
		transform: translate(-50%, -50%);
		width: 2px;
		height: 8px;
		border-radius: 1px;
		pointer-events: none;
	}
	.mini-dot.shaper-only {
		background: #3b82f6;
	}
	.mini-dot.executor-only {
		background: #6366f1;
	}
	.mini-dot.both {
		background: linear-gradient(to bottom, #3b82f6 50%, #6366f1 50%);
	}
	.hierarchy-item:hover {
		background: #f8fafc;
		border-color: #dbe4ee;
	}
	.hierarchy-item.selected {
		background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
		border: 2px solid #3b82f6;
		border-left: 5px solid #1d4ed8;
		box-shadow:
			0 0 0 1px rgba(37, 99, 235, 0.25),
			0 8px 22px rgba(37, 99, 235, 0.18);
	}
	.hierarchy-item.selected .outcome-title {
		font-weight: 700;
		color: #0f172a;
	}
	.hierarchy-item.branch-expanded:not(.selected) {
		border-color: #e0e7ff;
		box-shadow: inset 0 -2px 0 0 #c7d2fe;
	}
	.expand-btn {
		flex-shrink: 0;
		min-width: 1.85rem;
		min-height: 1.85rem;
		width: 1.85rem;
		height: 1.85rem;
		padding: 0;
		border: 1px solid #cbd5e1;
		border-radius: 10px;
		background: #f8fafc;
		cursor: pointer;
		color: #475569;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
		transition:
			background 0.12s ease,
			border-color 0.12s ease,
			color 0.12s ease,
			box-shadow 0.12s ease;
	}
	.expand-btn:hover {
		background: #f1f5f9;
		border-color: #94a3b8;
		color: #0f172a;
	}
	.expand-btn:focus-visible {
		outline: 2px solid #3b82f6;
		outline-offset: 2px;
	}
	.expand-btn--open {
		background: #dbeafe;
		border-color: #60a5fa;
		color: #1e40af;
		box-shadow: 0 1px 3px rgba(37, 99, 235, 0.2);
	}
	.expand-btn--open:hover {
		background: #bfdbfe;
		border-color: #3b82f6;
		color: #1e3a8a;
	}
	.expand-btn__chevron {
		font-size: 0.72rem;
		line-height: 1;
		font-weight: 800;
		letter-spacing: -0.02em;
	}
	.expand-placeholder {
		display: inline-block;
		width: 1.85rem;
		height: 1.85rem;
		flex-shrink: 0;
	}
	.outcome-num {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 1.45rem;
		height: 1.1rem;
		padding: 0 0.35rem;
		border-radius: 9999px;
		font-weight: 700;
		font-size: 0.64rem;
		color: #ffffff;
		background: linear-gradient(180deg, #60a5fa 0%, #2563eb 100%);
		border: 1px solid #3b82f6;
		flex-shrink: 0;
		white-space: nowrap;
		box-shadow: 0 2px 6px rgba(37, 99, 235, 0.2);
	}
	.hierarchy-item.selected .outcome-num {
		background: linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%);
		color: #ffffff;
		border-color: #2563eb;
	}
	.outcome-num.depth-0 {
		color: #ffffff;
		background: linear-gradient(180deg, #60a5fa 0%, #2563eb 100%);
		border-color: #3b82f6;
	}
	.outcome-num.depth-1 {
		color: #eff6ff;
		background: linear-gradient(180deg, #93c5fd 0%, #3b82f6 100%);
		border-color: #60a5fa;
	}
	.outcome-num.depth-2 {
		color: #eff6ff;
		background: linear-gradient(180deg, #bfdbfe 0%, #60a5fa 100%);
		border-color: #93c5fd;
	}
	.outcome-num.depth-3 {
		color: #eff6ff;
		background: linear-gradient(180deg, #dbeafe 0%, #93c5fd 100%);
		border-color: #bfdbfe;
	}
	.outcome-title {
		font-size: 0.84rem;
		line-height: 1.35;
		color: #111827;
		flex: 1;
		min-width: 0;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.child-count-note {
		display: inline-flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.35rem;
		font-size: 0.66rem;
		font-weight: 600;
		line-height: 1.2;
		color: #64748b;
		letter-spacing: 0.01em;
	}
	.child-count-badge {
		display: inline-flex;
		align-items: center;
		padding: 0.12rem 0.45rem;
		border-radius: 999px;
		font-size: 0.6rem;
		font-weight: 800;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		background: #f1f5f9;
		color: #64748b;
		border: 1px solid #e2e8f0;
	}
	.child-count-badge--open {
		background: #dbeafe;
		color: #1e40af;
		border-color: #93c5fd;
	}
	.child-count-text {
		font-weight: 600;
		color: #64748b;
	}
	.child-count-note--open .child-count-text {
		color: #334155;
	}
	.req-rate-badge {
		flex-shrink: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 1.45rem;
		height: 1.1rem;
		font-size: 0.64rem;
		font-weight: 700;
		color: #2f7a57;
		background: #dff4e9;
		border: 1px solid #9dd2b7;
		border-radius: 9999px;
		padding: 0 0.35rem;
		white-space: nowrap;
	}
	.req-rate-sub {
		width: 2.2rem;
		min-width: 2.2rem;
		height: 0.9rem;
		font-size: 0.54rem;
		padding: 0 0.25rem;
		color: #2f7a57;
		background: #dff4e9;
		border: 1px solid #9dd2b7;
		border-radius: 9999px;
		box-sizing: border-box;
		text-align: center;
	}
	.req-rate-empty {
		color: #cbd5e1;
		background: #f8fafc;
		border-color: #e2e8f0;
	}
</style>
