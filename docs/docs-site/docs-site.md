# Docs site

This site is built from the Markdown in `docs/` by [Zensical](https://zensical.org), configured in `mkdocs.yml`, and
served by the app at `/docs`. `docs/` is the single source; the built `site/` folder is never edited or committed.

## Adding a page
1. Write the page under `docs/<area>/`.
2. Add it to `nav` in `mkdocs.yml`. `tests/test_docs_site.py` fails when a page is missing from `nav`, or `nav` points
   to a missing page.
3. Link it from [the docs home](../index.md) if it belongs in the overview.

Links between pages are relative `.md` paths (`../core/store.md`). Links to files outside `docs/` don't work on the site.

## Building and previewing
```
uv run --group docs zensical build     # docs/ → site/, then /docs on a running app serves it
uv run --group docs zensical serve     # live preview on http://localhost:8000
```

Zensical is pinned in the `docs` dependency group of `pyproject.toml`; it isn't installed in the app's runtime.

## In the container
The `docs` stage of the `Dockerfile` builds the site, and the final image copies it to `/app/site`. A docs error fails
`docker build`, on purpose ([ADR 0001](../decisions/0001-tech-stack.md)).

## Serving
`watchtower/docs_site.py` mounts `site/` at `/docs` (`/docs` redirects to `/docs/`; unknown pages get the site's 404
page). When `site/` hasn't been built, `/docs` answers `503` with the build command. FastAPI's own Swagger and ReDoc
pages are switched off, because `/docs` belongs to this site.
