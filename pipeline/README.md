# CoTrace pipeline

Run the pipeline on one chat dialogue and export parsed artifacts.

`--input_diag` is a unified chat source argument name. It accepts both chat URLs and local paths.

## Quick start

Install dependencies:

```bash
pip install -r requirements.txt
```

Set API keys in a local `.env` file in this directory, or export them in your shell:

```bash
cp .env.example .env
```

Then edit `.env` with your keys. Do not commit `.env`.

Run with a local dialogue JSON file:

```bash
python run_pipeline.py \
  --input_diag /path/to/dialogue.json \
  --output_dir /path/to/output
```

Run with a chat URL:

```bash
python run_pipeline.py \
  --input_diag "https://poe.com/s/SHARE_CODE" \
  --output_dir /path/to/output
```

Run with a local chat repo directory:

```bash
python run_pipeline.py \
  --input_diag /path/to/chat_repo_dir \
  --output_dir /path/to/output
```

## View outputs in the web tool

To inspect pipeline outputs in the CoTrace Explorer, write each run under `tool/static/<dataset_name>/<run_name>/`, then start the tool with that dataset name.

From the repository root:

```bash
# 1) Run the pipeline and place parsed outputs where the tool can read them.
python pipeline/run_pipeline.py \
  --input_diag /path/to/dialogue.json \
  --output_dir /path/to/output

# 2) Start the interactive explorer.
cd tool
VITE_DATA_BASE=/path/to/output npm run dev
```

Open:

```text
http://localhost:5173
```

If you process multiple dialogues, write each one to a separate run folder under the same dataset:

```text
/path/to/output
├── dialogue-a/
├── dialogue-b/
└── dialogue-c/
```

The tool will show these folders in the run dropdown.

If `--output_dir` is omitted with `--input_diag`, outputs are written under:

```text
./outputs/<folder_name_or_inferred_name>/
```

`<folder_name_or_inferred_name>` comes from:
- `--folder_name` if provided
- Poe share code for URL input
- directory name for directory input
- file stem for file input

## `--input_diag` input format

`--input_diag` accepts either:

1. **Poe share URL**
   - Format: `https://poe.com/s/<CODE>`
   - The script downloads the chat and stores it as `chat_dialogue_input.json` before running the pipeline.

2. **Local path**
   - A `.json` or `.jsonl` file path, or
   - A directory path containing one dialogue file.

Use `--input_dir` (not `--input_diag`) when you want to process many dialogue files in batch.

For directory input, file discovery rules are:
- Prefer `input_dialogue.json`, `dialogue.json`, then `step0_input.json`.
- Otherwise, exactly one `*.json` or `*.jsonl` must exist in that directory.
- If multiple files are present, pass a specific file path via `--input_diag` or use `--input_dir` for batch runs.

## Desired dialogue JSON format

The loader supports multiple dialogue schemas. Recommended step0-style format:

```json
{
  "dialogue_id": "example_dialogue",
  "turns": [
    {"turn_id": 0, "speaker": "user", "text": "User utterance"},
    {"turn_id": 1, "speaker": "assistant", "text": "Assistant response"}
  ]
}
```

It also accepts compatible variants with `utterances` and CoGym-style `event_log` inputs (handled by normalization in `run_pipeline.py`).

## API keys

The pipeline loads environment variables from `.env` automatically. Put `.env` in this directory (`./.env`) or in a parent directory.

For the default OpenAI provider:

```bash
OPENAI_API_KEY=your_openai_api_key
```

Full pipeline runs also use OpenAI embeddings for Step 2 similarity, so `OPENAI_API_KEY` is required even if you use another chat provider unless you stop before Step 2.

Optional provider variables:

```bash
# Google Gemini
GEMINI_API_KEY=your_gemini_api_key

# OpenRouter
OPENROUTER_API_KEY=your_openrouter_api_key

# LiteLLM/OpenAI-compatible gateway
LITELLM_API_BASE=https://your-gateway.example.com
LITELLM_API_KEY=your_gateway_api_key
```

You can choose the provider/model with CLI flags:

```bash
python run_pipeline.py \
  --input_diag /path/to/dialogue.json \
  --output_dir /path/to/output \
  --provider openai \
  --model gpt-5.2
```

## Prompt versioning

LLM prompts live under `config/`. We keep a frozen paper snapshot alongside the current prompts:

| File | Version | Use |
|------|---------|-----|
| `config/prompts.py` | v0.2.0 | Default for new runs |
| `config/prompts_paper_v1.py` | v0.1.0 | Paper reproduction ([arXiv:2605.21363](https://arxiv.org/abs/2605.21363)).|

By default, `pipeline.py` imports from `config/prompts.py`.

## Output files

Main parsed outputs are written to `--output_dir`:

- `utterance_list.json`
- `requirement_relations.jsonl`
- `requirements_outputs_lists.json`
- `requirement_output_dependency.json`
- `requirement_contributions.json`
- `output_contributions.json`
- `requirement_action_map.json`
- `action_utterance_map.json`
- `requirement_status.json`

Optional outputs when available:
- `requirement_forward_labels.json` (from step2c)
- `intent_outcome_map.json` (from step05b; only when run with `--with-intentions`)

Raw pipeline intermediates are written under:

```text
<output_dir>/run/
```

