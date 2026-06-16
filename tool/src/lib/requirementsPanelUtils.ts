import type { ContribBarDisplay } from '$lib/contribPeerBar';
import type { RequirementActionMap } from '$lib/data/dataLoader';

export type RequirementStatusRow = {
	id: string;
	is_executed: boolean | null;
	is_dismissed: boolean | null;
	dismissed_at_turn: number | null;
	dismissed_by_action_ids?: string[];
};

export type RequirementStatusMap = Map<
	string,
	{
		is_executed: boolean | null;
		is_dismissed: boolean | null;
		dismissed_at_turn: number | null;
		dismissed_by_action_ids: string[];
	}
>;

export type RequirementChainLike = {
	currentId: string;
	history: Array<{ id: string; text: string }>;
};

export type ReqContribLike = {
	user: number;
	assistant: number;
};

export function requirementIdsEqual(a: string | null | undefined, b: string): boolean {
	if (a == null) return false;
	if (a === b) return true;
	const norm = (value: string) => value.trim().replace(/^r/i, '');
	return norm(a) === norm(b);
}

export function buildRequirementStatusIndex(rows: RequirementStatusRow[]): RequirementStatusMap {
	const map: RequirementStatusMap = new Map();
	for (const row of rows) {
		if (!row?.id) continue;
		const entry = {
			is_executed: row.is_executed,
			is_dismissed: row.is_dismissed,
			dismissed_at_turn: row.dismissed_at_turn ?? null,
			dismissed_by_action_ids: Array.isArray(row.dismissed_by_action_ids)
				? row.dismissed_by_action_ids.filter((id): id is string => typeof id === 'string' && id.length > 0)
				: []
		};
		map.set(row.id, entry);
		if (/^r\d+/.test(row.id)) map.set(row.id.replace(/^r/, ''), entry);
		if (/^\d+/.test(row.id)) map.set(`r${row.id}`, entry);
	}
	return map;
}

export function getRequirementStatusKind(
	statusById: RequirementStatusMap,
	requirementId: string
): 'done' | 'pending' | 'dismissed' | 'unknown' {
	const status = statusById.get(requirementId);
	if (!status) return 'unknown';
	if (status.is_dismissed) return 'dismissed';
	if (status.is_executed === true) return 'done';
	if (status.is_executed === false) return 'pending';
	return 'unknown';
}

export function getRequirementStatusLabel(statusById: RequirementStatusMap, requirementId: string): string {
	const kind = getRequirementStatusKind(statusById, requirementId);
	if (kind === 'done') return 'Executed';
	if (kind === 'pending') return 'Pending';
	if (kind === 'dismissed') return 'Dismissed';
	return 'Unknown';
}

export function sortRequirementChainsByCreationTurn<T extends RequirementChainLike>(
	chains: T[],
	creationTurnByReqId: Record<string, number>
): T[] {
	return [...chains].sort((a, b) => {
		const aTurn = creationTurnByReqId[a.currentId] ?? Number.POSITIVE_INFINITY;
		const bTurn = creationTurnByReqId[b.currentId] ?? Number.POSITIVE_INFINITY;
		return aTurn - bTurn;
	});
}

export function resolveContribBarByReqId(
	requirementId: string,
	contribBarByReqId: Record<string, ContribBarDisplay>,
	requirementContributionByReqId: Record<string, ReqContribLike>
): ContribBarDisplay {
	let byId = contribBarByReqId[requirementId];
	if (!byId && /^r\d+$/i.test(requirementId)) byId = contribBarByReqId[requirementId.replace(/^r/i, '')];
	if (!byId && /^\d+$/.test(requirementId)) byId = contribBarByReqId[`r${requirementId}`];
	if (byId) return byId;
	const contribution = requirementContributionByReqId[requirementId];
	if (!contribution || (contribution.user === 0 && contribution.assistant === 0)) {
		return { mode: 'legacy', userPct: 50, hasData: false };
	}
	const total = contribution.user + contribution.assistant;
	return {
		mode: 'legacy',
		userPct: total > 0 ? (contribution.user / total) * 100 : 50,
		hasData: true
	};
}

export function getRequirementLabel(historyLength: number): string {
	return historyLength > 0 ? 'Requirement revised' : 'Requirement created';
}

export function getRequirementActionEntry(requirementActionMap: RequirementActionMap, requirementId: string) {
	return (
		requirementActionMap[requirementId] ??
		requirementActionMap[requirementId.replace(/^r/, '')] ??
		requirementActionMap[`r${requirementId}`]
	);
}

export function getTurnFromActionId(actionId: string | undefined): number | null {
	const turnPart = actionId?.split('-')[0];
	const turn = turnPart == null ? Number.NaN : Number.parseInt(turnPart, 10);
	return Number.isFinite(turn) ? turn : null;
}

export function getExecutedTurn(requirementId: string, requirementActionMap: RequirementActionMap): number | null {
	const entry = getRequirementActionEntry(requirementActionMap, requirementId);
	const implementationActions = Array.isArray(entry?.implementation_actions)
		? entry.implementation_actions
		: [];
	if (implementationActions.length === 0) return null;
	const turns = implementationActions
		.map((action) => getTurnFromActionId(action.action_id))
		.filter((turn): turn is number => turn != null);
	if (turns.length === 0) return null;
	return Math.min(...turns);
}

function resolveStatusRow(
	statusById: RequirementStatusMap,
	requirementId: string
): RequirementStatusMap extends Map<string, infer V> ? V | undefined : never {
	return (
		statusById.get(requirementId) ??
		statusById.get(requirementId.replace(/^r/, '')) ??
		statusById.get(/^r/.test(requirementId) ? requirementId : `r${requirementId}`)
	);
}

/** True when requirement was deleted/dismissed (has dismiss action ids or is_dismissed flag). */
export function isRequirementDismissed(
	statusById: RequirementStatusMap,
	requirementId: string
): boolean {
	const status = resolveStatusRow(statusById, requirementId);
	if (!status) return false;
	if (status.is_dismissed) return true;
	return (status.dismissed_by_action_ids?.length ?? 0) > 0;
}

/** Dismiss turn: prefer turn from dismissed_by_action_ids, else dismissed_at_turn. */
export function getDismissedTurn(
	statusById: RequirementStatusMap,
	requirementId: string,
	actionTurnById: Record<string, number> = {}
): number | null {
	const status = resolveStatusRow(statusById, requirementId);
	if (!status || !isRequirementDismissed(statusById, requirementId)) return null;
	const actionIds = status.dismissed_by_action_ids ?? [];
	if (actionIds.length > 0) {
		const turns = actionIds
			.map((id) => actionTurnById[id] ?? getTurnFromActionId(id))
			.filter((turn): turn is number => turn != null);
		if (turns.length > 0) return Math.min(...turns);
	}
	return status.dismissed_at_turn;
}

export function goalLabel(goalId: string): string {
	const normalized = goalId.trim();
	const alphaChild = normalized.match(/^outcome_(\d+)([a-z])(?:_\d+)?$/i);
	if (alphaChild) return `Goal ${alphaChild[1]}${alphaChild[2].toLowerCase()}`;
	const numericChild = normalized.match(/^outcome_(\d+)_(\d+)(?:_\d+)?$/i);
	if (numericChild) {
		const childIdx = Number.parseInt(numericChild[2], 10);
		const suffix = Number.isFinite(childIdx) && childIdx >= 0
			? String.fromCharCode(97 + (childIdx % 26))
			: '';
		return `Goal ${numericChild[1]}${suffix}`;
	}
	const primary = normalized.match(/^outcome_(\d+)$/i);
	if (primary) return `Goal ${primary[1]}`;
	const fallback = normalized.match(/(\d+)/);
	return fallback ? `Goal ${fallback[1]}` : normalized;
}

export function indirectEventKey(requirementId: string, turn: number, explanation: string): string {
	return `req:${requirementId}:indirect:${turn}:${explanation}`;
}

export function getIndirectInfluenceEvents(
	requirementId: string,
	requirementActionMap: RequirementActionMap,
	requirementCreationTurnByReqId: Record<string, number>,
	getSpeakerForTurn: (turn: number | null | undefined) => string
): Array<{ turn: number; explanation: string; speaker: string }> {
	const entry = getRequirementActionEntry(requirementActionMap, requirementId);
	const relatedActions = Array.isArray(entry?.related_actions) ? entry.related_actions : [];
	const requirementCreationTurn = requirementCreationTurnByReqId[requirementId] ?? null;
	const seen = new Set<string>();
	return relatedActions
		.filter(
			(item): item is { influence?: string; explanation?: string; action_id?: string } =>
				!!item && typeof item === 'object'
		)
		.filter(
			(item) =>
				item.influence === 'indirect' &&
				typeof item.explanation === 'string' &&
				item.explanation.trim().length > 0
		)
		.map((item) => {
			const turn = getTurnFromActionId(item.action_id);
			if (turn == null) return null;
			if (requirementCreationTurn != null && turn > requirementCreationTurn) return null;
			const explanation = item.explanation!.trim();
			const key = `${turn}:${explanation}`;
			if (seen.has(key)) return null;
			seen.add(key);
			return { turn, explanation, speaker: getSpeakerForTurn(turn) };
		})
		.filter((item): item is { turn: number; explanation: string; speaker: string } => item != null)
		.sort((a, b) => a.turn - b.turn || a.explanation.localeCompare(b.explanation));
}

export function timelineClusterKey(entry: {
	key: string;
	rowKey: string;
	turn: number | null;
	eventKind?: 'base' | 'executed' | 'indirect' | 'dismissed';
}): string {
	if (entry.eventKind === 'indirect') return `indirect:${entry.key}`;
	if (entry.eventKind === 'executed') return `${entry.turn ?? 'na'}:executed`;
	if (entry.eventKind === 'dismissed') return `${entry.turn ?? 'na'}:dismissed`;
	if (entry.key === 'outcome-start') return 'base:outcome-start';
	if (entry.rowKey.startsWith('child-goal-start:')) return `base:child:${entry.turn ?? 'na'}`;
	return `base:req:${entry.turn ?? 'na'}`;
}
