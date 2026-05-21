# CoTrace-viewer

Interactive SvelteKit app for exploring how outcomes evolve through dialogue, with hierarchy/timeline views and role-based contribution summaries.

## Project scope

This README only documents this repository's app and data workflow.

## Setup

```sh
npm install
```

## Local development

```sh
npm run dev
```

Then open **http://localhost:5173/**.

```sh
npm run dev -- --open
```

Optional: run with a specific data base folder.

```sh
VITE_DATA_BASE=<folder> npm run dev
```

Helper script (same behavior, with optional port):

```sh
./run.sh <folder> --port 5173
```

## Viewing pipeline outputs

The pipeline writes the parsed JSON/JSONL files that this tool expects. Put each pipeline run under `tool/static/<dataset_name>/<run_name>/`.

From the repository root:

```sh
python pipeline/run_pipeline.py \
  --input_diag /path/to/dialogue.json \
  --output_dir tool/static/my-runs/my-dialogue

cd tool
npm install
./run.sh my-runs --port 5173
```

Then open **http://localhost:5173/explorer**.

For multiple runs, place each output directory under the same dataset folder:

```text
tool/static/my-runs/
├── dialogue-a/
├── dialogue-b/
└── dialogue-c/
```

The explorer will list those run folders in the Run dropdown.

## Data base and run selection

The app reads data from `static/<VITE_DATA_BASE>/`.

- If `VITE_DATA_BASE` points to a folder containing multiple run subfolders, the UI shows a Run dropdown.
- If it points to a single run folder, that run is loaded directly.
- If omitted, the default is `sample` (includes the bundled `sample/sample1` run).

## Required data files (per run)

Place these files in each run directory:

- `requirement_output_dependency.json`
- `requirements_outputs_lists.json`
- `requirement_relations.jsonl`
- `output_contributions.json`
- `requirement_action_map.json`
- `action_utterance_map.json`
- `utterance_list.json`

Optional file:

- `outcome_action_map.json`

Reference example data under `static/sample/sampel1/`.

## Quality checks

```sh
npm run check
```

Watch mode:

```sh
npm run check:watch
```

## Build and preview

```sh
npm run build
npm run preview
```

