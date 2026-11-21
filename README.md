# etn-tenders-watchtower
Project for MAIN Summit Hackathon

## Run

```
docker build -t watchtower .
docker run -p 8080:8080 -e OPENAI_API_KEY -v watchtower-data:/data watchtower
```

Open http://localhost:8080/. For local development: `uv sync && uv run python -m watchtower`.
Environment variables are listed in [ADR 0001](docs/decisions/0001-tech-stack.md).

### Models

| Variable | Default | Used for |
|---|---|---|
| `OPENAI_API_KEY` | required | Model key (environment only) |
| `WATCHTOWER_MODEL_STRONG` | `WATCHTOWER_MODEL`, then `gpt-5.6-luna` | Strong tier: explores portals, writes and repairs templates, decides outcomes |
| `WATCHTOWER_MODEL_LIGHT` | the strong model | Light tier: narrow checks the strong model delegates and reviews |
| `WATCHTOWER_MODEL` | `gpt-5.6-luna` | Sets both tiers when the two above are unset |

Regular reads make no model call. See [agent runtime → model tiers](docs/agent/runtime.md#model-tiers).
### Model smoke test

Put the key in `.env` (copy `.env.example`), then check that the key, the model, tool calling and structured output work
with one real call: `uv run --env-file .env python -m watchtower.agent.smoke`.
