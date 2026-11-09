<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

{@render children()}

<style>
	:global(:root) {
		/* Soft, friendly palette shared by every panel. */
		--ct-font: 'Nunito', ui-rounded, 'SF Pro Rounded', system-ui, -apple-system, 'Segoe UI', sans-serif;
		--ct-page: #ffffff;
		--ct-panel: #f2f3f5;
		--ct-card: #ffffff;
		--ct-line: #dcdee2;
		--ct-line-soft: #e7e8eb;
		--ct-ink: #24272b;
		--ct-text: #3a3d42;
		--ct-muted: #6f7378;
		--ct-faint: #a3a6a9;
		--ct-user: #4472c4;
		--ct-user-soft: #e2eaf7;
		--ct-asst: #4f8a4b;
		--ct-asst-soft: #e3eedb;
		--ct-asst-sage: #a9c09a;
		--ct-select: #24272b;
	}
	:global(html),
	:global(body) {
		height: 100%;
		margin: 0;
		background: var(--ct-page);
		color: var(--ct-text);
		font-family: var(--ct-font);
		-webkit-font-smoothing: antialiased;
	}
	:global(button),
	:global(select),
	:global(input) {
		font-family: inherit;
	}
</style>
