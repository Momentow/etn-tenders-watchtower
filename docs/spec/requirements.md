# Requirements

Distilled from the brief (`docs/Patterno-Track-Description.pdf`). Exact API: [api-contract.md](api-contract.md).

## Goal

An agent that connects Europe's public-procurement portals on its own, watches over them, and heals itself when one breaks.
**Nobody may help the system while it runs.**

## Functional

1. **Attach.** Given only the address of a portal it has never seen, deliver every open tender it lists and each tender's documents.
2. **Notice.** Know when a portal stops delivering correctly, and say so. If it can't be repaired, say that too.
3. **Repair.** Get back to correct delivery after the portal has changed.
4. **Dashboard.** The page an operator opens in the morning: every portal, whether it delivers, what broke, and what the system did about it.
5. **API.** Exactly as in [api-contract.md](api-contract.md).

## Non-functional

- One container, port 8080, serving the API, the dashboard and the docs (`/docs`).
- `docker build .` works on `linux/amd64` and builds relatively quickly on small to medium machines. The default command starts everything.
- Any language, framework and model. Model keys come from env vars and are named in the README.
- Use an LLM to learn how a portal is read, to check it and to repair it. Regular reads should ideally make no model call.
- No steering: no person or service of ours directs the system during the run.
- Politeness: at most 1 request/second per portal, no logins, no captcha solving.
- Should scale to many portals, with a low model cost per portal.

## Judging

| Criterion | What is checked |
|---|---|
| Attach | Tenders and documents delivered correctly from unseen portals (private ones built for the challenge, plus real ones). |
| Notice | The state the system reports matches what it delivers. |
| Repair | Correct delivery again after a portal has changed. |
| Dashboard | An operator sees which portals deliver, which broke and what the system did. |
| Approach | How well it would work on many more portals, and what one portal costs in model usage. |

## Hand-in

- Git repository with a `Dockerfile` at its root.
- README: how the system works (one page), the env vars it needs, and the model cost per portal.
- A video of at most 3 minutes showing the system at work, including the dashboard.

## Finding test portals

- **Sources:** ted.europa.eu (recent notices per country; the tender-documents address is the portal), and national sites:
  oeffentlichevergabe.de (DE), boamp.fr (FR), contrataciondelestado.es (ES), tenderned.nl (NL). Search in the local language
  (*Vergabeplattform Vergabeunterlagen*, *marchés publics DCE*, *licitaciones pliegos*, *przetargi dokumentacja*).
- **A good test set:** each portal has a public list and ≥ 5 open tenders with documents that download without login;
  ≥ 4 countries and ≥ 3 portal software packages (check the URL path or footer); a mix of server-rendered pages, JS apps
  (JSON API) and multi-step downloads; 1–2 portals blocked by a login or captcha; a few held back as unseen until the final test.
- **Traps:** notice links that go to a summary page instead of the tender; deadlines in local time; ZIP files over 1 GB;
  captcha shown, yet the documents still download.
