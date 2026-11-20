"""Serve every mock portal: `python -m mockportals` (port from MOCK_PORT, default 8081; variants from MOCK_VARIANTS)."""

import os

import uvicorn

from mockportals.app import create_app


def main() -> None:
    uvicorn.run(
        create_app(), host=os.environ.get("MOCK_HOST", "127.0.0.1"), port=int(os.environ.get("MOCK_PORT", "8081"))
    )


if __name__ == "__main__":
    main()
