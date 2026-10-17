import pytest

from runbook_assistant.config import PROJECT_ROOT, Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANSWER_MODEL", "custom-model")

    # _env_file=None ignores the real .env so the test is hermetic.
    settings = Settings(_env_file=None)

    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(settings)
    assert settings.answer_model == "custom-model"
    assert settings.runbooks_dir == PROJECT_ROOT / "data" / "runbooks"
