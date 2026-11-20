# Test-portal registry

Our own test set (see [requirements → Finding test portals](../spec/requirements.md#finding-test-portals)).
Offline copies of these quirks, with mutated variants: [mock portals](../test-portals/mock-portals.md).
Mark the portals held back for the final test `unseen`. Don't use them while developing.

Machine-readable copy, with URL patterns and every quirk: [`testportals/portals.json`](https://github.com/Momentow/etn-tenders-watchtower/blob/master/testportals/portals.json).
Expected tenders from TED, one file per portal: `testportals/fixtures/<portal>.json`. Their `id`, `url` and `documents` are null,
so match on title, buyer and reference number.

**Kind:** `ssr` (server-rendered) · `js-api` (JS app with a JSON API) · `multi-step` (multi-step download)
**Access:** `open` · `login` · `captcha` · `captcha-bypassable` (captcha shown, but the documents download without it)
**Use:** `dev` (develop against it) · `blocked` (must end `failed` with a reason) · `unverified` · `unseen` (final test only)

Verified on 2026-11-20 with a polite live probe: at least 1.1 s between requests per host, a browser User-Agent, no login, no
captcha. One document per sampled tender was streamed (first 1 MB) to confirm it is a real file. **Docs ok** counts sampled
open tenders whose documents downloaded anonymously. **Open (TED)** counts open TED notices from 2026-10-21 to 2026-11-20 that
point to the portal.

| # | Portal | Country | Software | Kind | Access | Open (TED) | Docs ok | Use |
|---|---|---|---|---|---|---|---|---|
| 1 | [dtvp.de](https://www.dtvp.de) | DE | vmp-satellite (cosinex) | multi-step | open | 89 | 6/6 | dev |
| 2 | [evergabe.nrw.de](https://www.evergabe.nrw.de) | DE | vmp-satellite (cosinex) | multi-step | open | 33 | 4/6 | dev |
| 3 | [marches-publics.gouv.fr](https://www.marches-publics.gouv.fr) | FR | atexo-mpe (PLACE) | multi-step | open | 134 (portal: 1639) | 4/6 | dev |
| 4 | [achatpublic.com](https://www.achatpublic.com) | FR | achatpublic | multi-step | captcha-bypassable | 87 | 4/5 | dev |
| 5 | [tenderned.nl](https://www.tenderned.nl) | NL | tenderned | js-api | open | 571 (portal: 2582) | 5/5 | dev |
| 6 | [publicprocurement.be](https://www.publicprocurement.be) | BE | e-procurement-be (BOSA) | js-api | open | 412 | 8 files listed; download not confirmed | dev |
| 7 | [contractaciopublica.cat](https://contractaciopublica.cat) | ES | Generalitat de Catalunya | js-api | open | 168 | 1/1 (browser) | dev |
| 8 | [provia.at](https://www.provia.at) | AT | provia bieterportal | ssr | login | 75 | 0/6 | blocked |
| 9 | [cloud.3p.eu](https://cloud.3p.eu) | BE | 3P | multi-step | captcha | 212 | 0/6 | blocked |
| 10 | [marches-publics.info](https://www.marches-publics.info) | FR | AWS | multi-step | captcha | 255 | not tested | blocked |
| 11 | [s2c.mercell.com](https://s2c.mercell.com) | BE | mercell | js-api | ? | 180 | not tested | unverified |
| 12 | [acquistinretepa.it](https://www.acquistinretepa.it) | IT | Consip | js-api | ? | 54 | not tested | unverified |
| 13 | gv.vergabeportal.at | AT | vergabeportal-at | | | 41 | not confirmed | unseen |
| 14 | vergabe.autobahn.de | DE | netserver | | | 17 | not confirmed | unseen |
| 15 | marches.megalis.bretagne.bzh | FR | atexo-mpe | | | 14 | 1/1 | unseen |
| 16 | fcc.tuttogare.it | IT | tuttogare | | | 6 | 1/1 | unseen |

**Coverage of the `dev` + `blocked` set:** 6 countries (DE, FR, NL, BE, ES, AT), 10 software packages, all three kinds, and
3 blocked portals (1 login, 2 captcha) plus 1 captcha-bypassable. The `unseen` set adds IT and two software families that
appear nowhere in `dev`. megalis runs the same software as PLACE, so it tests template reuse.

## Quirks per portal

### dtvp.de and evergabe.nrw.de (vmp-satellite)
- One software on many hosts: `/Satellite/` on dtvp.de, `/VMPSatellite/` on evergabe.nrw.de. Reusing a template between them is the test.
- TED links `/notice/{id}/documents` redirect to `/public/company/project/{id}/de/overview?N`. The documents tab renders only once a session cookie is set (request the page twice).
- Strip the Wicket page version (`?1`, `?2` …) and `;jsessionid=…` to get a stable `url`. The `CX…` id is stable.
- Access varies by tender: some keep the *Vergabeunterlagen* tab disabled (documents only for registered companies), e.g. 2 of 6 on evergabe.nrw.de.
- Whole-tender ZIP: `documents/archive/Vergabeunterlagen_{id}.zip` (30 MB+ seen).
- The tender list is filled by JS: `POST /Center/api/v2/project/search` needs an `X-JWT` token that the search page issues once a session exists. Deadlines: `29.12.2026 10:00 Ortszeit` (Europe/Berlin). Pages are ISO-8859-1.

### marches-publics.gouv.fr (PLACE, atexo-mpe)
- **Summary-page trap:** TED links `?page=Entreprise.EntrepriseAdvancedSearch&AllCons&id=…` land on the search page. Some TED ids answer *« L'accès à cette consultation n'est pas autorisé »*. Take tenders from the public list `?page=Entreprise.EntrepriseAdvancedSearch&AllCons` (1639 results, 10 per page).
- Tender page `/app.php/entreprise/consultation/{id}?orgAcronyme={org}`. The règlement or DCE ZIP downloads directly from `EntrepriseDownloadReglement&id={base64 id}`. The full DCE goes through `EntrepriseDemandeTelechargementDce`, which offers an "anonymous download" option.
- Slow: the first byte of a document can take more than 40 s. Deadlines: `24/11/2026 12:00 (heure de Paris)`.

### achatpublic.com
- **Captcha trap:** a captcha and login form on every page, yet *« télécharger les pièces de manière anonyme »* works without them: `POST /sdm/ent2/gen/telechargerDCE.action` with the `dceId` from the page.
- Old TED path `/sdm/ent/gen/ent_detail.do` redirects to `/sdm/ent2/gen/ficheCsl.action?PCSLID={id}`.
- **Large file:** a 627 MB DCE ZIP was seen. It's over 250 MB, so it may be skipped.
- TED shows +02:00 offsets for November deadlines here. Don't trust TED offsets; use Europe/Paris.

### tenderned.nl
- Angular micro-frontend: the HTML shell is empty and loads about 60 JS chunks, so rendering at 1 req/s takes more than a minute. Read the public JSON API instead:
  `GET /papi/tenderned-rs-tns/v2/publicaties?page=0&size=50` (Spring paging: `content`, `totalElements`) and `/publicaties/{id}`.
- The list mixes notice types (`typePublicatie`), including award and early-termination notices: filter to calls for competition.
- `sluitingsDatum` is local time with no offset (Europe/Amsterdam). The notice PDF is at `/publicaties/{id}/pdf`. Documents: `/publicaties/{id}/documenten` lists the files (`grootte` in bytes, `links.download.href`), plus `links.downloadZip` for all of them. No login or token needed. In the UI the *Documenten* tab loads only on click.

### publicprocurement.be
- Vue app. First it fetches an anonymous token: `POST /auth/realms/supplier/protocol/openid-connect/token` with `grant_type=client_credentials`.
- Documents JSON: `GET /api/dos/publication-workspaces/{uuid}/documents?full=false&type=WORKSPACE&type=ESPD_REQUEST&type=SDI` gives `versions[].document.originalFileName` and `fileHash` (sha256), with no login.
- Heavy start-up: the app loads several MB of reference JSON (cities, CPVs, NUTS) first, so a polite render takes minutes. Call the JSON API instead.

### contractaciopublica.cat
- Angular app (`base href /ca/`). Public list API: `GET /portal-api/cerca-avancada?page=0&size=20&sortField=dataUltimaPublicacio&sortOrder=desc` (Spring paging, capped at 10000).
- Each entry has `fasesVigents`: `ANUNCI_EN_TERMINI` means open. `ADJUDICACIO` and `FORMALITZACIO` are award phases. Lots appear as separate entries (`expedientId;lotId`).
- **Summary-page trap:** TED links point to the buyer profile `/ca/perfils-contractant/detall/{id}`, not the tender. The tender page is `/ca/detall-publicacio/{expedientId}/{id}`.
- Detail API: `GET /portal-api/detall-publicacio-expedient/{expedientId}/{id}`. Documents carry `titol`, `mida` (bytes), `hash` (MD5) and an opaque `path` token.
- Downloads run as a JS fetch turned into a `blob:` URL; there's no plain `href`. A 512,749-byte PDF matched `mida` exactly. The real endpoint is still to be found from the XHR log.

### Blocked: provia.at, cloud.3p.eu, marches-publics.info
- **provia.at (login):** tender pages are public, but every `File/Download?fileGuid=…` redirects to `/bieterportal/Login`. Expected: `failed`, with a reason saying documents need a login.
- **cloud.3p.eu (captcha):** an ASP.NET postback picks the country, then the anonymous option (*« Ik wens mij niet te identificeren »*) fails with *« De verificatie tegen robots is mislukt »*, an invisible bot check. Expected: `failed` (captcha).
- **marches-publics.info (captcha):** both the identified and the anonymous DCE download ask for an image captcha. We did not test whether it is enforced (we don't solve or submit captchas). If a browser check shows the download works without it, re-tag it `captcha-bypassable`.

### Unverified: s2c.mercell.com, acquistinretepa.it
- mercell: an earlier probe with a bot User-Agent got 403; with a browser User-Agent the Angular shell answers 200. Document access not checked.
- acquistinretepa.it: TED links redirect to `/opencms/opencms/scheda_altri_bandi.html?idBando={id}`, or fail with HTTP 500 after 3 redirects. The detail page is an AngularJS template.

## Smoke runs

How to run one: [E2E and smoke runs](../test-portals/e2e-and-smoke.md). **Verdict** compares what Watchtower reported with
what we know about the portal: ✓ right, ⚠ right state but a gap, ✗ wrong state.

### 2026-11-21: the 12 `dev`, `blocked` and `unverified` portals (first live run)

Real model, fresh database, portals added 5 s apart. All 12 settled within 10 minutes; **1 serving, 11 failed**.

| Portal | Use | Reported | Time | Verdict |
|---|---|---|---|---|
| evergabe.nrw.de | dev | `serving`, 20 tenders | 2m35s | ⚠ First list page only (about 33 open). 14 of 20 deadlines `null`: tenders live on other vmp-satellite hosts (vergabe-westfalen.de and others) the detail rules don't fit. `referenceNumber` always `null`. `url` keeps the Wicket page version (`/overview?4`). Deadlines that were read carry the right offset |
| dtvp.de | dev | `failed`: detail/document links redirect to a Keycloak login | 1m15s | ✗ False block. Documents download anonymously (6/6 in the probe) once a session cookie exists; some tenders are registered-only |
| marches-publics.gouv.fr | dev | `failed`: document download redirects to a login | 1m15s | ✗ False block. The RC downloads directly; the full DCE has an anonymous-download option |
| achatpublic.com | dev | `failed`: no readable list, search is client-side | 2m25s | ⚠ Truthful, but the list was never found |
| tenderned.nl | dev | `failed` (temporary): Angular shell times out while rendering | 4m45s | ✗ The render can't finish: ~60 JS chunks at 1 req/s exceed the 45 s render timeout. The public JSON API (`/papi/tenderned-rs-tns/v2/publicaties`) needs no browser |
| publicprocurement.be | dev | `failed` (temporary): JS shell times out in the browser | 4m35s | ✗ Same cause; the JSON API needs an anonymous `client_credentials` token |
| contractaciopublica.cat | dev | `failed`: only a 5-item latest-publications widget found | 9m15s | ✗ The full search/list API wasn't found |
| provia.at | blocked | `failed`: details and documents need a login | 4m15s | ✓ |
| cloud.3p.eu | blocked | `failed`: base URL shows only the 3P logo, a tenant URL is needed | 4m35s | ✓ State; the reason is true for the URL given (we expected the captcha) |
| marches-publics.info | blocked | `failed`: each tender is split over two sibling rows, no item selector fits | 5m35s | ⚠ State right, but it stopped at a template-format limit, before reaching the captcha |
| s2c.mercell.com | unverified | `failed` (temporary): HTTP 403 from an Azure Application Gateway on every path | 5m25s | ✓ State; a constant 403 is better reported as a block than as temporary. Move to `blocked` |
| acquistinretepa.it | unverified | `failed` (temporary): JS app times out while rendering | 6m55s | ✗ Same render-timeout cause as tenderned.nl |

**Findings, by how many portals each one costs:**

1. **Heavy JS apps can't render at 1 req/s (3 portals: tenderned.nl, publicprocurement.be, acquistinretepa.it).** The browser
   spaces every script request through the per-host limiter, but a render times out after 45 s, so an app with more
   scripts than that never finishes and its API calls are never captured.
2. **One login redirect ends the attach (2 portals: dtvp.de, marches-publics.gouv.fr).** The agent reports `Blocked` after a
   document link of one tender redirects to a login, without trying other tenders, a session cookie from the list page,
   or the portal's anonymous-download route. A `failed` portal that could deliver is a Notice error.
3. **List not found (2 portals: achatpublic.com, contractaciopublica.cat).**
4. **Template format: one tender over several sibling rows (1 portal: marches-publics.info).**
5. **Partial reads are served without a warning (evergabe.nrw.de):** one page of the list, and detail rules that fit only some
   of the hosts a portal links to.

## How to rebuild

See [`testportals/README.md`](https://github.com/Momentow/etn-tenders-watchtower/blob/master/testportals/README.md):
`python testportals/ted_portals.py --out /tmp/ted collect …`, then `build --pin testportals/testset.pinned.txt`, then copy the fixtures.
