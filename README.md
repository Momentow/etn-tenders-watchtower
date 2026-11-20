# etn-tenders-watchtower
Project for MAIN Summit Hackathon

## Run

```
docker build -t watchtower .
docker run -p 8080:8080 -e OPENAI_API_KEY -v watchtower-data:/data watchtower
```

Open http://localhost:8080/. For local development: `uv sync && uv run python -m watchtower`.
Environment variables are listed in [ADR 0001](docs/decisions/0001-tech-stack.md).
