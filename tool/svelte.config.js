import adapter from '@sveltejs/adapter-static';

const isProd = process.env.NODE_ENV === 'production';
const rawBase = process.env.BASE_PATH ?? '';
const base =
	!rawBase || rawBase === '/'
		? ''
		: rawBase.startsWith('/')
			? rawBase.replace(/\/$/, '')
			: `/${rawBase.replace(/\/$/, '')}`;

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		paths: {
			base,
			relative: isProd && base !== ''
		},
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: '404.html',
			strict: false
		}),
		prerender: {
			handleMissingId: 'warn'
		}
	}
};

export default config;
