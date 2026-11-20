from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PLACEHOLDER = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Watchtower</title></head>
<body><h1>Watchtower</h1><p>No portals yet.</p></body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> str:
    return _PLACEHOLDER
