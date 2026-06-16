<script lang="ts">
	import type { OutcomeNode } from '$lib/types';
	import type { ContribBarDisplay } from '$lib/contribPeerBar';
	import { isAssistantSpeakerId } from '$lib/speakerUtils';
	import { isEunsuSpeakerName } from '$lib/contribPeerBar';
	import type { RequirementActionMap, UtteranceListData, ActionUtteranceMap } from '$lib/data/dataLoader';
	import {
		requirementIdsEqual,
		buildRequirementStatusIndex,
		getRequirementStatusKind,
		getRequirementStatusLabel,
		sortRequirementChainsByCreationTurn,
		resolveContribBarByReqId,
		getRequirementLabel as getRequirementEventLabel,
		getExecutedTurn,
		getDismissedTurn,
		isRequirementDismissed,
		chainHasRevisionHistory,
		getRequirementActionEntry,
		getTurnFromActionId,
		goalLabel,
		getIndirectInfluenceEvents,
		indirectEventKey,
		timelineClusterKey
	} from '$lib/requirementsPanelUtils';

	export type RequirementChainItem = {
		currentId: string;
		currentText: string;
		history: { id: string; text: string }[];
	};

	type ReqContrib = {
		user: number;
		assistant: number;
		userShaper: number;
		assistantShaper: number;
		userShaperDir?: number;
		userShaperInd?: number;
		assistantShaperDir?: number;
		assistantShaperInd?: number;
		userExecutor: number;
		assistantExecutor: number;
	};

	let {
		outcome,
		requirementChains = [],
		requirementContributionByReqId = {},
		contribBarByReqId = {},
		requirementStatusOverview = [],
		utteranceList = null,
		actionUtteranceMap = {},
		requirementCreationTurnByReqId = {},
		requirementActionMap = {},
		finalTurn = 1,
		childGoalStarts = [],
		selectedRequirementId = null,
		revisionFocusVersionId = null,
		onRequirementClick,
		onRevisionVersionFocus,
		onChildGoalClick,
		onTurnFocus,
		onPanelRowHover
	}: {
		outcome: OutcomeNode;
		requirementChains?: RequirementChainItem[];
		requirementContributionByReqId?: Record<string, ReqContrib>;
		/** Per-requirement bar: Slack teammates (Eunsu excluded from the two-color split) vs legacy user/assistant. */
		contribBarByReqId?: Record<string, ContribBarDisplay>;
		requirementStatusOverview?: Array<{
			id: string;
			is_executed: boolean | null;
			is_dismissed: boolean | null;
			dismissed_at_turn: number | null;
			dismissed_by_action_ids?: string[];
		}>;
		utteranceList?: UtteranceListData | null;
		actionUtteranceMap?: ActionUtteranceMap;
		requirementCreationTurnByReqId?: Record<string, number>;
		requirementActionMap?: RequirementActionMap;
		finalTurn?: number;
		childGoalStarts?: Array<{ goalId: string; goalTitle: string; turn: number }>;
		selectedRequirementId?: string | null;
		/** Earlier version id when user is viewing original requirement actions. */
		revisionFocusVersionId?: string | null;
		onRequirementClick?: (reqId: string) => void;
		onRevisionVersionFocus?: (versionId: string | null, turn?: number | null) => void;
		onChildGoalClick?: (goalId: string) => void;
		onTurnFocus?: (turn: number | null) => void;
		/** Hover over any requirement row highlights this turn in the chat log (no click). */
		onPanelRowHover?: (turn: number | null) => void;
	} = $props();

	/**
	 * Vertical timeline axis (T1–TN): span matches the visible requirements list pane top→bottom
	 * (not scroll/content height). Top/bottom reserves clear the T1 / Tn edge labels.
	 */
	const TIMELINE_AXIS_TOP_PX = 22;
	const TIMELINE_AXIS_BOTTOM_RESERVE_PX = 26;
	const TIMELINE_AXIS_MIN_PX = 72;
	const LIST_VIEWPORT_FALLBACK_PX = 520;

	let requirementsContentEl = $state<HTMLDivElement | null>(null);
	let requirementsListEl = $state<HTMLDivElement | null>(null);
	let timelineRailEl = $state<HTMLDivElement | null>(null);
	let connectorsOverlayEl = $state<HTMLDivElement | null>(null);

	type ConnectorViewportSeg = {
		key: string;
		d: string;
		selected: boolean;
		executed: boolean;
		indirect: boolean;
		dismissed: boolean;
		revised: boolean;
	};

	let connectorViewportPaths = $state<ConnectorViewportSeg[]>([]);
	let layoutVersion = $state(0);
	let expandedIndirectKeys = $state<Record<string, boolean>>({});
	const requirementRowEls = new Map<string, HTMLElement>();
	const indirectCardEls = new Map<string, HTMLElement>();
	const executedCardEls = new Map<string, HTMLElement>();
	const dismissedCardEls = new Map<string, HTMLElement>();

	let _bumpRaf = 0;
	function bumpLayout() {
		if (_bumpRaf) return;
		_bumpRaf = requestAnimationFrame(() => {
			_bumpRaf = 0;
			layoutVersion += 1;
		});
	}

	/** Geometry of a row card in `requirements-content` space (handles `position:relative` shells). */
	function layoutMetricsInContent(contentEl: HTMLElement, el: HTMLElement): { centerY: number; leftX: number } {
		const cr = contentEl.getBoundingClientRect();
		const er = el.getBoundingClientRect();
		return {
			centerY: er.top - cr.top + er.height / 2,
			leftX: er.left - cr.left
		};
	}

	function observeLayout(el: HTMLElement) {
		bumpLayout();
		let ro: ResizeObserver | null = null;
		if (typeof ResizeObserver !== 'undefined') {
			ro = new ResizeObserver(() => bumpLayout());
			ro.observe(el);
		}
		return {
			destroy() {
				ro?.disconnect();
			}
		};
	}

	function trackRequirementRow(el: HTMLElement, params: { key: string }) {
		requirementRowEls.set(params.key, el);
		bumpLayout();
		let ro: ResizeObserver | null = null;
		if (typeof ResizeObserver !== 'undefined') {
			ro = new ResizeObserver(() => bumpLayout());
			ro.observe(el);
		}
		return {
			update(next: { key: string }) {
				if (next.key !== params.key) {
					requirementRowEls.delete(params.key);
					requirementRowEls.set(next.key, el);
				}
				params = next;
				bumpLayout();
			},
			destroy() {
				requirementRowEls.delete(params.key);
				ro?.disconnect();
				bumpLayout();
			}
		};
	}

	function trackIndirectCard(el: HTMLElement, params: { key: string }) {
		indirectCardEls.set(params.key, el);
		bumpLayout();
		let ro: ResizeObserver | null = null;
		if (typeof ResizeObserver !== 'undefined') {
			ro = new ResizeObserver(() => bumpLayout());
			ro.observe(el);
		}
		return {
			update(next: { key: string }) {
				if (next.key !== params.key) {
					indirectCardEls.delete(params.key);
					indirectCardEls.set(next.key, el);
				}
				params = next;
				bumpLayout();
			},
			destroy() {
				indirectCardEls.delete(params.key);
				ro?.disconnect();
				bumpLayout();
			}
		};
	}

	function trackExecutedCard(el: HTMLElement, params: { key: string }) {
		executedCardEls.set(params.key, el);
		bumpLayout();
		let ro: ResizeObserver | null = null;
		if (typeof ResizeObserver !== 'undefined') {
			ro = new ResizeObserver(() => bumpLayout());
			ro.observe(el);
		}
		return {
			update(next: { key: string }) {
				if (next.key !== params.key) {
					executedCardEls.delete(params.key);
					executedCardEls.set(next.key, el);
				}
				params = next;
				bumpLayout();
			},
			destroy() {
				executedCardEls.delete(params.key);
				ro?.disconnect();
				bumpLayout();
			}
		};
	}

	function trackDismissedCard(el: HTMLElement, params: { key: string }) {
		dismissedCardEls.set(params.key, el);
		bumpLayout();
		let ro: ResizeObserver | null = null;
		if (typeof ResizeObserver !== 'undefined') {
			ro = new ResizeObserver(() => bumpLayout());
			ro.observe(el);
		}
		return {
			update(next: { key: string }) {
				if (next.key !== params.key) {
					dismissedCardEls.delete(params.key);
					dismissedCardEls.set(next.key, el);
				}
				params = next;
				bumpLayout();
			},
			destroy() {
				dismissedCardEls.delete(params.key);
				ro?.disconnect();
				bumpLayout();
			}
		};
	}

	const speakerByTurnId = $derived.by((): Map<number, string> => {
		const map = new Map<number, string>();
		for (const u of utteranceList?.utterances ?? []) {
			map.set(u.turn_id, u.speaker);
		}
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

	function speakerColor(speaker: string): string {
		if (!peerNames) return isAssistantSpeakerId(speaker) ? '#16a34a' : '#3b82f6';
		if (speaker === peerNames.a) return PEER_A_COLOR;
		if (speaker === peerNames.b) return PEER_B_COLOR;
		return '#94a3b8';
	}

	function circleStyle(speaker: string): string {
		const c = speakerColor(speaker);
		const filled = peerNames ? speaker === peerNames.b : isAssistantSpeakerId(speaker);
		return `border-color:${c};background:${filled ? c : 'transparent'}`;
	}

	function getSpeakerForTurn(turn: number | null | undefined): string {
		if (turn == null) return 'user';
		return speakerByTurnId.get(turn) ?? 'user';
	}

	const outcomeSpeaker = $derived(getSpeakerForTurn(outcome.created_at));
	const outcomeStartTurn = $derived(outcome.created_at ?? null);

	const requirementStatusById = $derived.by(() => {
		return buildRequirementStatusIndex(requirementStatusOverview ?? []);
	});

	function getReqStatusKind(reqId: string): 'done' | 'pending' | 'dismissed' | 'unknown' {
		return getRequirementStatusKind(requirementStatusById, reqId);
	}

	function getReqStatusLabel(reqId: string): string {
		return getRequirementStatusLabel(requirementStatusById, reqId);
	}

	const actionTurnById = $derived.by((): Record<string, number> => {
		const map: Record<string, number> = {};
		for (const [id, entry] of Object.entries(actionUtteranceMap ?? {})) {
			if (entry?.turn_id != null) map[id] = entry.turn_id;
		}
		return map;
	});

	function reqIsDismissed(reqId: string): boolean {
		return isRequirementDismissed(requirementStatusById, reqId);
	}

	function getReqDismissedTurn(reqId: string): number | null {
		return getDismissedTurn(requirementStatusById, reqId, actionTurnById);
	}

	const sortedChains = $derived.by(() => {
		return sortRequirementChainsByCreationTurn(requirementChains, requirementCreationTurnByReqId ?? {});
	});

	const totalRequirements = $derived(requirementChains.length);

	function resolveContribBar(reqId: string): ContribBarDisplay {
		return resolveContribBarByReqId(reqId, contribBarByReqId, requirementContributionByReqId);
	}

	function getRequirementLabel(chain: RequirementChainItem): string {
		return getRequirementEventLabel(chain.history.length);
	}

	function getSpeakerNearTurn(turn: number | null | undefined): string {
		if (turn == null) return 'user';
		return (
			speakerByTurnId.get(turn) ??
			speakerByTurnId.get(turn - 1) ??
			speakerByTurnId.get(turn + 1) ??
			'user'
		);
	}

	function getRequirementCreatorSpeaker(reqId: string, fallbackTurn: number | null): string {
		const entry = getRequirementActionEntry(requirementActionMap, reqId);
		const origin = Array.isArray(entry?.origin_actions) ? entry.origin_actions : [];
		const originTurns = origin
			.map((a) => getTurnFromActionId(a.action_id))
			.filter((turn): turn is number => turn != null);
		if (originTurns.length > 0) {
			const createdTurn = Math.min(...originTurns);
			return getSpeakerNearTurn(createdTurn);
		}
		return getSpeakerNearTurn(fallbackTurn);
	}

	function formatTurnLabel(turn: number | null | undefined): string {
		return turn == null ? 'T?' : `T${turn + 1}`;
	}

	function getIndirectEvents(reqId: string): Array<{ turn: number; explanation: string; speaker: string }> {
		return getIndirectInfluenceEvents(
			reqId,
			requirementActionMap,
			requirementCreationTurnByReqId,
			getSpeakerForTurn
		);
	}

	function getIndirectEventsForChain(
		chain: RequirementChainItem,
		selected: boolean
	): Array<{ turn: number; explanation: string; speaker: string }> {
		if (!selected) return getIndirectEvents(chain.currentId);
		if (
			revisionFocusVersionId != null &&
			chain.history.some((h) => h.id === revisionFocusVersionId)
		) {
			return getIndirectEvents(revisionFocusVersionId);
		}
		return getIndirectEvents(chain.currentId);
	}

	function toggleIndirectExpanded(key: string) {
		expandedIndirectKeys = { ...expandedIndirectKeys, [key]: !expandedIndirectKeys[key] };
		bumpLayout();
	}

	type TimelineEntry = {
		key: string;
		rowKey: string;
		reqId?: string;
		turn: number | null;
		speaker: string;
		selected: boolean;
		eventKind?: 'base' | 'executed' | 'indirect' | 'dismissed' | 'revise-origin';
		explanation?: string;
	};

	type TimelineMarkerLayout = TimelineEntry & {
		markerY: number;
		plotY: number;
		rowY: number;
		targetX: number;
		clusterKey: string;
		clusterSize: number;
	};

	/** Group axis dots that share the same turn without mixing outcome vs requirement rows. */

	type TimelineAxisCluster = {
		key: string;
		plotY: number;
		count: number;
		turn: number | null;
		anySelected: boolean;
		members: TimelineMarkerLayout[];
		dotColor: string;
		hasExecuted: boolean;
		hasIndirect: boolean;
		hasDismissed: boolean;
		hasRevised: boolean;
	};

	type DisplayRow =
		| {
				kind: 'indirect';
				key: string;
				reqId: string;
				turn: number;
				speaker: string;
				explanation: string;
		  }
		| {
				kind: 'outcome-start';
				key: 'outcome-start';
				turn: number | null;
		  }
		| {
				kind: 'child-goal-start';
				key: string;
				goalId: string;
				goalTitle: string;
				turn: number;
				speaker: string;
		  }
		| {
				kind: 'executed';
				key: string;
				reqId: string;
				turn: number;
				speaker: string;
		  }
		| {
				kind: 'dismissed';
				key: string;
				reqId: string;
				turn: number;
				speaker: string;
		  }
		| {
				kind: 'requirement-original';
				key: string;
				reqId: string;
				versionId: string;
				text: string;
				turn: number | null;
				chain: RequirementChainItem;
		  }
		| {
				kind: 'requirement';
				key: string;
				reqId: string;
				chain: RequirementChainItem;
				turn: number | null;
		  };

	const displayRows = $derived.by((): DisplayRow[] => {
		const rows: DisplayRow[] = [
			{
				kind: 'outcome-start',
				key: 'outcome-start',
				turn: outcomeStartTurn
			}
		];
		for (const child of childGoalStarts) {
			rows.push({
				kind: 'child-goal-start',
				key: `child-goal-start:${child.goalId}`,
				goalId: child.goalId,
				goalTitle: child.goalTitle,
				turn: child.turn,
				speaker: getSpeakerForTurn(child.turn)
			});
		}
		for (const chain of sortedChains) {
			const reqId = chain.currentId;
			const turn = requirementCreationTurnByReqId[reqId] ?? null;
			if (requirementIdsEqual(selectedRequirementId, reqId)) {
				for (const indirect of getIndirectEventsForChain(chain, true)) {
					rows.push({
						kind: 'indirect',
						key: indirectEventKey(reqId, indirect.turn, indirect.explanation),
						reqId,
						turn: indirect.turn,
						speaker: indirect.speaker,
						explanation: indirect.explanation
					});
				}
				if (chainHasRevisionHistory(chain)) {
					for (const earlier of chain.history) {
						const originTurn = requirementCreationTurnByReqId[earlier.id] ?? null;
						rows.push({
							kind: 'requirement-original',
							key: `req:${reqId}:original:${earlier.id}`,
							reqId,
							versionId: earlier.id,
							text: earlier.text,
							turn: originTurn,
							chain
						});
					}
				}
				const executedTurn = getExecutedTurn(reqId, requirementActionMap);
				if (executedTurn != null) {
					rows.push({
						kind: 'executed',
						key: `req:${reqId}:executed`,
						reqId,
						turn: executedTurn,
						speaker: getSpeakerForTurn(executedTurn)
					});
				}
				const dismissedTurn = getReqDismissedTurn(reqId);
				if (dismissedTurn != null) {
					rows.push({
						kind: 'dismissed',
						key: `req:${reqId}:dismissed`,
						reqId,
						turn: dismissedTurn,
						speaker: getSpeakerForTurn(dismissedTurn)
					});
				}
			}
			rows.push({
				kind: 'requirement',
				key: `req:${reqId}`,
				reqId,
				chain,
				turn
			});
		}
		const priority: Record<DisplayRow['kind'], number> = {
			indirect: 0,
			'outcome-start': 1,
			'child-goal-start': 2,
			'requirement-original': 2.8,
			requirement: 3,
			executed: 4,
			dismissed: 5
		};
		return rows.sort((a, b) => {
			const ta = a.turn ?? Number.POSITIVE_INFINITY;
			const tb = b.turn ?? Number.POSITIVE_INFINITY;
			if (ta !== tb) return ta - tb;
			const pa = priority[a.kind];
			const pb = priority[b.kind];
			if (pa !== pb) return pa - pb;
			return a.key.localeCompare(b.key);
		});
	});

	const timelineEntries = $derived.by((): TimelineEntry[] => [
		{
			key: 'outcome-start',
			rowKey: 'outcome-start',
			turn: outcomeStartTurn,
			speaker: outcomeSpeaker,
			selected: selectedRequirementId == null,
			eventKind: 'base'
		},
		...childGoalStarts.map((child) => ({
			key: `child-goal-start:${child.goalId}`,
			rowKey: `child-goal-start:${child.goalId}`,
			turn: child.turn,
			speaker: getSpeakerForTurn(child.turn),
			selected: selectedRequirementId == null,
			eventKind: 'base' as const
		})),
		...sortedChains.map((chain) => {
			const turn = requirementCreationTurnByReqId[chain.currentId] ?? null;
			const creatorSpeaker = getRequirementCreatorSpeaker(chain.currentId, turn);
			const entries: TimelineEntry[] = [
				{
					key: `req:${chain.currentId}`,
					rowKey: `req:${chain.currentId}`,
					reqId: chain.currentId,
					turn,
					speaker: creatorSpeaker,
					selected: requirementIdsEqual(selectedRequirementId, chain.currentId),
					eventKind: 'base'
				}
			];
			const executedTurn = getExecutedTurn(chain.currentId, requirementActionMap);
			if (requirementIdsEqual(selectedRequirementId, chain.currentId) && executedTurn != null) {
				entries.push({
					key: `req:${chain.currentId}:executed`,
					rowKey: `req:${chain.currentId}:executed`,
					reqId: chain.currentId,
					turn: executedTurn,
					speaker: getSpeakerForTurn(executedTurn),
					selected: true,
					eventKind: 'executed'
				});
			}
			const dismissedTurn = getReqDismissedTurn(chain.currentId);
			if (requirementIdsEqual(selectedRequirementId, chain.currentId) && dismissedTurn != null) {
				entries.push({
					key: `req:${chain.currentId}:dismissed`,
					rowKey: `req:${chain.currentId}:dismissed`,
					reqId: chain.currentId,
					turn: dismissedTurn,
					speaker: getSpeakerForTurn(dismissedTurn),
					selected: true,
					eventKind: 'dismissed'
				});
			}
			if (requirementIdsEqual(selectedRequirementId, chain.currentId) && chainHasRevisionHistory(chain)) {
				for (const earlier of chain.history) {
					const originTurn = requirementCreationTurnByReqId[earlier.id] ?? null;
					entries.push({
						key: `req:${chain.currentId}:revise-origin:${earlier.id}`,
						rowKey: `req:${chain.currentId}:original:${earlier.id}`,
						reqId: chain.currentId,
						turn: originTurn,
						speaker: getRequirementCreatorSpeaker(earlier.id, originTurn),
						selected: true,
						eventKind: 'revise-origin'
					});
				}
			}
			if (requirementIdsEqual(selectedRequirementId, chain.currentId)) {
				for (const indirect of getIndirectEventsForChain(chain, true)) {
					entries.push({
						key: indirectEventKey(chain.currentId, indirect.turn, indirect.explanation),
						rowKey: `req:${chain.currentId}`,
						reqId: chain.currentId,
						turn: indirect.turn,
						speaker: indirect.speaker,
						selected: true,
						eventKind: 'indirect',
						explanation: indirect.explanation
					});
				}
			}
			return entries;
		}).flat()
	]);

	const timelineMaxTurn = $derived.by(() => {
		const turns = timelineEntries
			.map((entry) => entry.turn)
			.filter((turn): turn is number => turn != null);
		return Math.max(finalTurn ?? 0, ...turns, 1);
	});

	/** Visible inner height of the scroll list (excludes list padding). */
	function listViewportContentHeightPx(): number {
		const list = requirementsListEl;
		if (!list || typeof list.clientHeight !== 'number') {
			return LIST_VIEWPORT_FALLBACK_PX;
		}
		const cs = getComputedStyle(list);
		const pt = parseFloat(cs.paddingTop) || 0;
		const pb = parseFloat(cs.paddingBottom) || 0;
		return Math.max(0, list.clientHeight - pt - pb);
	}

	/** Length of the time axis stroke (turn → Y mapping uses this span only). */
	const timelineAxisSpanPx = $derived.by(() => {
		layoutVersion;
		const port = listViewportContentHeightPx();
		const span =
			port - TIMELINE_AXIS_TOP_PX - TIMELINE_AXIS_BOTTOM_RESERVE_PX;
		return Math.max(TIMELINE_AXIS_MIN_PX, span);
	});

	const timelineAxisBottomY = $derived(TIMELINE_AXIS_TOP_PX + timelineAxisSpanPx);

	/** Sticky rail + axis SVG height = visible list inner height. */
	const timelineRailHeightPx = $derived.by(() => {
		layoutVersion;
		const h = listViewportContentHeightPx();
		return Math.max(TIMELINE_AXIS_TOP_PX + TIMELINE_AXIS_MIN_PX + TIMELINE_AXIS_BOTTOM_RESERVE_PX, h);
	});

	const timelineMarkerLayouts = $derived.by((): TimelineMarkerLayout[] => {
		layoutVersion;
		const contentEl = requirementsContentEl;
		if (!contentEl) return [];
		const topPadding = TIMELINE_AXIS_TOP_PX;
		const yMin = topPadding;
		const yMax = topPadding + timelineAxisSpanPx;

		const usableHeight = Math.max(1, timelineAxisSpanPx);
		const tDenom = Math.max(1, timelineMaxTurn);

		const raw = timelineEntries
			.map((entry) => {
				const targetEl = entry.eventKind === 'indirect'
					? indirectCardEls.get(entry.key)
					: entry.eventKind === 'executed'
						? executedCardEls.get(entry.key)
						: entry.eventKind === 'dismissed'
							? dismissedCardEls.get(entry.key)
							: requirementRowEls.get(entry.rowKey);
				if (!targetEl) return null;
				const { centerY, leftX } = layoutMetricsInContent(contentEl, targetEl);
				const rowY = centerY;
				// End at the row’s left edge only. (Old `max(66, …)` was for the previous padded overlay + 96px SVG; with a separate rail column, leftX≈0 and 66px drew lines across the card text.)
				const targetX = Math.max(0, leftX);
				const turnIdx = entry.turn == null ? 0 : Math.max(0, Math.min(tDenom, entry.turn));
				/** Axis Y from actual turn index (same scale as T1–TN ticks). */
				const axisY = topPadding + (turnIdx / tDenom) * usableHeight;
				return {
					...entry,
					markerY: axisY,
					plotY: axisY,
					rowY,
					targetX,
					clusterKey: '',
					clusterSize: 0
				};
			})
			.filter((entry): entry is TimelineMarkerLayout => entry != null);

		const groups = new Map<string, TimelineMarkerLayout[]>();
		for (const m of raw) {
			const k = timelineClusterKey(m);
			const list = groups.get(k);
			if (list) list.push(m);
			else groups.set(k, [m]);
		}
		for (const m of raw) {
			const k = timelineClusterKey(m);
			const g = groups.get(k)!;
			const turnRef = g[0].turn;
			const turnIdx = turnRef == null ? 0 : Math.max(0, Math.min(tDenom, turnRef));
			const axisY = topPadding + (turnIdx / tDenom) * usableHeight;
			const clamped = Math.min(yMax, Math.max(yMin, axisY));
			m.plotY = clamped;
			m.markerY = clamped;
			m.clusterKey = k;
			m.clusterSize = g.length;
		}
		return raw;
	});

	const timelineAxisClusters = $derived.by((): TimelineAxisCluster[] => {
		const markers = timelineMarkerLayouts;
		const byKey = new Map<string, TimelineMarkerLayout[]>();
		for (const m of markers) {
			const list = byKey.get(m.clusterKey);
			if (list) list.push(m);
			else byKey.set(m.clusterKey, [m]);
		}
		const clusters = [...byKey.values()].map((members) => {
			const m0 = members[0];
			const sel = members.find((m) => m.selected);
			const colorM = sel ?? m0;
			return {
				key: m0.clusterKey,
				plotY: m0.plotY,
				count: members.length,
				turn: m0.turn,
				anySelected: members.some((m) => m.selected),
				members,
				dotColor: speakerColor(colorM.speaker),
				hasExecuted: members.some((m) => m.eventKind === 'executed'),
				hasIndirect: members.some((m) => m.eventKind === 'indirect'),
				hasDismissed: members.some((m) => m.eventKind === 'dismissed'),
				hasRevised: members.some((m) => m.eventKind === 'revise-origin')
			};
		});
		clusters.sort((a, b) => a.plotY - b.plotY || a.key.localeCompare(b.key));
		return clusters;
	});

	const timelineTickTurns = $derived.by(() => {
		if (timelineMaxTurn <= 1) return [];
		const divisions = 4;
		const ticks: number[] = [];
		for (let i = 1; i < divisions; i += 1) {
			const t = Math.round((timelineMaxTurn * i) / divisions);
			if (t > 0 && t < timelineMaxTurn && !ticks.includes(t)) ticks.push(t);
		}
		return ticks;
	});

	/** End-of-axis label: same 1-based convention as formatTurnLabel (turn index → T{turn+1}). */
	const timelineAxisEndLabel = $derived(`T${timelineMaxTurn + 1}`);

	function turnToAxisY(turn: number): number {
		const usableHeight = Math.max(1, timelineAxisSpanPx);
		const clampedTurn = Math.max(0, Math.min(timelineMaxTurn, turn));
		const denom = Math.max(1, timelineMaxTurn);
		return TIMELINE_AXIS_TOP_PX + (clampedTurn / denom) * usableHeight;
	}

	function refreshConnectorPaths() {
		const list = requirementsListEl;
		const overlay = connectorsOverlayEl;
		const contentEl = requirementsContentEl;
		const rail = timelineRailEl;
		if (!list || !overlay || !contentEl || !rail) {
			connectorViewportPaths = [];
			return;
		}
		const oRect = overlay.getBoundingClientRect();
		if (oRect.width <= 0 || oRect.height <= 0) {
			connectorViewportPaths = [];
			return;
		}
		const contentRect = contentEl.getBoundingClientRect();
		const railRect = rail.getBoundingClientRect();
		const markers = timelineMarkerLayouts;
		const out: ConnectorViewportSeg[] = [];
		for (const m of markers) {
			const sx = railRect.left + 40 - oRect.left;
			const sy = railRect.top + m.plotY - oRect.top;
			const kinkX = railRect.left + 54 - oRect.left;
			const ex = contentRect.left + m.targetX - oRect.left;
			const ey = contentRect.top + m.rowY - oRect.top;
			const d = `M ${sx} ${sy} L ${kinkX} ${sy} L ${kinkX} ${ey} L ${ex} ${ey}`;
			out.push({
				key: m.key,
				d,
				selected: m.selected,
				executed: m.eventKind === 'executed',
				indirect: m.eventKind === 'indirect',
				dismissed: m.eventKind === 'dismissed',
				revised: m.eventKind === 'revise-origin'
			});
		}
		connectorViewportPaths = out;
	}

	function scrollConnectorPathsSync(listEl: HTMLElement) {
		const onScroll = () => refreshConnectorPaths();
		listEl.addEventListener('scroll', onScroll, { passive: true });
		queueMicrotask(() => refreshConnectorPaths());
		return {
			destroy() {
				listEl.removeEventListener('scroll', onScroll);
			}
		};
	}

	function focusTurnFromPanel(turn: number | null, reqId?: string) {
		if (reqId && !requirementIdsEqual(selectedRequirementId, reqId)) onRequirementClick?.(reqId);
		onTurnFocus?.(turn);
	}

	function onAxisClusterClick(clus: TimelineAxisCluster) {
		const indirectM = clus.members.find((m) => m.eventKind === 'indirect');
		if (indirectM) {
			toggleIndirectExpanded(indirectM.key);
			onTurnFocus?.(indirectM.turn);
			return;
		}
		const dismissedM = clus.members.find((m) => m.eventKind === 'dismissed');
		if (dismissedM) {
			focusTurnFromPanel(dismissedM.turn, dismissedM.reqId);
			return;
		}
		const reviseOriginM = clus.members.find((m) => m.eventKind === 'revise-origin');
		if (reviseOriginM) {
			focusTurnFromPanel(reviseOriginM.turn, reviseOriginM.reqId);
			return;
		}
		const allChild =
			clus.members.length > 0 &&
			clus.members.every((m) => m.rowKey.startsWith('child-goal-start:'));
		if (allChild) {
			const first = clus.members[0];
			const goalId = first.rowKey.slice('child-goal-start:'.length);
			onChildGoalClick?.(goalId);
			focusTurnFromPanel(first.turn);
			return;
		}
		const sel = clus.members.find(
			(m) => m.reqId && requirementIdsEqual(selectedRequirementId, m.reqId)
		);
		const reqPick = sel ?? clus.members.find((m) => m.reqId);
		focusTurnFromPanel(clus.turn, reqPick?.reqId);
	}

	$effect(() => {
		selectedRequirementId;
		displayRows;
		expandedIndirectKeys;
		bumpLayout();
	});

	$effect(() => {
		layoutVersion;
		timelineMarkerLayouts;
		queueMicrotask(() => refreshConnectorPaths());
	});
</script>

<div class="requirements-panel">
	<h3 class="panel-header">Timeline (Num Requirements: {totalRequirements})</h3>

	<!-- svelte-ignore a11y_no_static_element_interactions: hover sync to chat only -->
	<div
		class="requirements-list"
		role="presentation"
		bind:this={requirementsListEl}
		use:observeLayout
		use:scrollConnectorPathsSync
		onmouseleave={() => onPanelRowHover?.(null)}
	>
		<div class="requirements-connectors-viewport" bind:this={connectorsOverlayEl} aria-hidden="true">
			<svg class="requirements-connectors-svg" width="100%" height="100%">
				{#each connectorViewportPaths as seg (seg.key)}
					<path
						class="timeline-connector-line"
						class:selected={seg.selected}
						class:executed={seg.executed}
						class:indirect={seg.indirect}
						class:dismissed={seg.dismissed}
						class:revised={seg.revised}
						d={seg.d}
					></path>
				{/each}
			</svg>
		</div>
		<div class="requirements-scroll-row">
			<div class="timeline-rail-sticky-wrap">
				<p class="timeline-axis-sr">
					Timeline from earlier conversation turns at the top to later turns toward the bottom.
				</p>
			<div
				class="timeline-rail"
				style="height: {timelineRailHeightPx}px; --axis-bottom-y: {timelineAxisBottomY}px"
				bind:this={timelineRailEl}
			>
				<div class="timeline-axis-caption" aria-hidden="true">
					<span class="timeline-axis-caption-arrow">↓</span>
					<span class="timeline-axis-caption-text">Conv. progress</span>
				</div>
				<svg
					class="timeline-rail-svg"
					width="96"
					height={timelineRailHeightPx}
					viewBox={`0 0 96 ${timelineRailHeightPx}`}
					preserveAspectRatio="xMinYMin meet"
					aria-hidden="true"
				>
					<defs>
						<pattern id="timelineHatchUser" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
							<rect width="6" height="6" fill="{peerNames ? '#f3f0ff' : '#eff6ff'}"></rect>
							<line x1="0" y1="0" x2="0" y2="6" stroke="{peerNames ? '#c4b5fd' : '#93c5fd'}" stroke-width="1.2"></line>
						</pattern>
						<pattern id="timelineHatchUserIndirect" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
							<rect width="4" height="4" fill="{peerNames ? '#f3f0ff' : '#eff6ff'}"></rect>
							<line x1="0" y1="0" x2="0" y2="4" stroke="{peerNames ? PEER_A_COLOR : '#3b82f6'}" stroke-width="1.1"></line>
						</pattern>
						<pattern id="timelineHatchAssistant" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
							<rect width="6" height="6" fill="{peerNames ? '#fff7ed' : '#e8f6ef'}"></rect>
							<line x1="0" y1="0" x2="0" y2="6" stroke="{peerNames ? '#fdba74' : '#3c9b7d'}" stroke-width="1.2"></line>
						</pattern>
						<pattern id="timelineHatchAssistantIndirect" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
							<rect width="4" height="4" fill="{peerNames ? '#fff7ed' : '#e8f6ef'}"></rect>
							<line x1="0" y1="0" x2="0" y2="4" stroke="{peerNames ? PEER_B_COLOR : '#16a34a'}" stroke-width="1.1"></line>
						</pattern>
					</defs>
					<line class="timeline-axis-line" x1="40" y1={TIMELINE_AXIS_TOP_PX} x2="40" y2={timelineAxisBottomY}></line>
					{#each timelineTickTurns as tickTurn}
						<line class="timeline-axis-tick" x1="34" y1={turnToAxisY(tickTurn)} x2="40" y2={turnToAxisY(tickTurn)}></line>
					{/each}
					{#each timelineAxisClusters as clus (clus.key)}
						<circle
							class="timeline-marker-dot"
							class:selected={clus.anySelected}
							class:executed={clus.hasExecuted}
							class:indirect={clus.hasIndirect}
							class:dismissed={clus.hasDismissed}
							class:revised={clus.hasRevised}
							style="--dot-color:{clus.dotColor}"
							onmouseenter={() => onPanelRowHover?.(clus.turn ?? null)}
							cx="40"
							cy={clus.plotY}
							r={clus.anySelected ? 5.5 : 4.5}
							role="button"
							tabindex="0"
							aria-label={formatTurnLabel(clus.turn)}
							onclick={() => onAxisClusterClick(clus)}
							onkeydown={(e) => {
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									onAxisClusterClick(clus);
								}
							}}
						></circle>
					{/each}
				</svg>

				<div class="timeline-edge-label timeline-edge-label-top" aria-hidden="true">T1</div>
				<div class="timeline-edge-label timeline-edge-label-bottom" aria-hidden="true">{timelineAxisEndLabel}</div>
			</div>
			</div>

			<div class="requirements-content" bind:this={requirementsContentEl} use:observeLayout>
			{#each displayRows as row (row.key)}
				{#if row.kind === 'outcome-start'}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(outcomeStartTurn ?? null)}>
						<button
							type="button"
							class="req-item outcome-started req-plain-button"
							class:dimmed={selectedRequirementId != null}
							use:trackRequirementRow={{ key: 'outcome-start' }}
							onclick={() => focusTurnFromPanel(outcomeStartTurn)}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span class="circle-indicator" style="{circleStyle(outcomeSpeaker)}"></span>
									<span class="req-type-label">{goalLabel(outcome.outcome_id)} started</span>
								</div>
								<p class="req-text outcome-text">{outcome.outcome ?? outcome.outcome_id}</p>
							</div>
						</button>
					</div>
				{:else if row.kind === 'child-goal-start'}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(row.turn)}>
						<button
							type="button"
							class="req-item child-goal-started req-plain-button"
							class:dimmed={selectedRequirementId != null}
							use:trackRequirementRow={{ key: row.key }}
							onclick={() => {
								onChildGoalClick?.(row.goalId);
								focusTurnFromPanel(row.turn);
							}}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span class="circle-indicator" style="{circleStyle(row.speaker)}"></span>
									<span class="req-type-label">Child {goalLabel(row.goalId)} started</span>
								</div>
								<p class="req-text outcome-text">{row.goalTitle}</p>
							</div>
						</button>
					</div>
			{:else if row.kind === 'indirect'}
				{@const isIndirectOpen = !!expandedIndirectKeys[row.key]}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(row.turn)}>
						<button
							type="button"
							class="req-item req-indirect-item req-indirect-button"
							class:expanded={isIndirectOpen}
							style="--sc:{speakerColor(row.speaker)}"
							use:trackIndirectCard={{ key: row.key }}
							aria-expanded={isIndirectOpen}
							onclick={(e) => { e.stopPropagation(); toggleIndirectExpanded(row.key); onTurnFocus?.(row.turn); }}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span class="circle-indicator" style="{circleStyle(row.speaker)}"></span>
									<span class="req-type-label">Indirect influence</span>
									<span class="req-indirect-toggle" aria-hidden="true">{isIndirectOpen ? '▲' : '▼'}</span>
								</div>
								{#if isIndirectOpen}
									<div class="req-text-card req-indirect-text-card">
										{row.explanation}
									</div>
								{/if}
							</div>
						</button>
					</div>
				{:else if row.kind === 'executed'}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(row.turn)}>
						<button
							type="button"
							class="req-item req-executed-item req-plain-button"
							use:trackExecutedCard={{ key: row.key }}
							onclick={() => focusTurnFromPanel(row.turn, row.reqId)}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span class="circle-indicator" style="{circleStyle(row.speaker)}"></span>
									<span class="req-type-label">Executed</span>
								</div>
							</div>
						</button>
					</div>
				{:else if row.kind === 'dismissed'}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(row.turn)}>
						<button
							type="button"
							class="req-item req-dismissed-item req-plain-button"
							use:trackDismissedCard={{ key: row.key }}
							onclick={() => focusTurnFromPanel(row.turn, row.reqId)}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span class="circle-indicator circle-indicator--muted" aria-hidden="true"></span>
									<span class="req-type-label">Dismissed</span>
								</div>
							</div>
						</button>
					</div>
				{:else if row.kind === 'requirement-original'}
					{@const speaker = getRequirementCreatorSpeaker(row.versionId, row.turn)}
					{@const isOriginalFocused = revisionFocusVersionId === row.versionId}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(row.turn)}>
						<button
							type="button"
							class="req-item req-original-item req-clickable"
							class:selected={isOriginalFocused}
							class:req-original-item--hint={!isOriginalFocused}
							use:trackRequirementRow={{ key: row.key }}
							onclick={(e) => {
								e.stopPropagation();
								onRevisionVersionFocus?.(row.versionId, row.turn);
							}}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span class="circle-indicator" style="{circleStyle(speaker)}"></span>
									<span class="req-type-label">Original requirement</span>
								</div>
								{#if !isOriginalFocused}
									<p class="req-version-hint">Click to view related actions (original)</p>
								{:else}
									<p class="req-version-hint req-version-hint--active">Viewing related actions (original)</p>
								{/if}
								<div class="req-text-card req-text-card--superseded">
									{row.text}
								</div>
							</div>
						</button>
					</div>
				{:else}
					{@const reqId = row.reqId}
					{@const chain = row.chain}
					{@const turn = row.turn}
					{@const speaker = getRequirementCreatorSpeaker(reqId, turn)}
					{@const isSelected = requirementIdsEqual(selectedRequirementId, reqId)}
					{@const isDismissed = reqIsDismissed(reqId)}
					{@const statusKind = getReqStatusKind(reqId)}
					{@const statusLabel = getReqStatusLabel(reqId)}
					{@const contrib = resolveContribBar(reqId)}
					{@const hasRevisionStack = chainHasRevisionHistory(chain)}
					{@const showRevisedOnly = hasRevisionStack && isSelected}
					{@const isRevisedActionFocus = isSelected && revisionFocusVersionId == null}
					<div class="req-row-shell" role="presentation" onmouseenter={() => onPanelRowHover?.(turn)}>
						<button
							type="button"
							class="req-item req-clickable"
							class:selected={isSelected && isRevisedActionFocus}
							class:req-dismissed={isDismissed}
							class:req-has-revision={hasRevisionStack && !isSelected}
							class:req-revised-item={showRevisedOnly}
							class:req-revised-item--muted={isSelected && revisionFocusVersionId != null}
							class:dimmed={selectedRequirementId != null && !isSelected && !isDismissed}
							use:trackRequirementRow={{ key: row.key }}
							onclick={(e) => {
								e.stopPropagation();
								const wasSelected = requirementIdsEqual(selectedRequirementId, reqId);
								if (wasSelected && revisionFocusVersionId != null) {
									onRevisionVersionFocus?.(null, turn);
									return;
								}
								if (!wasSelected) onTurnFocus?.(turn);
								onRequirementClick?.(reqId);
							}}
						>
							<div class="req-content">
								<div class="req-label-row">
									<span
										class="circle-indicator"
										class:circle-indicator--muted={isDismissed}
										style={isDismissed ? undefined : circleStyle(speaker)}
									></span>
									<span class="req-type-label" class:req-type-label--dismissed={isDismissed}>
										{isDismissed
											? 'Dismissed requirement'
											: showRevisedOnly
												? 'Revised requirement'
												: getRequirementLabel(chain)}
									</span>
								</div>
								{#if isSelected && showRevisedOnly && revisionFocusVersionId == null}
									<p class="req-version-hint req-version-hint--active">Viewing related actions (revised)</p>
								{:else if isSelected && revisionFocusVersionId != null}
									<p class="req-version-hint">Click to view related actions (revised)</p>
								{/if}
								{#if hasRevisionStack && !isSelected}
									<div
										class="req-revision-stack req-revision-stack--preview"
										style="--stack-depth: {chain.history.length}"
										aria-hidden="false"
									>
										{#each chain.history as _, layerIdx (layerIdx)}
											<div
												class="req-revision-layer req-revision-layer--ghost"
												style="--layer-index: {layerIdx}"
												aria-hidden="true"
											>
												<div class="req-text-card req-text-card--ghost"></div>
											</div>
										{/each}
										<div class="req-revision-front">
											<div
												class="req-text-card"
												class:req-text-card--dismissed={isDismissed}
											>
												{chain.currentText}
											</div>
										</div>
									</div>
								{:else}
									<div
										class="req-text-card"
										class:req-text-card--dismissed={isDismissed}
										class:card-selected={isSelected && !isDismissed}
									>
										{chain.currentText}
									</div>
								{/if}

								{#if isSelected}
									{#if contrib.hasData}
										<div class="contrib-bar-wrap">
											{#if contrib.mode === 'peers'}
												<div
													class="contrib-bar contrib-bar--peers"
													title="{contrib.leftName}{contrib.rightName ? ` · ${contrib.rightName}` : ''}"
												>
													<div class="contrib-bar-peer-a" style="width: {contrib.leftPct}%"></div>
													{#if contrib.rightName}
														<div class="contrib-bar-peer-b" style="width: {100 - contrib.leftPct}%"></div>
													{/if}
												</div>
											{:else}
												<div class="contrib-bar">
													<div class="contrib-bar-user" style="width: {contrib.userPct}%"></div>
													<div class="contrib-bar-assistant" style="width: {100 - contrib.userPct}%"></div>
												</div>
											{/if}
										</div>
									{/if}
									<div class="status-row">
										<span class="status-square {statusKind}"></span>
										<span class="status-label">{statusLabel}</span>
									</div>
								{/if}
							</div>
						</button>
					</div>
				{/if}
			{/each}
			</div>
		</div>
	</div>
</div>

<style>
	.requirements-panel {
		display: flex;
		flex-direction: column;
		background: #ffffff;
		border: 1px solid #e5e7eb;
		border-radius: 18px;
		min-height: 0;
		overflow: hidden;
		box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
	}
	.panel-header {
		margin: 0;
		padding: 0.9rem 1rem;
		font-size: 0.72rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: #64748b;
		border-bottom: 1px solid #e5e7eb;
		background: #f8fafc;
		flex-shrink: 0;
	}
	.requirements-list {
		position: relative;
		display: flex;
		flex-direction: column;
		overflow-y: auto;
		flex: 1;
		min-height: 0;
		padding: 0.65rem 0.65rem 0.65rem 0.4rem;
		background: #ffffff;
	}
	.requirements-connectors-viewport {
		position: absolute;
		inset: 0;
		pointer-events: none;
		/* Above req cards (z 3) so gap + row-edge segments show; below rail (z 6) so axis/dots stay crisp. */
		z-index: 5;
		overflow: visible;
	}
	.requirements-connectors-svg {
		display: block;
		width: 100%;
		height: 100%;
		overflow: visible;
	}
	.requirements-scroll-row {
		display: flex;
		flex-direction: row;
		align-items: flex-start;
		gap: 0.22rem;
		flex: 1 0 auto;
		min-height: 100%;
		width: 100%;
		box-sizing: border-box;
	}
	.timeline-rail-sticky-wrap {
		position: sticky;
		top: 0;
		align-self: flex-start;
		flex: 0 0 auto;
		z-index: 6;
	}
	.timeline-axis-sr {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}
	/* Sits in the rail’s narrow left strip before the axis; no extra column. */
	.timeline-axis-caption {
		position: absolute;
		left: 0;
		top: 1.65rem;
		z-index: 2;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.2rem;
		width: 0.55rem;
		max-width: 0.65rem;
		box-sizing: border-box;
		user-select: none;
		pointer-events: none;
	}
	.timeline-axis-caption-arrow {
		font-size: 0.72rem;
		font-weight: 600;
		color: #94a3b8;
		line-height: 1;
		flex-shrink: 0;
	}
	/* Sideways: one horizontal line rotated 90° so it runs top→bottom along the rail (time). */
	.timeline-axis-caption-text {
		display: block;
		font-size: 0.48rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: #64748b;
		white-space: nowrap;
		writing-mode: sideways-lr;
	}
	@supports not (writing-mode: sideways-lr) {
		.timeline-axis-caption-text {
			writing-mode: vertical-rl;
			transform: rotate(180deg);
			text-orientation: mixed;
		}
	}
	.timeline-rail {
		position: relative;
		flex: 0 0 96px;
		width: 96px;
		min-width: 96px;
		box-sizing: border-box;
		/* Transparent so connector lines (z 5) show through this column; axis/dots paint in SVG above them. */
		background: transparent;
	}
	.timeline-rail-svg {
		display: block;
		pointer-events: none;
		position: relative;
		z-index: 1;
	}
	.requirements-content {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		min-height: 100%;
		flex: 1;
		min-width: 0;
		padding-left: 0;
		width: auto;
		box-sizing: border-box;
	}
	.timeline-axis-line {
		stroke: #cbd5e1;
		stroke-width: 2;
		stroke-linecap: round;
		pointer-events: none;
	}
	.timeline-axis-tick {
		stroke: #cbd5e1;
		stroke-width: 1.4;
		stroke-linecap: round;
		pointer-events: none;
	}
	.timeline-edge-label {
		position: absolute;
		left: 0.2rem;
		font-size: 0.62rem;
		font-weight: 700;
		color: #64748b;
		background: #ffffff;
		border: 1px solid #dbe4ee;
		border-radius: 999px;
		padding: 0.08rem 0.34rem;
		line-height: 1;
		pointer-events: none;
		z-index: 7;
	}
	.timeline-edge-label-top {
		top: 0.15rem;
	}
	.timeline-edge-label-bottom {
		top: calc(var(--axis-bottom-y, 200px) - 0.2rem);
		bottom: auto;
	}
	.timeline-connector-line {
		fill: none;
		stroke: #94a3b8;
		stroke-width: 2;
		stroke-dasharray: 5 5;
		stroke-linecap: round;
		stroke-linejoin: round;
		shape-rendering: geometricPrecision;
		pointer-events: none;
		vector-effect: non-scaling-stroke;
	}
	.timeline-connector-line.selected {
		stroke: #60a5fa;
		stroke-width: 2;
	}
	.timeline-connector-line.executed {
		stroke: #3c9b7d;
		stroke-width: 2;
		stroke-dasharray: 2 4;
	}
	.timeline-connector-line.indirect {
		stroke: #94a3b8;
		stroke-width: 1.8;
		stroke-dasharray: 3 4;
	}
	.timeline-connector-line.dismissed {
		stroke: #94a3b8;
		stroke-width: 2;
		stroke-dasharray: 2 3;
	}
	.timeline-connector-line.revised {
		stroke: #d97706;
		stroke-width: 2;
		stroke-dasharray: 4 3;
	}
	.timeline-marker-dot {
		fill: var(--dot-color, #3b82f6);
		stroke: var(--dot-color, #3b82f6);
		stroke-width: 2;
		cursor: pointer;
		pointer-events: auto;
	}
	.timeline-marker-dot.selected {
		stroke-width: 2.4;
	}
	.timeline-marker-dot.executed {
		stroke: #15803d;
		fill: #22c55e;
	}
	.timeline-marker-dot.dismissed {
		stroke: #64748b;
		fill: #94a3b8;
	}
	.timeline-marker-dot.revised {
		stroke: #b45309;
		fill: #f59e0b;
	}
	.timeline-marker-dot:focus-visible {
		outline: 2px solid #2563eb;
		outline-offset: 2px;
	}
	.req-row-shell {
		position: relative;
		padding-left: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		z-index: 3;
	}
	.req-clickable,
	.req-plain-button,
	.req-indirect-button {
		position: relative;
		z-index: 1;
	}
	.req-item {
		position: relative;
		display: flex;
		align-items: flex-start;
		gap: 0.65rem;
		padding: 0.8rem 0.9rem;
		transition: opacity 0.15s;
		border-radius: 16px;
		border: 1px solid #e5e7eb;
		background: #ffffff;
		box-shadow: 0 4px 12px rgba(15, 23, 42, 0.04);
	}
	.req-item.dimmed {
		opacity: 0.42;
	}
	.req-clickable {
		width: 100%;
		background: none;
		border: none;
		cursor: pointer;
		text-align: left;
		font: inherit;
	}
	.req-plain-button {
		width: 100%;
		background: none;
		border: 1px solid #e5e7eb;
		text-align: left;
		font: inherit;
		cursor: pointer;
	}
	.req-plain-button:hover {
		box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.22);
		border-color: #dbeafe;
	}
	.req-clickable:hover {
		background: #f8fafc;
		border-color: #dbe4ee;
		box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2);
	}
	.req-clickable.selected {
		opacity: 1;
		background: #eff6ff;
		border-color: #93c5fd;
		box-shadow: 0 8px 20px rgba(59, 130, 246, 0.12);
	}
	.req-indirect-item {
		border-color: color-mix(in srgb, var(--sc, #3b82f6) 35%, #e5e7eb);
		background:
			repeating-linear-gradient(
				-45deg,
				color-mix(in srgb, var(--sc, #3b82f6) 14%, transparent) 0px,
				color-mix(in srgb, var(--sc, #3b82f6) 14%, transparent) 5px,
				color-mix(in srgb, var(--sc, #3b82f6) 6%, #ffffff) 5px,
				color-mix(in srgb, var(--sc, #3b82f6) 6%, #ffffff) 10px
			),
			color-mix(in srgb, var(--sc, #3b82f6) 6%, #ffffff);
		box-shadow: 0 6px 16px color-mix(in srgb, var(--sc, #3b82f6) 10%, transparent);
	}
	.req-executed-item {
		border-color: #b9decf;
		background: #eef8f2;
		box-shadow: 0 6px 16px rgba(47, 122, 87, 0.08);
	}
	.req-dismissed-item {
		border-color: #cbd5e1;
		background: #f1f5f9;
		box-shadow: 0 4px 12px rgba(100, 116, 139, 0.08);
	}
	.req-revised-item {
		border-color: #fcd34d;
		background: #fffbeb;
		box-shadow: 0 6px 16px rgba(245, 158, 11, 0.1);
	}
	.req-original-item {
		border-color: #fde68a;
		background: #fffdf5;
		box-shadow: 0 4px 12px rgba(245, 158, 11, 0.08);
		opacity: 0.96;
	}
	.req-original-item.selected {
		border-color: #f59e0b;
		background: #fffbeb;
		box-shadow: 0 6px 16px rgba(245, 158, 11, 0.14);
		opacity: 1;
	}
	.req-original-item--hint {
		border-style: dashed;
	}
	.req-original-item--hint:hover {
		border-color: #f59e0b;
		background: #fffbeb;
	}
	.req-revised-item--muted {
		opacity: 0.72;
		border-style: dashed;
	}
	.req-revised-item--muted.selected {
		opacity: 0.85;
	}
	.req-version-hint {
		margin: 0;
		font-size: 0.68rem;
		font-weight: 600;
		color: #b45309;
		letter-spacing: 0.01em;
	}
	.req-version-hint--active {
		color: #92400e;
	}
	.req-clickable.req-has-revision {
		border-color: #fde68a;
		background: linear-gradient(135deg, #fffbeb 0%, #ffffff 55%);
	}
	.req-clickable.req-has-revision.selected {
		border-color: #f59e0b;
		background: linear-gradient(135deg, #fef3c7 0%, #eff6ff 60%);
		box-shadow: 0 8px 20px rgba(245, 158, 11, 0.14);
	}
	.req-revision-stack {
		position: relative;
		margin-top: 0.15rem;
		padding-top: calc(var(--stack-depth, 1) * 7px);
		padding-right: calc(var(--stack-depth, 1) * 7px);
	}
	.req-revision-layer {
		position: absolute;
		top: calc(var(--layer-index, 0) * 7px);
		left: calc(var(--layer-index, 0) * 7px);
		right: calc((var(--stack-depth, 1) - 1 - var(--layer-index, 0)) * 7px);
		z-index: calc(var(--layer-index, 0) + 1);
		pointer-events: none;
	}
	.req-revision-front {
		position: relative;
		z-index: calc(var(--stack-depth, 1) + 2);
	}
	.req-text-card--superseded {
		font-size: 0.78rem;
		color: #64748b;
		background: #f1f5f9;
		border-color: #cbd5e1;
		border-style: dashed;
		opacity: 0.72;
		text-decoration: line-through;
		text-decoration-color: #94a3b8;
		text-decoration-thickness: 1px;
		box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
	}
	.req-text-card--ghost {
		min-height: 2.4rem;
		background: #f8fafc;
		border-color: #e2e8f0;
		border-style: dashed;
		opacity: 0.55;
		box-shadow: none;
	}
	.req-revision-layer--ghost {
		opacity: 1;
	}
	.req-clickable.req-dismissed {
		opacity: 0.62;
		background: #eef2f6;
		border-color: #cbd5e1;
		border-style: dashed;
		color: #64748b;
		box-shadow: none;
	}
	.req-clickable.req-dismissed:hover {
		opacity: 0.78;
		background: #e8edf2;
		border-color: #94a3b8;
		box-shadow: none;
	}
	.req-clickable.req-dismissed .req-type-label--dismissed {
		color: #64748b;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		font-size: 0.68rem;
	}
	.req-clickable.req-dismissed .req-text-card--dismissed {
		color: #64748b;
		background: rgba(241, 245, 249, 0.65);
		border-color: #cbd5e1;
		border-style: dashed;
		text-decoration: line-through;
		text-decoration-color: #94a3b8;
		text-decoration-thickness: 1.5px;
	}
	.req-clickable.req-dismissed.selected {
		opacity: 0.92;
		background: #e8edf2;
		border-color: #94a3b8;
		border-style: dashed;
		box-shadow: 0 4px 14px rgba(100, 116, 139, 0.1);
	}
	.req-clickable.req-dismissed.selected .req-text-card--dismissed {
		background: rgba(248, 250, 252, 0.85);
	}
	.circle-indicator--muted {
		border-color: #94a3b8 !important;
		background: transparent !important;
	}
	.child-goal-started {
		border-color: #c7d2fe;
		background: #eef2ff;
		box-shadow: 0 6px 16px rgba(67, 56, 202, 0.08);
	}
	.req-indirect-button {
		width: 100%;
		cursor: pointer;
		text-align: left;
		font: inherit;
	}
	.req-indirect-button:hover {
		border-color: var(--sc, #60a5fa);
		filter: brightness(0.99);
		box-shadow: 0 0 0 1px color-mix(in srgb, var(--sc, #3b82f6) 25%, transparent);
	}
	.req-indirect-button.expanded {
		border-color: var(--sc, #3b82f6);
		box-shadow: 0 8px 18px color-mix(in srgb, var(--sc, #3b82f6) 15%, transparent);
	}
	.req-indirect-text-card {
		background: #ffffff;
		border-color: #e5e7eb;
		color: #334155;
	}
	.req-indirect-toggle {
		color: var(--sc, #2563eb);
		margin-left: auto;
		font-size: 0.7rem;
		font-weight: 700;
		line-height: 1;
	}

	.circle-indicator {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		flex-shrink: 0;
		margin-top: 2px;
		box-sizing: border-box;
		position: relative;
		z-index: 1;
		border: 2.5px solid currentColor;
	}
	.req-content {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.req-label-row {
		display: flex;
		align-items: center;
		gap: 0.4rem;
	}
	.req-type-label {
		font-size: 0.72rem;
		font-weight: 600;
		color: #475569;
	}
	.req-text-card {
		font-size: 0.82rem;
		line-height: 1.45;
		color: #334155;
		padding: 0.6rem 0.7rem;
		background: #f8fafc;
		border: 1px solid #e5e7eb;
		border-radius: 12px;
		word-break: break-word;
	}
	.req-text-card.card-selected {
		background: #ffffff;
		border-color: #3b82f6;
		border-width: 2px;
	}
	.outcome-text {
		font-size: 0.82rem;
		line-height: 1.45;
		color: #334155;
		margin: 0;
	}

	.contrib-bar-wrap {
		margin-top: 0.25rem;
	}
	.contrib-bar {
		display: flex;
		height: 8px;
		border-radius: 4px;
		overflow: hidden;
		background: #e5e7eb;
	}
	.contrib-bar-user {
		background: linear-gradient(135deg, #3b82f6, #60a5fa);
		transition: width 0.3s ease;
	}
	.contrib-bar--peers {
		background: #e5e7eb;
	}
	.contrib-bar-peer-a {
		background: linear-gradient(135deg, #7c3aed, #a78bfa);
		transition: width 0.3s ease;
		min-width: 0;
	}
	.contrib-bar-peer-b {
		background: linear-gradient(135deg, #ea580c, #fb923c);
		transition: width 0.3s ease;
		min-width: 0;
	}
	.contrib-bar-assistant {
		background: linear-gradient(135deg, #16a34a, #4ade80);
		transition: width 0.3s ease;
	}

	.status-row {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-top: 0.3rem;
	}
	.status-square {
		width: 16px;
		height: 16px;
		border-radius: 3px;
		flex-shrink: 0;
	}
	.status-square.done {
		background: #3c9b7d;
	}
	.status-square.pending {
		background: #3b82f6;
	}
	.status-square.dismissed {
		background: #94a3b8;
	}
	.status-square.unknown {
		background: #cbd5e1;
	}
	.status-label {
		font-size: 0.88rem;
		font-weight: 700;
		color: #334155;
	}
</style>
