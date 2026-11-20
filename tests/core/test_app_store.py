from watchtower.app import create_app
from watchtower.core.migrations import MIGRATIONS


async def test_startup_opens_store_in_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app()
    async with app.router.lifespan_context(app):
        assert await app.state.store.schema_version() == len(MIGRATIONS)
    assert (tmp_path / "watchtower.db").exists()


async def test_startup_starts_scheduler_and_rejects_bad_env(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.scheduler._tasks  # ticker running (no handlers registered yet)

    monkeypatch.setenv("WATCHTOWER_HEALTHCHECK_MODE", "sometimes")
    app = create_app()
    with pytest.raises(ValueError):
        async with app.router.lifespan_context(app):
            pass
