"""Basic skeleton tests."""


def test_skeleton_imports_settings():
    from app.config.settings import get_settings

    settings = get_settings()
    assert settings.db_name == "CRM"
