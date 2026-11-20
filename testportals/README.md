# Test portals

Our real-portal test set (T-001). Human-readable registry: [docs/portals/registry.md](../docs/portals/registry.md).
Not shipped in the Docker image (`.dockerignore`).

| File | What it is |
|---|---|
| `portals.json` | Machine-readable registry: kind, access, use (`dev` / `blocked` / `unverified` / `unseen`), verified counts, URL patterns, quirks. T-020 loads it. |
| `fixtures/<portal>.json` | Expected tenders from TED (published 2026-10-21 to 2026-11-20). `id`, `url` and `documents` are null: match extracted tenders on title, buyer and reference number, not as exact answers. |
| `testset.pinned.txt` | Input for `ted_portals.py build`: the pinned portal keys and roles. |
| `ted_portals.py` | Standard-library script that finds portals through the public TED Search API and writes the fixtures. |

**Don't open `unseen` portals while developing.** They are held back for the final test (T-020).

## Rebuild

`collect` and `probe` touch the network (TED at 1 call/s, portals at ≥ 1.1 s per host). Keep the output out of git:

```bash
python testportals/ted_portals.py --out /tmp/ted collect --countries DE,FR,ES,NL,PL,AT,BE,IT --days 30
python testportals/ted_portals.py --out /tmp/ted build --pin testportals/testset.pinned.txt
python testportals/ted_portals.py --out /tmp/ted probe --testset-only
cp /tmp/ted/fixtures/<portal>.json testportals/fixtures/
```
