<script lang="ts">
	import { onMount } from 'svelte';
	import ChatLogPanel from '$lib/components/ChatLogPanel.svelte';
	import RequirementsPanel from '$lib/components/RequirementsPanel.svelte';
	import Tutorial from '$lib/components/Tutorial.svelte';
	import OutcomeHierarchyWithTimeline from '$lib/components/OutcomeHierarchyWithTimeline.svelte';
	import type { TutorialStep } from '$lib/components/Tutorial.svelte';
	import { selectedRun } from '$lib/stores/selectedRun';
	import {
		loadOutcomeRequirementTree,
		loadOutcomeContributions,
		loadRequirements,
		loadRequirementActionMap,
		loadRequirementContributions,
		loadRequirementStatus,
		loadUtteranceList,
		loadActionUtteranceMap,
		loadNodeAnalysis,
		loadOutcomeActionMap,
		loadOutcomeIntentionMap,
		buildRequirementContentMap,
		getRequirementContent
	} from '$lib/data/dataLoader';
	import {
		buildContribBarFromRequirementContributionEntry,
		type ContribBarDisplay
	} from '$lib/contribPeerBar';
	import { isAssistantSpeakerId } from '$lib/speakerUtils';
	import { compareOutcomeIds, outcomeSortKey } from '$lib/utils';
	import type {
		OutcomeNode,
		OutcomeContribution,
		OutcomeContributionsData,
		RoleContributionDetail,
		NodeAnalysisData
	} from '$lib/types';
	import type { RequirementActionMap, RequirementContributionsData, RequirementStatusById, UtteranceListData, ActionUtteranceMap, OutcomeActionMapData, OutcomeActionItem } from '$lib/data/dataLoader';

	let runs = $state<string[]>([]);
	let selectedRunValue = $state<string | null>(null);
	let tree = $state<Awaited<ReturnType<typeof loadOutcomeRequirementTree>> | null>(null);
	let contributions = $state<OutcomeContributionsData | null>(null);
	let requirementRows = $state<Awaited<ReturnType<typeof loadRequirements>> | null>(null);
	let nodeAnalysisData = $state<NodeAnalysisData | null>(null);
	let requirementActionMap = $state<RequirementActionMap>({});
	let requirementContributions = $state<RequirementContributionsData>({});
	let requirementStatusById = $state<RequirementStatusById>({});
	let utteranceList = $state<UtteranceListData | null>(null);
	let actionUtteranceMap = $state<ActionUtteranceMap>({});
	let outcomeActionMap = $state<OutcomeActionMapData | null>(null);
	let outcomeIntentionMap = $state<Record<string, string>>({});
	let selectedOutcomeId = $state<string | null>(null);
	let hierarchyHighlightedOutcomeId = $state<string | null>(null);
	let selectedIntentLabel = $state<string | null>(null);
	let selectedRequirementId = $state<string | null>(null);
	let selectedActionId = $state<string | null>(null);
	let focusedChatTurnId = $state<number | null>(null);
	/** Hovered action id: sync highlight across action card, timeline marker, and chat utterance. */
	let hoveredActionId = $state<string | null>(null);
	let hoveredPanelTurnId = $state<number | null>(null);
	let leftColWidth = $state(380);
	let isResizing = $state(false);
	let loading = $state(false);
	let runsLoading = $state(true);
	let error = $state<string | null>(null);

	type RunGroup = { category: string; items: { path: string; label: string }[] };

	function buildRunGroups(runList: string[]): RunGroup[] {
		const map = new Map<string, { path: string; label: string }[]>();
		for (const p of runList) {
			if (p === '__default__') {
				if (!map.has('')) map.set('', []);
				map.get('')!.push({ path: p, label: 'Current run' });
				continue;
			}
			const slash = p.indexOf('/');
			const cat = slash === -1 ? '' : p.slice(0, slash);
			const label = slash === -1 ? p : p.slice(slash + 1);
			if (!map.has(cat)) map.set(cat, []);
			map.get(cat)!.push({ path: p, label: label || p });
		}
		const keys = [...map.keys()].sort((a, b) => {
			if (a === '') return -1;
			if (b === '') return 1;
			return a.localeCompare(b);
		});
		return keys.map((category) => ({
			category: category || 'Runs',
			items: (map.get(category) ?? []).sort((x, y) => x.label.localeCompare(y.label))
		}));
	}

	const runGroups = $derived(buildRunGroups(runs));

	const TUTORIAL_STORAGE_KEY = 'chatvis_tutorial_done';
	let tutorialOpen = $state(false);
	let tutorialStepIndex = $state(0);

	const tutorialSteps: TutorialStep[] = [
		{
			id: 'goals-hierarchy',
			target: 'outcomes',
			title: 'Goals & hierarchy',
			body: 'Read each goal’s name and how goals are nested in the hierarchy. Try clicking the mini timeline graph: the horizontal axis is conversation time.',
			bodyHtml:
				'<p>Read each goal’s name and how goals are nested in the hierarchy. This panel shows how goals and their relationships change over the conversation—earlier toward the top, later toward the bottom.</p>' +
				'<p>Try clicking the mini timeline graph in this panel. The horizontal axis is conversation time (earlier to later turns). The top row (bulb) is Shaper activity (idea framing), and the bottom row (wrench) is Executor activity (implementation actions).</p>'
		},
		{
			id: 'outcome',
			target: 'outcomes',
			title: 'Pick a goal',
			body: 'Click a goal on the left to connect requirements, timeline, and chat context.'
		},
		{
			id: 'click-outcome',
			target: 'outcomes',
			title: 'Try it',
			body: 'Now click one goal.'
		},
		{
			id: 'explore-goals-requirements',
			targets: ['outcomes', 'requirements'],
			title: 'Goal & requirements',
			body: 'The left shows your goal; the center lists requirements for that goal. Click different goals on the left to compare requirements.',
			bodyHtml:
				'<p>The <strong>left panel</strong> shows your selected <strong>goal</strong> in the hierarchy. The <strong>center panel</strong> lists <strong>requirements</strong>—the constraints that must be satisfied for that goal to be executed.</p>' +
				'<p>Click <strong>different goals</strong> on the left to see how the requirement list changes for each.</p>'
		},
		{
			id: 'explore-reqs-timeline-chat',
			noBackdropDim: true,
			title: 'Requirements, timeline & chat',
			body: 'Click different requirements. The vertical line is the timeline (conversation progress). The chat log shows related messages; expand Indirect influence when present.',
			bodyHtml:
				'<p>Click <strong>different requirements</strong> in the center to explore.</p>' +
				'<p>The long <strong>vertical line</strong> in the requirements column is the <strong>timeline</strong> (<strong>conversation progress</strong>)—earlier turns toward the top, later toward the bottom.</p>' +
				'<p>When you select a requirement, the <strong>chat log</strong> on the right scrolls to the related conversation. Try expanding <strong>Indirect influence</strong> entries to read their explanations.</p>'
		},
		{
			id: 'timeline',
			target: 'timeline-marker',
			title: 'Timeline marker',
			body: 'A circular marker represents an action at that turn. Click one marker to jump to the related action in the chat log.'
		},
		{
			id: 'click-timeline-marker',
			target: 'timeline-marker',
			title: 'Try it',
			body: 'Click a marker to inspect the linked action.'
		},
		{
			id: 'chat',
			target: 'chat',
			title: 'Chat log',
			body: 'Use the right-hand chat panel to review the selected action or turn in full conversation context.'
		},
		{
			id: 'wrap-up-google-form',
			noBackdropDim: true,
			title: 'Keep exploring',
			body: 'Feel free to click more requirements.',
			bodyHtml:
				'<p>Feel free to keep clicking <strong>requirements</strong> in the center to explore further.'
		}
	];

	const currentTutorialTarget = $derived(tutorialSteps[tutorialStepIndex]?.id ?? null);

	function closeTutorial() {
		tutorialOpen = false;
		if (typeof localStorage !== 'undefined') localStorage.setItem(TUTORIAL_STORAGE_KEY, '1');
	}
	function nextTutorialStep() {
		if (tutorialStepIndex >= tutorialSteps.length - 1) closeTutorial();
		else tutorialStepIndex += 1;
	}
	function backTutorialStep() {
		if (tutorialStepIndex > 0) tutorialStepIndex -= 1;
	}

	function jumpToTutorialStep(stepId: string) {
		const idx = tutorialSteps.findIndex((s) => s.id === stepId);
		if (idx >= 0) tutorialStepIndex = idx;
	}

	function isStepAlreadyDone(stepId: string): boolean {
		switch (stepId) {
			case 'click-outcome':
				return selectedOutcomeId != null;
			case 'click-timeline-marker':
				return selectedActionId != null || focusedChatTurnId != null;
			default:
				return false;
		}
	}

	$effect(() => {
		if (!tutorialOpen) return;
		const step = tutorialSteps[tutorialStepIndex];
		if (!step) return;

		if (
			step.id === 'explore-reqs-timeline-chat' &&
			selectedOutcome != null &&
			selectedOutcome.requirements.length === 0
		) {
			jumpToTutorialStep('timeline');
			return;
		}

		// If user already did the requested action, auto-advance to keep tutorial in sync.
		if (isStepAlreadyDone(step.id)) {
			if (tutorialStepIndex >= tutorialSteps.length - 1) closeTutorial();
			else tutorialStepIndex += 1;
		}
	});

	async function loadData(run: string | null) {
		loading = true;
		error = null;
		try {
			const [t, c, rows, analysis, actionMap, reqContrib, reqStatus, utterances, utteranceMap, outcomeActionMapData, intentMap] = await Promise.all([
				loadOutcomeRequirementTree(run),
				loadOutcomeContributions(run),
				loadRequirements(run),
				loadNodeAnalysis(run),
				loadRequirementActionMap(run),
				loadRequirementContributions(run),
				loadRequirementStatus(run),
				loadUtteranceList(run),
				loadActionUtteranceMap(run),
				loadOutcomeActionMap(run),
				loadOutcomeIntentionMap(run)
			]);
			outcomeActionMap = outcomeActionMapData;
			outcomeIntentionMap = intentMap;
			tree = t;
			contributions = c;
			requirementRows = rows;
			nodeAnalysisData = analysis;
			requirementActionMap = actionMap;
			requirementContributions = reqContrib;
			requirementStatusById = reqStatus;
			utteranceList = utterances;
			actionUtteranceMap = utteranceMap;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			tree = null;
			contributions = null;
			requirementRows = null;
			nodeAnalysisData = null;
			requirementActionMap = {};
			requirementContributions = {};
			requirementStatusById = {};
			utteranceList = null;
			actionUtteranceMap = {};
			outcomeIntentionMap = {};
		} finally {
			loading = false;
		}
	}

	onMount(async () => {
		try {
			const res = await fetch(`${import.meta.env.BASE_URL}api/runs`);
			const data = res.ok ? (await res.json()) as { runs: string[] } : { runs: [] };
			runs = data.runs ?? [];
			// Default to first run (wine3 when VITE_DATA_BASE is set) so it loads immediately
			if (runs.length > 0 && selectedRunValue == null) {
				const firstRun = runs[0];
				selectedRunValue = firstRun;
				const run = firstRun === '__default__' ? null : firstRun;
				selectedRun.set(run);
				await loadData(run);
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			runsLoading = false;
		}
		// First visit: open the guide automatically (Help uses the same flow).
		try {
			if (typeof localStorage !== 'undefined' && localStorage.getItem(TUTORIAL_STORAGE_KEY) !== '1') {
				tutorialOpen = true;
				tutorialStepIndex = 0;
			}
		} catch {
			/* private mode / no storage */
		}
	});

	async function onRunChange(newRun: string) {
		if (!newRun) return;
		const run = newRun === '__default__' ? null : newRun;
		selectedRunValue = newRun;
		selectedRun.set(run);
		selectedOutcomeId = null;
		selectedRequirementId = null;
		selectedActionId = null;
		hoveredPanelTurnId = null;
		error = null;
		await loadData(run);
	}

	function getContributionForOutcome(outcomeId: string): OutcomeContribution | null {
		const outcome = tree?.outcomes.find((o) => o.outcome_id === outcomeId);
		if (!contributions) return null;
		// When outcome has no requirements: use outcome_action_map only, compute rate from actions (role + speaker)
		if (outcome && outcome.requirements.length === 0) {
			const mapEntry = outcomeActionMap?.outcome_action_map[outcomeId];
			const mapActions = mapEntry?.actions ?? [];
			if (mapActions.length > 0) {
				const userActions = mapActions.filter((a) => !isAssistantSpeakerId(a.speaker));
				const assistantActions = mapActions.filter((a) => isAssistantSpeakerId(a.speaker));
				const totalActions = mapActions.length;
				const userRate = userActions.length / totalActions;
				const asstRate = assistantActions.length / totalActions;
				const ROLES = ['SHAPER', 'EXECUTOR'] as const;
				const emptyDetail = (): RoleContributionDetail => ({
					M_dir: 0,
					M_ind: 0,
					M_total: 0,
					count: 0,
					action_examples: []
				});
				const roleContribUser: Record<string, RoleContributionDetail> = {};
				const roleContribAsst: Record<string, RoleContributionDetail> = {};
				const roleFreqUser: Record<string, number> = {};
				const roleFreqAsst: Record<string, number> = {};
				for (const role of ROLES) {
					const uCount = userActions.filter((a) => (a.role ?? 'EXECUTOR') === role).length;
					const aCount = assistantActions.filter((a) => (a.role ?? 'EXECUTOR') === role).length;
					const roleTotal = uCount + aCount;
					const ru = roleTotal > 0 ? uCount / roleTotal : 0;
					const ra = roleTotal > 0 ? aCount / roleTotal : 0;
					roleContribUser[role] = { ...emptyDetail(), M_total: ru, count: uCount };
					roleContribAsst[role] = { ...emptyDetail(), M_total: ra, count: aCount };
					roleFreqUser[role] = uCount;
					roleFreqAsst[role] = aCount;
				}
				return {
					thread_id: outcomeSortKey(outcomeId),
					outcome_id: outcomeId,
					outcome: outcome.outcome ?? outcomeId,
					speaker_contributions: {
						user: {
							direct_influence: userRate,
							indirect_influence: 0,
							total_influence: userRate,
							requirement_count: 0
						},
						assistant: {
							direct_influence: asstRate,
							indirect_influence: 0,
							total_influence: asstRate,
							requirement_count: 0
						}
					},
					role_contributions: { user: roleContribUser, assistant: roleContribAsst },
					role_frequencies: { user: roleFreqUser, assistant: roleFreqAsst },
					requirements: []
				};
			}
			return null;
		}
		// Outcome has requirements: use output_contributions.json rate as-is
		const entry = contributions[outcomeId] ?? null;
		return entry;
	}

	const orderedOutcomes = $derived(
		!tree
			? []
			: [...tree.outcomes].sort(
					(a, b) => compareOutcomeIds(a.outcome_id, b.outcome_id)
				)
			);

	/** Which outcomes to show in the hierarchy. By default we hide outcomes that have no requirements AND no actions (empty box).
	 *  When outcomeIntentionMap has entries, show all outcomes so the full outcome/intention hierarchy is visible (e.g. intent_outcome_map has 8 outcomes → show all 8). */
	const visibleOutcomes = $derived.by(() => {
		if (!orderedOutcomes.length) return [];
		const hasIntentionMap = Object.keys(outcomeIntentionMap).length > 0;
		if (hasIntentionMap) {
			// Show all outcomes so hierarchy reflects full intent_outcome_map
			return orderedOutcomes;
		}
		return orderedOutcomes.filter((o) => {
			if (o.requirements.length > 0) return true;
			const actions = outcomeActionMap?.outcome_action_map[o.outcome_id]?.actions ?? [];
			return actions.length > 0;
		});
	});

	const selectedOutcome = $derived(
		selectedOutcomeId && tree
			? tree.outcomes.find((o) => o.outcome_id === selectedOutcomeId) ?? null
			: null
	);

	function getRequirementStatus(reqId: string): { is_executed: boolean; is_dismissed: boolean; dismissed_at_turn: number | null } | null {
		const exact = requirementStatusById[reqId];
		if (exact) return exact;
		const noPrefix = reqId.replace(/^r/, '');
		const withPrefix = /^r/.test(reqId) ? reqId : `r${reqId}`;
		return requirementStatusById[noPrefix] ?? requirementStatusById[withPrefix] ?? null;
	}

	const selectedOutcomeReqStatusOverview = $derived.by(() => {
		if (!selectedOutcome) return [];
		return selectedOutcome.requirements.map((reqId) => {
			const st = getRequirementStatus(reqId);
			return {
				id: reqId,
				is_executed: st ? st.is_executed : null,
				is_dismissed: st ? st.is_dismissed : null,
				dismissed_at_turn: st ? st.dismissed_at_turn : null
			};
		});
	});

	type OutcomeRelationshipView = {
		prev_action_id: string;
		prev_outcome_id: string;
		relationship_type: string;
		relationship_score: number | null;
		explanation: string;
		similarity: number | null;
		prev_outcome_title: string;
		prev_outcome_index: number;
	};

	const selectedOutcomeRelationships = $derived.by((): OutcomeRelationshipView[] => {
		if (!selectedOutcomeId) return [];
		const rows =
			outcomeActionMap?.outcome_action_map?.[selectedOutcomeId]?.prev_action_relationships ?? [];
		return rows
			.map((r) => {
				const prevOutcome = tree?.outcomes.find((o) => o.outcome_id === r.prev_outcome_id);
				const prevFromMap = outcomeActionMap?.outcome_action_map?.[r.prev_outcome_id];
				const prevTitle = prevOutcome?.outcome ?? prevFromMap?.description ?? r.prev_outcome_id;
				return {
					prev_action_id: r.prev_action_id,
					prev_outcome_id: r.prev_outcome_id,
					relationship_type: r.relationship_type ?? 'UNKNOWN',
					relationship_score: r.relationship_score ?? null,
					explanation: r.explanation ?? '',
					similarity: typeof r.similarity === 'number' ? r.similarity : null,
					prev_outcome_title: prevTitle,
					prev_outcome_index: outcomeNumber(r.prev_outcome_id)
				};
			})
			.sort((a, b) => {
				const scoreDiff = (b.relationship_score ?? -1) - (a.relationship_score ?? -1);
				if (scoreDiff !== 0) return scoreDiff;
				const simDiff = (b.similarity ?? -1) - (a.similarity ?? -1);
				if (simDiff !== 0) return simDiff;
				return a.prev_outcome_index - b.prev_outcome_index;
			});
	});

	const selectedOutcomeRelationshipSummary = $derived.by(() => {
		const summary: Record<string, number> = {};
		for (const r of selectedOutcomeRelationships) {
			const key = (r.relationship_type || 'UNKNOWN').toUpperCase();
			summary[key] = (summary[key] ?? 0) + 1;
		}
		return summary;
	});

	const otherOutcomes = $derived(
		selectedOutcomeId ? visibleOutcomes.filter((o) => o.outcome_id !== selectedOutcomeId) : visibleOutcomes
			);

	const topLevelGoalCount = $derived.by(() => {
		if (!visibleOutcomes.length) return 0;
		const childIds = new Set(dependencyEdges.map((e) => e.childId));
		return visibleOutcomes.filter((o) => !childIds.has(o.outcome_id)).length;
	});

	/** Normalize output id (e.g. outcome_2_0) to outcome_id (outcome_2) for matching tree.outcomes */
	function outputIdToOutcomeId(outputId: string): string {
		const lastUnderscore = outputId.lastIndexOf('_');
		if (lastUnderscore <= 0) return outputId;
		const suffix = outputId.slice(lastUnderscore + 1);
		if (!/^\d+$/.test(suffix)) return outputId;
		const head = outputId.slice(0, lastUnderscore);
		// Keep canonical outcome ids like "outcome_1" intact.
		if (!head.includes('_')) return outputId;
		return head;
	}

	/** Deep-normalize output id (e.g. outcome_4_0_0 -> outcome_4) for child-start-only matching. */
	function outputIdToOutcomeIdDeep(outputId: string): string {
		let normalized = outputId;
		while (/_\d+$/.test(normalized)) normalized = normalized.replace(/_\d+$/, '');
		return normalized;
	}
	function isHierarchicalChildOutcomeId(parentOutcomeId: string, childOutcomeId: string): boolean {
		if (parentOutcomeId === childOutcomeId) return false;
		const escapedParent = parentOutcomeId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
		// Accept only strict hierarchical descendants:
		// outcome_4 -> outcome_4a / outcome_4a_0 / outcome_4_1 / outcome_4_1_0 ...
		// Reject semantic/related links like outcome_1 -> outcome_4.
		const hierarchyPattern = new RegExp(`^${escapedParent}(?:[a-z]|_\\d+)(?:_|$)`, 'i');
		return hierarchyPattern.test(childOutcomeId);
	}
	function truncate(s: string, n: number): string {
		return s.length <= n ? s : s.slice(0, n) + '…';
	}

	/** Resolved dependency edges with outcome_id and index for display; only when we have both outcomes in tree */
	const dependencyEdges = $derived.by(() => {
		const dep = tree?.outputs_dependency;
		if (!dep?.length || !tree) return [];
		return dep
			.map((e) => {
				const parentId = outputIdToOutcomeId(e.parent_id);
				const childId = outputIdToOutcomeId(e.child_id);
				const parent = tree!.outcomes.find((o) => o.outcome_id === parentId);
				const child = tree!.outcomes.find((o) => o.outcome_id === childId);
				if (!parent || !child) return null;
				const parentIdx = visibleOutcomes.findIndex((o) => o.outcome_id === parentId);
				const childIdx = visibleOutcomes.findIndex((o) => o.outcome_id === childId);
				const parentIndex = parentIdx >= 0 ? parentIdx + 1 : outcomeSortKey(parentId);
				const childIndex = childIdx >= 0 ? childIdx + 1 : outcomeSortKey(childId);
				return {
					parentId,
					childId,
					parentTitle: parent.outcome,
					childTitle: child.outcome,
					parentIndex,
					childIndex
				};
			})
			.filter((x): x is NonNullable<typeof x> => x != null);
	});

	/** outcome_id → sorted list of related outcome numbers (for "related: 2, 5" above outcome box) */
	/** Unique nodes from dependency edges, sorted by outcome index, for SVG diagram */
	const dependencyDiagramNodes = $derived.by(() => {
		const ids = new Set<string>();
		for (const e of dependencyEdges) {
			ids.add(e.parentId);
			ids.add(e.childId);
		}
		const nodes: { outcome_id: string; index: number; title: string }[] = [];
		for (const id of ids) {
			const edge = dependencyEdges.find((e) => e.parentId === id || e.childId === id);
			const index = edge?.parentId === id ? edge.parentIndex : edge!.childIndex;
			const title = tree?.outcomes.find((o) => o.outcome_id === id)?.outcome ?? (edge?.parentId === id ? edge.parentTitle : edge!.childTitle);
			nodes.push({ outcome_id: id, index, title });
		}
		nodes.sort((a, b) => a.index - b.index);
		return nodes;
	});

	/** Per-outcome dependency: dependsOn = parents (this outcome depends on them), requiredBy = children (they depend on this) */
	type DepLink = { outcome_id: string; index: number; title: string };
	const dependencyByOutcomeId = $derived.by(() => {
		const map = new Map<string, { dependsOn: DepLink[]; requiredBy: DepLink[] }>();
		if (!tree) return map;
		for (const o of tree.outcomes) {
			map.set(o.outcome_id, { dependsOn: [], requiredBy: [] });
		}
		for (const e of dependencyEdges) {
			const childEntry = map.get(e.childId);
			if (childEntry) {
				childEntry.dependsOn.push({
					outcome_id: e.parentId,
					index: e.parentIndex,
					title: e.parentTitle
				});
			}
			const parentEntry = map.get(e.parentId);
			if (parentEntry) {
				parentEntry.requiredBy.push({
					outcome_id: e.childId,
					index: e.childIndex,
					title: e.childTitle
				});
			}
		}
		return map;
	});

	const selectedOutcomeChildGoalStarts = $derived.by(() => {
		if (!selectedOutcomeId || !tree) return [];
		const treeData = tree;
		const dep = tree.outputs_dependency ?? [];
		if (dep.length === 0) return [];
		const childrenByParentOutcomeId = new Map<string, string[]>();
		for (const edge of dep) {
			const parentOutcomeId = outputIdToOutcomeId(edge.parent_id);
			const childOutputId = edge.child_id;
			const childOutcomeId = outputIdToOutcomeId(childOutputId);
			if (parentOutcomeId === childOutcomeId) continue;
			if (!isHierarchicalChildOutcomeId(parentOutcomeId, childOutcomeId)) continue;
			const list = childrenByParentOutcomeId.get(parentOutcomeId) ?? [];
			if (!list.includes(childOutputId)) list.push(childOutputId);
			childrenByParentOutcomeId.set(parentOutcomeId, list);
		}
		const selectedOutcomeBaseId = outputIdToOutcomeId(selectedOutcomeId);
		const descendantOutputIds: string[] = [];
		const visitedOutcomeIds = new Set<string>([selectedOutcomeBaseId]);
		const queue: string[] = [selectedOutcomeBaseId];
		while (queue.length > 0) {
			const current = queue.shift()!;
			const directChildren = childrenByParentOutcomeId.get(current) ?? [];
			for (const childOutputId of directChildren) {
				descendantOutputIds.push(childOutputId);
				const childOutcomeId = outputIdToOutcomeId(childOutputId);
				if (!visitedOutcomeIds.has(childOutcomeId)) {
					visitedOutcomeIds.add(childOutcomeId);
					queue.push(childOutcomeId);
				}
			}
		}
		return descendantOutputIds
			.map((childOutputId) => {
				const childOutcomeId = outputIdToOutcomeId(childOutputId);
				const childOutcomeIdDeep = outputIdToOutcomeIdDeep(childOutputId);
				const goal =
					treeData.outcomes.find((o) => o.outcome_id === childOutcomeId) ??
					treeData.outcomes.find((o) => o.outcome_id === childOutcomeIdDeep);
				if (!goal || goal.created_at == null) return null;
				return {
					goalId: childOutputId,
					goalTitle: goal.outcome ?? childOutcomeId,
					turn: goal.created_at
				};
			})
			.filter((row): row is { goalId: string; goalTitle: string; turn: number } => row != null)
			.sort((a, b) => a.turn - b.turn || a.goalId.localeCompare(b.goalId));
	});
	const selectedOutcomeChildGoalCount = $derived.by(() => {
		const ids = new Set(selectedOutcomeChildGoalStarts.map((row) => row.goalId));
		return ids.size;
	});

	const requirementTextsByOutcome = $derived.by(() => {
		const map = new Map<string, string[]>();
		if (!tree || !requirementRows) return map;
		const contentByReqId = buildRequirementContentMap(requirementRows);
		for (const outcome of tree.outcomes) {
			const texts: string[] = [];
			for (const reqId of outcome.requirements) {
				const text = getRequirementContent(reqId, contentByReqId);
				if (text) texts.push(text);
			}
			map.set(outcome.outcome_id, texts);
		}
		return map;
	});

	// Superseded = requirement IDs that were revised (appear as related_prev in MODIFY/REVISE)
	const supersededIdsByOutcome = $derived.by(() => {
		const map = new Map<string, Set<string>>();
		if (!requirementRows || !tree) return map;
		for (const outcome of tree.outcomes) {
			const superseded = new Set<string>();
			for (const row of requirementRows) {
				if (row.outcome_id !== outcome.outcome_id) continue;
				if (row.operation_type !== 'MODIFY') continue;
				const prev = row.related_prev_requirement;
				if (Array.isArray(prev)) prev.forEach((id) => superseded.add(id));
				else if (typeof prev === 'string') superseded.add(prev);
			}
			map.set(outcome.outcome_id, superseded);
		}
		return map;
	});

	/** Per-requirement User vs Assistant contribution (from requirement_contributions.json) for pie icon */
	type ReqContrib = {
		user: number; assistant: number;
		userShaper: number; assistantShaper: number;
		userShaperDir: number; userShaperInd: number;
		assistantShaperDir: number; assistantShaperInd: number;
		userExecutor: number; assistantExecutor: number;
	};
	const requirementContributionByReqId = $derived.by((): Record<string, ReqContrib> => {
		const normalizeRole = (r: string): 'SHAPER' | 'EXECUTOR' => {
			const u = r.toUpperCase();
			if (u === 'CREATOR' || u === 'SHAPER') return 'SHAPER';
			return 'EXECUTOR';
		};
		const out: Record<string, ReqContrib> = {};
		for (const [reqId, entry] of Object.entries(requirementContributions)) {
			const e = entry as { overall?: { user?: { rate?: number }; assistant?: { rate?: number } }; role_contributions?: Record<string, Record<string, { M_total?: number; M_dir?: number; M_ind?: number }>> } | null;
			if (!e) continue;
			const overall = e.overall;
			if (!overall || typeof overall !== 'object') continue;
			const userRate = Number(overall.user?.rate ?? 0);
			const assistantRate = Number(overall.assistant?.rate ?? 0);
			let userShaper = 0, assistantShaper = 0;
			let userShaperDir = 0, userShaperInd = 0, assistantShaperDir = 0, assistantShaperInd = 0;
			let userExecutor = 0, assistantExecutor = 0;
			const rc = e.role_contributions;
			if (rc && typeof rc === 'object') {
				for (const [speaker, roles] of Object.entries(rc)) {
					if (!roles || typeof roles !== 'object') continue;
					for (const [role, data] of Object.entries(roles)) {
						const d = data as { M_total?: number; M_dir?: number; M_ind?: number };
						const m = Number(d?.M_total ?? 0);
						const mDir = Number(d?.M_dir ?? 0);
						const mInd = Number(d?.M_ind ?? 0);
						const normed = normalizeRole(role);
						if (speaker === 'user') {
							if (normed === 'SHAPER') { userShaper += m; userShaperDir += mDir; userShaperInd += mInd; }
							else userExecutor += m;
						} else if (speaker === 'assistant') {
							if (normed === 'SHAPER') { assistantShaper += m; assistantShaperDir += mDir; assistantShaperInd += mInd; }
							else assistantExecutor += m;
						}
					}
				}
			}
			const val: ReqContrib = { user: userRate, assistant: assistantRate, userShaper, assistantShaper, userShaperDir, userShaperInd, assistantShaperDir, assistantShaperInd, userExecutor, assistantExecutor };
			out[reqId] = val;
			if (/^r\d+$/.test(reqId)) out[reqId.replace(/^r/, '')] = val;
			if (/^\d+$/.test(reqId)) out['r' + reqId] = val;
		}
		return out;
	});

	/** Requirement row contribution bar: Slack = two teammate colors (Eunsu excluded from the pair); else user/assistant. */
	const contribBarByReqId = $derived.by((): Record<string, ContribBarDisplay> => {
		const out: Record<string, ContribBarDisplay> = {};
		for (const [reqId, entry] of Object.entries(requirementContributions)) {
			const v = buildContribBarFromRequirementContributionEntry(entry);
			out[reqId] = v;
			if (/^r\d+$/.test(reqId)) out[reqId.replace(/^r/, '')] = v;
			if (/^\d+$/.test(reqId)) out['r' + reqId] = v;
		}
		return out;
	});

	// Final requirement IDs per outcome (exclude superseded; show only latest in each revision chain)
	const finalRequirementIdsByOutcome = $derived.by(() => {
		const map = new Map<string, string[]>();
		if (!tree) return map;
		for (const outcome of tree.outcomes) {
			const superseded = supersededIdsByOutcome.get(outcome.outcome_id);
			const finalIds = outcome.requirements.filter((id) => !superseded?.has(id));
			map.set(outcome.outcome_id, finalIds);
		}
		return map;
	});

	const finalRequirementTextsByOutcome = $derived.by(() => {
		const map = new Map<string, string[]>();
		if (!tree || !requirementRows) return map;
		const contentByReqId = buildRequirementContentMap(requirementRows);
		for (const outcome of tree.outcomes) {
			const finalIds = finalRequirementIdsByOutcome.get(outcome.outcome_id) ?? [];
			const texts = finalIds
				.map((reqId) => getRequirementContent(reqId, contentByReqId))
				.filter((t): t is string => t != null);
			map.set(outcome.outcome_id, texts);
		}
		return map;
	});

	/** Per-outcome revision chains for requirements that have MODIFY history. Used for stacked display in outcome box. */
	const requirementChainsByOutcome = $derived.by(() => {
		const map = new Map<string, { currentId: string; currentText: string; history: { id: string; text: string }[] }[]>();
		if (!tree || !requirementRows) return map;
		const contentByReqId = buildRequirementContentMap(requirementRows);
		const getContent = (id: string) => getRequirementContent(id, contentByReqId) ?? '';
		for (const outcome of tree.outcomes) {
			const outcomeId = outcome.outcome_id;
			const rowsO = requirementRows.filter((r) => r.outcome_id === outcomeId);
			const finalIds = finalRequirementIdsByOutcome.get(outcomeId) ?? [];
			const chains: { currentId: string; currentText: string; history: { id: string; text: string }[] }[] = [];
			for (const finalId of finalIds) {
				const chainRows: { id: string; text: string }[] = [];
				let id: string = finalId;
				while (true) {
					const row = rowsO.find((r) => r.requirement_id === id);
					if (!row) break;
					chainRows.push({ id: row.requirement_id, text: getContent(row.requirement_id) });
					if (row.operation_type !== 'MODIFY') break;
					const prev = row.related_prev_requirement;
					// related_prev_requirement can be string or array (e.g. ["r6"] in requirement_relations.jsonl)
					const prevId = Array.isArray(prev) && prev.length > 0 ? prev[0] : typeof prev === 'string' ? prev : null;
					if (prevId == null) break;
					id = prevId;
				}
				// chainRows is [newest, ..., oldest]. We want history = previous versions, current = latest.
				chainRows.reverse(); // [oldest, ..., newest]
				const currentId = chainRows[chainRows.length - 1]?.id ?? finalId;
				const currentText = chainRows[chainRows.length - 1]?.text ?? getContent(finalId);
				const history = chainRows.slice(0, -1).map(({ id: hid, text }) => ({ id: hid, text }));
				chains.push({ currentId, currentText, history });
			}
			map.set(outcomeId, chains);
		}
		return map;
	});


	// Selected requirement content for center panel
	const selectedRequirementContent = $derived.by(() => {
		if (!selectedRequirementId || !requirementRows) return null;
		const contentByReqId = buildRequirementContentMap(requirementRows);
		return getRequirementContent(selectedRequirementId, contentByReqId);
	});


	/** requirement_id → creation turn (0-based) from requirementRows */
	const requirementCreationTurnByReqId = $derived.by((): Record<string, number> => {
		const map: Record<string, number> = {};
		if (!requirementRows) return map;
		for (const row of requirementRows) map[row.requirement_id] = row.t;
		return map;
	});


	// Requirement IDs to show on timeline: when one is selected, only that (avoids Earlier/Current overlap); else current versions only
	const timelineRequirementIds = $derived.by((): string[] => {
		if (!selectedOutcome) return [];
		const finalIds = finalRequirementIdsByOutcome.get(selectedOutcome.outcome_id) ?? selectedOutcome.requirements;
		if (selectedRequirementId) return [selectedRequirementId];
		return finalIds;
	});

	/** When outcome has no requirements, use this turn to show actions up to this outcome (fallback when no outcome_action_map). */
	const outcomeTurnIdWhenNoReqs = $derived.by((): number | null => {
		if (!selectedOutcome || selectedOutcome.requirements.length > 0) return null;
		return selectedOutcome.created_at ?? null;
	});

	/**
	 * Per-goal actions from outcome_action_map.json (roles, evidence). Used for chat/timeline even when
	 * this goal also has requirements — otherwise user SHAPER etc. only in OAM would never reach the UI.
	 */
	const outcomeActionsFromMap = $derived.by((): OutcomeActionItem[] | null => {
		if (!selectedOutcome || !outcomeActionMap?.outcome_action_map) return null;
		const entry = outcomeActionMap.outcome_action_map[selectedOutcome.outcome_id];
		const actions = entry?.actions;
		return actions && actions.length > 0 ? actions : null;
	});

	// Action IDs to highlight on timeline when a requirement is selected
	const highlightedActionIds = $derived.by((): Set<string> | null => {
		if (!selectedRequirementId || !requirementActionMap[selectedRequirementId]) return null;
		const entry = requirementActionMap[selectedRequirementId];
		const set = new Set<string>();
		const origin = Array.isArray(entry?.origin_actions) ? entry.origin_actions : [];
		for (const a of origin) set.add(a.action_id);
		const related = Array.isArray(entry?.related_actions) ? entry.related_actions : [];
		for (const a of related) {
			if (a && typeof a === 'object' && 'action_id' in a) set.add((a as { action_id: string }).action_id);
		}
		return set.size > 0 ? set : null;
	});

	function getRequirementActionEntryForUi(reqId: string | null) {
		if (!reqId) return null;
		return (
			requirementActionMap[reqId] ??
			requirementActionMap[reqId.replace(/^r/i, '')] ??
			requirementActionMap[`r${reqId.replace(/^r/i, '')}`] ??
			null
		);
	}

	const selectedRequirementInfluenceKinds = $derived.by(() => {
		const entry = getRequirementActionEntryForUi(selectedRequirementId);
		if (!entry) return { hasDirect: false, hasIndirect: false };
		const origin = Array.isArray(entry.origin_actions) ? entry.origin_actions : [];
		const impl = Array.isArray((entry as { implementation_actions?: unknown[] }).implementation_actions)
			? (entry as { implementation_actions: unknown[] }).implementation_actions
			: [];
		const contrib = Array.isArray((entry as { contributing_actions?: unknown[] }).contributing_actions)
			? (entry as { contributing_actions: unknown[] }).contributing_actions
			: [];
		const related = Array.isArray(entry.related_actions) ? entry.related_actions : [];
		const hasIndirect = related.some((a) =>
			!!a &&
			typeof a === 'object' &&
			'influence' in a &&
			(a as { influence?: string }).influence === 'indirect'
		);
		const hasDirect =
			origin.length > 0 ||
			impl.length > 0 ||
			contrib.length > 0 ||
			related.some((a) =>
				!!a &&
				typeof a === 'object' &&
				(('influence' in a && (a as { influence?: string }).influence === 'direct') ||
					('relationship_type' in a &&
						['DIRECT', 'DIRECT_CONNECTION'].includes(
							String((a as { relationship_type?: string }).relationship_type ?? '').toUpperCase()
						)))
			);
		return { hasDirect, hasIndirect };
	});

	// When selected requirement has multiple versions: show "Earlier" or "Current" in actions panel
	const selectedRequirementVersionLabel = $derived.by((): 'Earlier' | 'Current' | null => {
		if (!selectedRequirementId || !selectedOutcome) return null;
		const chains = requirementChainsByOutcome.get(selectedOutcome.outcome_id) ?? [];
		for (const chain of chains) {
			if (chain.currentId === selectedRequirementId) return chain.history.length > 0 ? 'Current' : null;
			if (chain.history.some((h) => h.id === selectedRequirementId)) return 'Earlier';
		}
		return null;
	});

	// When viewing Current: action_ids that appear in any Earlier version (so we can show "From Earlier" on those)
	const inheritedFromEarlierActionIds = $derived.by((): Set<string> | null => {
		if (!selectedRequirementId || !selectedOutcome) return null;
		const chains = requirementChainsByOutcome.get(selectedOutcome.outcome_id) ?? [];
		for (const chain of chains) {
			if (chain.currentId !== selectedRequirementId || chain.history.length === 0) continue;
			const set = new Set<string>();
			for (const earlier of chain.history) {
				const entry = requirementActionMap[earlier.id];
				if (!entry) continue;
				const origin = Array.isArray(entry.origin_actions) ? entry.origin_actions : [];
				for (const a of origin) set.add(a.action_id);
				const related = Array.isArray(entry.related_actions) ? entry.related_actions : [];
				for (const a of related) {
					if (a && typeof a === 'object' && 'action_id' in a)
						set.add((a as { action_id: string }).action_id);
				}
			}
			return set.size > 0 ? set : null;
		}
		return null;
	});

	// Turn when this requirement version was created — Earlier = ADD turn, Current = MODIFY turn.
	const selectedRequirementCreationTurn = $derived.by((): number | null => {
		if (!selectedRequirementId || !requirementRows) return null;
		const row = requirementRows.find((r) => r.requirement_id === selectedRequirementId);
		return row ? row.t : null;
	});

	// When viewing Current: turn where the requirement was first created (earliest version) — show gray star there.
	const earliestVersionCreationTurn = $derived.by((): number | null => {
		if (!selectedRequirementId || !requirementRows || !selectedOutcome) return null;
		const chains = requirementChainsByOutcome.get(selectedOutcome.outcome_id) ?? [];
		for (const chain of chains) {
			if (chain.currentId === selectedRequirementId && chain.history.length > 0) {
				const oldestRow = requirementRows.find((r) => r.requirement_id === chain.history[0].id);
				return oldestRow ? oldestRow.t : null;
			}
		}
		return null;
	});

	// Action IDs that actually created this requirement (origin_actions = creation_action_ids); only these show "Created here!".
	const selectedRequirementCreationActionIds = $derived.by((): Set<string> | null => {
		if (!selectedRequirementId) return null;
		const entry = requirementActionMap[selectedRequirementId];
		const origin = Array.isArray(entry?.origin_actions) ? entry.origin_actions : [];
		if (origin.length === 0) return null;
		return new Set(origin.map((a) => a.action_id));
	});

	// When creation turn has no action on timeline, use one from the selected requirement's map so the star can attach to a marker.
	const creationTurnAction = $derived.by((): { action_id: string; role: string; action_text: string } | null => {
		const turn = selectedRequirementCreationTurn;
		if (turn == null || !selectedRequirementId) return null;
		const entry = requirementActionMap[selectedRequirementId];
		if (!entry) return null;
		const atTurn = (a: { action_id: string }) => {
			const parts = a.action_id.split('-');
			return (parseInt(parts[0], 10) || 0) === turn;
		};
		const origin = Array.isArray(entry.origin_actions) ? entry.origin_actions : [];
		const found = origin.find(atTurn);
		if (found) return { action_id: found.action_id, role: found.role, action_text: found.action_text };
		const related = Array.isArray(entry.related_actions) ? entry.related_actions : [];
		for (const a of related) {
			if (a && typeof a === 'object' && 'action_id' in a && 'action_text' in a && atTurn(a as { action_id: string }))
				return { action_id: (a as { action_id: string }).action_id, role: (a as { role?: string }).role ?? '', action_text: (a as { action_text: string }).action_text };
		}
		return null;
	});



	function onResizeStart(e: MouseEvent) {
		e.preventDefault();
		isResizing = true;
		const startX = e.clientX;
		const startW = leftColWidth;
		const onMove = (ev: MouseEvent) => {
			const delta = ev.clientX - startX;
			leftColWidth = Math.max(260, Math.min(720, startW + delta));
		};
		const onUp = () => {
			isResizing = false;
			window.removeEventListener('mousemove', onMove);
			window.removeEventListener('mouseup', onUp);
		};
		window.addEventListener('mousemove', onMove);
		window.addEventListener('mouseup', onUp);
	}

	function selectOutcome(outcomeId: string) {
		const outcome = tree?.outcomes.find((o) => o.outcome_id === outcomeId) ?? null;
		const hasRequirements = (outcome?.requirements?.length ?? 0) > 0;
		selectedIntentLabel = null;
		selectedOutcomeId = outcomeId;
		hierarchyHighlightedOutcomeId = null;
		selectedRequirementId = null;
		focusedChatTurnId = null;
		hoveredPanelTurnId = null;
		if (tutorialOpen && tutorialSteps[tutorialStepIndex]?.id === 'click-outcome') {
			if (!hasRequirements) {
				jumpToTutorialStep('timeline');
			} else {
				tutorialStepIndex += 1;
			}
		}
	}

	function selectOutcomeFromOutputId(outputId: string) {
		const normalizedOutcomeId = outputIdToOutcomeId(outputId);
		if (!tree?.outcomes.some((o) => o.outcome_id === normalizedOutcomeId)) return;
		// Keep current (parent) timeline context, only highlight child in left hierarchy.
		hierarchyHighlightedOutcomeId = normalizedOutcomeId;
	}


	const chatLinkedRequirementIds = $derived.by(() => {
		// Outcome mode: selected outcome only
		if (selectedOutcome) {
			const ids = new Set<string>(selectedOutcome.requirements ?? []);
			return Array.from(ids);
		}
		// Intent mode: all outcomes that belong to selected intent
		if (selectedIntentLabel) {
			const ids = new Set<string>();
			for (const o of visibleOutcomes) {
				const label = (outcomeIntentionMap[o.outcome_id] ?? 'Unmapped').trim() || 'Unmapped';
				if (label !== selectedIntentLabel) continue;
				for (const r of o.requirements ?? []) ids.add(r);
			}
			return Array.from(ids);
		}
		return [];
	});

	function clearSelection() {
		selectedIntentLabel = null;
		selectedOutcomeId = null;
		hierarchyHighlightedOutcomeId = null;
		selectedRequirementId = null;
		focusedChatTurnId = null;
		hoveredPanelTurnId = null;
	}

	function sameRequirementId(a: string | null, b: string): boolean {
		if (a == null) return false;
		if (a === b) return true;
		const n = (s: string) => s.trim().replace(/^r/i, '');
		return n(a) === n(b);
	}

	function selectRequirement(reqId: string) {
		if (sameRequirementId(selectedRequirementId, reqId)) {
			selectedRequirementId = null;
			selectedActionId = null;
		} else {
			selectedRequirementId = reqId;
			selectedActionId = null;
			if (tutorialOpen && tutorialSteps[tutorialStepIndex]?.id === 'explore-reqs-timeline-chat') {
				tutorialStepIndex += 1;
			}
		}
		focusedChatTurnId = null;
		hoveredPanelTurnId = null;
	}
	function selectAction(actionId: string | null) {
		selectedActionId = actionId;
		focusedChatTurnId = null;
		if (tutorialOpen && actionId != null && tutorialSteps[tutorialStepIndex]?.id === 'click-timeline-marker') {
			tutorialStepIndex += 1;
		}
	}

	function focusChatTurn(turn: number | null) {
		if (turn == null) return;
		selectedActionId = null;
		focusedChatTurnId = turn;
	}


	function outcomeNumber(outcomeId: string): number {
		return outcomeSortKey(outcomeId);
	}





</script>

<svelte:head>
	<title>{tree?.dialogue_id ?? 'Requirement evolution from dialogue'}</title>
</svelte:head>

<div class="page">
	{#if runsLoading}
		<div class="loading">Loading…</div>
	{:else if runs.length === 0}
		<div class="first-screen">
			<p class="column-hint">No session folders found under the data directory.</p>
		</div>
	{:else if !tree}
		<div class="first-screen">
			<p class="column-hint">Failed to load run data.</p>
			{#if loading}
				<div class="loading loading--inline">Loading…</div>
			{/if}
			{#if error}
				<div class="error">{error}</div>
			{/if}
		</div>
	{:else}
		<header class="header">
			<div class="header__title-row">
				<h1 class="title">{tree.dialogue_id}</h1>
				<div class="run-picker">
					<label class="run-picker__label" for="run-select">Session</label>
					<select
						id="run-select"
						class="run-picker__select"
						value={selectedRunValue ?? ''}
						onchange={(e) => onRunChange((e.currentTarget as HTMLSelectElement).value)}
					>
						{#each runGroups as g (g.category)}
							<optgroup label={g.category}>
								{#each g.items as item (item.path)}
									<option value={item.path}>{item.label}</option>
								{/each}
							</optgroup>
						{/each}
					</select>
				</div>
			</div>
			<div class="header-actions">
				<button
					type="button"
					class="help-trigger"
					onclick={() => { tutorialOpen = true; tutorialStepIndex = 0; }}
					title="Open guide"
				>
					<span class="help-trigger__icon" aria-hidden="true">?</span>
					<span>Help</span>
				</button>
			</div>
		</header>
		<Tutorial
			open={tutorialOpen}
			steps={tutorialSteps}
			stepIndex={tutorialStepIndex}
			onClose={closeTutorial}
			onNext={nextTutorialStep}
			onBack={backTutorialStep}
		/>

		<main class="main three-col-main">
			<div
				class="three-col-grid"
				class:has-selection={selectedOutcomeId}
				class:is-resizing={isResizing}
				style="--left-col-width: {leftColWidth}px"
			>
				<!-- Left column: CHATS (OutcomeHierarchyWithTimeline) -->
				<div
					class="col-left"
					data-tutorial="outcomes"
				>
					{#if tree && visibleOutcomes.length > 0}
						<OutcomeHierarchyWithTimeline
							outcomes={visibleOutcomes}
							outputVersions={tree?.output_versions ?? []}
							dependencyEdges={dependencyEdges}
							selectedOutcomeId={hierarchyHighlightedOutcomeId ?? selectedOutcomeId}
							requirementTextsByOutcome={finalRequirementTextsByOutcome}
							outcomeIntentionMap={outcomeIntentionMap}
							onOutcomeClick={(id: string) => { if (selectedOutcomeId === id) clearSelection(); else selectOutcome(id); }}
							onIntentClick={(intent: string | null) => {
								selectedIntentLabel = intent;
								hierarchyHighlightedOutcomeId = null;
								if (intent != null) {
									selectedOutcomeId = null;
									selectedRequirementId = null;
									selectedActionId = null;
								}
							}}
							outcomeActionMap={outcomeActionMap}
							requirementActionMap={requirementActionMap}
							utteranceList={utteranceList}
							finalTurn={tree?.final_turn ?? 0}
							requirementStatusById={requirementStatusById}
						/>
					{/if}
				</div>

				<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
				<div
					class="resize-handle"
					role="separator"
					aria-orientation="vertical"
					aria-label="Resize sidebar"
					onmousedown={onResizeStart}
				></div>

				<!-- Center column: REQUIREMENTS -->
				<div class="col-center" data-tutorial="requirements">
					{#if selectedOutcome}
						<RequirementsPanel
							outcome={selectedOutcome}
							requirementChains={requirementChainsByOutcome.get(selectedOutcome.outcome_id) ?? []}
							requirementContributionByReqId={requirementContributionByReqId}
							contribBarByReqId={contribBarByReqId}
							requirementStatusOverview={selectedOutcomeReqStatusOverview}
							utteranceList={utteranceList}
							requirementCreationTurnByReqId={requirementCreationTurnByReqId}
							requirementActionMap={requirementActionMap}
							finalTurn={tree?.final_turn ?? 0}
							childGoalStarts={selectedOutcomeChildGoalStarts}
							selectedRequirementId={selectedRequirementId}
							onRequirementClick={selectRequirement}
							onTurnFocus={focusChatTurn}
							onPanelRowHover={(t) => (hoveredPanelTurnId = t)}
							onChildGoalClick={selectOutcomeFromOutputId}
						/>
					{:else}
						<div class="col-right-empty">
							<p class="column-hint">Select a goal on the left to see its requirements here.</p>
						</div>
					{/if}
				</div>

				<!-- Right column: Chat Log -->
				<div class="col-right" data-tutorial="chat">
					{#if !selectedOutcome && !selectedIntentLabel}
						<div class="empty-guide">
							<span class="empty-guide__tip-head">🧚 Tip:</span>
							<span>
								You collaborated on {topLevelGoalCount} goal{topLevelGoalCount === 1 ? '' : 's'}.
								<span class="empty-guide__sub-note">Select a goal on the left to view your and the agent’s contributions.</span>
							</span>
						</div>
					{:else if selectedOutcome && selectedOutcome.requirements.length === 0}
						<div class="empty-guide empty-guide--soft">
							<span class="empty-guide__tip-head">🧚 Tip:</span>
							{#if selectedOutcomeChildGoalCount > 0}
								<span>
									This goal has {selectedOutcomeChildGoalCount} child goal{selectedOutcomeChildGoalCount === 1 ? '' : 's'}. Click a child goal item on the left pannel or right timeline.
									<span class="empty-guide__sub-note">* Click the left graph to view it larger and read a brief explanation.</span>
								</span>
							{:else}
								This goal has no requirements. Try selecting another goal.
							{/if}
						</div>
					{:else if selectedOutcome && !selectedRequirementId}
						<div class="empty-guide empty-guide--soft">
							<span class="empty-guide__tip-head">🧚 Tip:</span>
							{#if selectedOutcomeChildGoalCount > 0}
								<span>
									This goal has {selectedOutcomeChildGoalCount} child goal{selectedOutcomeChildGoalCount === 1 ? '' : 's'}. Click a child goal item on the left pannel or right timeline.
									<span class="empty-guide__sub-note">* Click the left graph to view it larger and read a brief explanation.</span>
								</span>
							{:else}
								<span>
									Click a requirement in the center to focus on who contributed to its creation and implementation.
									<span class="empty-guide__sub-note">* Requirement is a condition or constraint to realize the goal.</span>
								</span>
							{/if}
						</div>
					{:else if selectedOutcome && selectedRequirementId}
						<div class="empty-guide empty-guide--soft">
							<span class="empty-guide__tip-head">🧚 Tip:</span>
							<span>
								{#if selectedRequirementInfluenceKinds.hasDirect}
									<div class="empty-guide__line">
										<span class="empty-guide__inline-strong">Direct influence</span> means actions that directly affected requirement creation (e.g., explicit asks).
									</div>
								{/if}
								{#if selectedRequirementInfluenceKinds.hasIndirect}
									<div class="empty-guide__line">
										<span class="empty-guide__inline-strong">Indirect influence</span> means actions that affected requirement creation indirectly by triggering related context. Click to see the rationale!
									</div>
								{/if}
								{#if !selectedRequirementInfluenceKinds.hasDirect && !selectedRequirementInfluenceKinds.hasIndirect}
									<div class="empty-guide__line">
										No direct or indirect influence evidence is available for this requirement.
									</div>
								{/if}
							</span>
						</div>
					{/if}
					<ChatLogPanel
						requirementId={selectedRequirementId}
						requirementCreationTurn={selectedRequirementCreationTurn}
						focusedTurnId={focusedChatTurnId}
						allRequirementIds={chatLinkedRequirementIds}
						childGoalStarts={selectedOutcomeChildGoalStarts}
						utteranceList={utteranceList}
						requirementActionMap={requirementActionMap}
						actionUtteranceMap={actionUtteranceMap}
						selectedActionId={selectedActionId}
						hoveredActionId={hoveredActionId}
						hoveredPanelTurnId={hoveredPanelTurnId}
						onActionHover={(id: string | null) => {
							hoveredActionId = id;
							if (id) hoveredPanelTurnId = null;
						}}
						onActionClick={(id: string | null) => selectAction(id)}
						outcomeTurnIdWhenNoReqs={outcomeActionsFromMap != null ? null : outcomeTurnIdWhenNoReqs}
						outcomeActionsFromMap={outcomeActionsFromMap}
						hideActions={selectedOutcome == null && selectedIntentLabel != null}
					/>
				</div>
			</div>
		</main>
	{/if}
</div>

<style>
	.page {
		display: flex;
		flex-direction: column;
		box-sizing: border-box;
		height: 100dvh;
		max-height: 100dvh;
		min-height: 0;
		overflow: hidden;
		background: #f8fafc;
		padding: 0.9rem 1rem;
		width: 100%;
		max-width: 1480px;
		margin: 0 auto;
	}
	.loading,
	.error {
		text-align: center;
		padding: 3rem;
		color: #6b7280;
	}
	.loading--inline {
		padding: 1rem;
	}
	.error {
		color: #b91c1c;
	}
	.first-screen {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		flex: 1 1 0;
		min-height: 0;
		overflow-y: auto;
		gap: 1rem;
	}
	.page > .loading {
		flex: 1 1 0;
		min-height: 0;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.header-actions {
		display: inline-flex;
		align-items: center;
		gap: 0.55rem;
		flex-shrink: 0;
	}
	.header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.5rem;
		margin-bottom: 0.75rem;
		flex-wrap: wrap;
		padding: 0.4rem 0.25rem 0;
		flex-shrink: 0;
	}
	.header__title-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.65rem 1.25rem;
		min-width: 0;
		flex: 1;
	}
	.run-picker {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		flex-shrink: 0;
	}
	.run-picker__label {
		font-size: 0.78rem;
		font-weight: 700;
		color: #475569;
		white-space: nowrap;
	}
	.run-picker__select {
		min-width: 12rem;
		max-width: min(42vw, 22rem);
		padding: 0.4rem 0.55rem;
		border: 1px solid #cbd5e1;
		border-radius: 10px;
		font-size: 0.84rem;
		color: #0f172a;
		background: #ffffff;
		cursor: pointer;
	}
	.run-picker__select:focus {
		outline: none;
		border-color: #6366f1;
		box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.16);
	}
	.title {
		margin: 0;
		font-size: 1.15rem;
		font-weight: 700;
		color: #111827;
		line-height: 1.3;
	}
	.help-trigger {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		padding: 0.42rem 0.78rem;
		border-radius: 999px;
		border: 1px solid #bfdbfe;
		background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
		color: #1e40af;
		font-size: 0.85rem;
		font-weight: 700;
		letter-spacing: 0.01em;
		cursor: pointer;
		box-shadow: 0 2px 8px rgba(37, 99, 235, 0.14);
		transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
	}
	.help-trigger:hover {
		transform: translateY(-1px);
		background: linear-gradient(180deg, #dbeafe 0%, #bfdbfe 100%);
		box-shadow: 0 6px 14px rgba(37, 99, 235, 0.2);
	}
	.help-trigger:focus-visible {
		outline: 2px solid #60a5fa;
		outline-offset: 2px;
	}
	.help-trigger__icon {
		display: inline-grid;
		place-items: center;
		width: 1.1rem;
		height: 1.1rem;
		border-radius: 999px;
		background: #1d4ed8;
		color: #ffffff;
		font-size: 0.72rem;
		font-weight: 800;
		line-height: 1;
	}
	.empty-guide {
		flex-shrink: 0;
		margin: 0 0 0.5rem;
		padding: 0.72rem 0.85rem;
		border-radius: 10px;
		border: 1px solid #ddd6fe;
		background: #f5f3ff;
		color: #4c1d95;
		font-size: 0.95rem;
		font-weight: 600;
		line-height: 1.45;
		display: flex;
		align-items: center;
		gap: 0.45rem;
	}
	.empty-guide--soft {
		border-style: solid;
		border-color: #ddd6fe;
		background: #f5f3ff;
		color: #4c1d95;
	}
	.empty-guide__tip-head {
		display: inline-flex;
		align-items: center;
		flex-shrink: 0;
		padding: 0.16rem 0.46rem;
		border-radius: 999px;
		border: 1px solid #c4b5fd;
		background: #ede9fe;
		box-shadow: 0 1px 0 rgba(124, 58, 237, 0.08) inset;
		font-size: 0.82rem;
		font-weight: 800;
		color: #6d28d9;
		letter-spacing: 0.01em;
		text-transform: uppercase;
	}
	.empty-guide__sub-note {
		display: block;
		margin-top: 0.18rem;
		font-size: 0.82rem;
		font-weight: 500;
		color: #6d28d9;
		opacity: 0.92;
	}
	.empty-guide__inline-strong {
		font-weight: 800;
	}
	.empty-guide__line {
		display: block;
		margin-top: 0.1rem;
		font-size: inherit;
		font-weight: inherit;
		color: inherit;
	}
	/* ── 3-column layout ─────────────────────────────────── */
	.main {
		display: flex;
		flex-direction: column;
		min-width: 0;
		flex: 1 1 0;
		min-height: 0;
		overflow: hidden;
	}
	.three-col-main {
		display: flex;
		flex-direction: column;
		min-height: 0;
		flex: 1 1 0;
		overflow: hidden;
	}
	.three-col-grid {
		display: grid;
		grid-template-columns: var(--left-col-width, 320px) 6px 380px 1fr;
		grid-template-rows: minmax(0, 1fr);
		gap: 0;
		column-gap: 0.75rem;
		flex: 1 1 0;
		min-height: 0;
		overflow: hidden;
		align-items: stretch;
	}
	.three-col-grid.is-resizing {
		user-select: none;
		cursor: col-resize;
	}
	.three-col-grid > * {
		min-height: 0;
	}
	.resize-handle {
		width: 6px;
		cursor: col-resize;
		background: transparent;
		position: relative;
		z-index: 2;
		flex-shrink: 0;
		transition: background 0.15s;
		border-radius: 3px;
	}
	.resize-handle:hover,
	.three-col-grid.is-resizing .resize-handle {
		background: #cbd5e1;
	}
	.resize-handle::after {
		content: '';
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		width: 2px;
		height: 32px;
		border-radius: 1px;
		background: #94a3b8;
		opacity: 0;
		transition: opacity 0.15s;
	}
	.resize-handle:hover::after,
	.three-col-grid.is-resizing .resize-handle::after {
		opacity: 1;
	}
	.col-left {
		min-width: 0;
		min-height: 0;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		align-items: stretch;
	}
	.col-left :global(.outcome-hierarchy-with-timeline) {
		/* flex + min-height: component; column clips so only .hierarchy-list scrolls */
		min-width: 0;
	}
	.col-left :global(.hierarchy-list) {
		flex: 1;
		min-height: 0;
		overflow: auto;
	}
	.col-center {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}
	.col-center :global(.requirements-panel) {
		flex: 1 1 0;
		min-height: 0;
		overflow: hidden;
	}
	.col-right {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}
	.col-right :global(.panel) {
		flex: 1 1 0;
		min-height: 0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
	}
	.col-right-empty {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100%;
		background: #ffffff;
		border: 1px solid #e5e7eb;
		border-radius: 18px;
		box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
	}
	.column-hint {
		margin: 0;
		font-size: 0.875rem;
		color: #64748b;
		text-align: center;
	}

	@media (max-width: 900px) {
		.three-col-grid {
			grid-template-columns: 1fr 1fr;
			grid-template-rows: auto minmax(0, 1fr);
		}
		.col-left {
			grid-column: 1 / -1;
			max-height: 300px;
		}
		.resize-handle {
			display: none;
		}
	}
	@media (max-width: 700px) {
		.page {
			padding: 0.5rem 0.75rem;
		}
		.three-col-grid {
			grid-template-columns: 1fr;
		}
		.col-left {
			grid-column: auto;
		}
	}
</style>
