<script lang="ts">
	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { base } from '$app/paths';

	const explorerPath = `${base}/explorer`;

	if (browser) {
		goto(explorerPath, { replaceState: true });
	}
</script>

<svelte:head>
	<title>CoTrace Explorer</title>
	<meta http-equiv="refresh" content={`0; url=${explorerPath}`} />
</svelte:head>

<main>
	<p>Opening CoTrace Explorer...</p>
	<p><a href={explorerPath}>Open Explorer</a></p>
</main>
