# CoTrace Web Demo

A static GitHub Pages deployment of the CoTrace visualisation tool, pre-loaded with the **sample1** dataset.

**Live URL:** https://rladmstn1714.github.io/CoTrace/

---

## How it works

The source lives in `../tool/` (a SvelteKit + Vite app).  
A GitHub Actions workflow (`.github/workflows/pages.yml`) runs automatically on every push to `main` that touches `tool/`, and also on demand.

It does three things without touching any source files:

1. **Builds** the tool with two environment variables:
   | Variable | Value | Purpose |
   |---|---|---|
   | `BASE_PATH` | `/CoTrace` | Prefixes all SvelteKit asset/link paths so the app sits at `github.io/CoTrace/` |
   | `VITE_DATA_BASE` | `CoTrace/sample` | Baked into the JS bundle; makes data fetches resolve to `/CoTrace/sample/sample1/…` which matches the static JSON files the build copies out |

2. **Creates** a tiny static file at `build/api/runs` containing `{"runs":["sample1"]}` so the run-discovery fetch the app makes on load resolves correctly on a server-less host.

3. **Deploys** the `build/` directory to GitHub Pages via the official `actions/deploy-pages` action.

---

## One-time setup (repo settings)

In **Settings → Pages** of the GitHub repo, set:

- **Source:** GitHub Actions

That's it. The first push (or manual trigger via **Actions → Run workflow**) will publish the site.

---

## Local preview of the exact same build

```bash
cd tool
VITE_DATA_BASE=CoTrace/sample BASE_PATH=/CoTrace npm run build
mkdir -p build/api
printf '{"runs":["sample1"]}' > build/api/runs
# serve build/ with any static file server, e.g.:
npx serve build
```

Then open http://localhost:3000/CoTrace/
