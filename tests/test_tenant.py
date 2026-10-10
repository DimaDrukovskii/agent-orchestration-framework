import json

import pytest
from fastapi.testclient import TestClient

from src import tenant
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}


def test_default_tenant_is_mts_with_its_own_files():
    assert settings.tenant == "mts"
    assert "Алина" in tenant.read_tenant_file("prompt.md")
    assert (tenant.tenant_dir() / "knowledge").is_dir()
    assert json.loads((tenant.tenant_dir() / "site_map.json").read_text(encoding="utf-8"))


def test_template_tenant_is_complete_and_has_no_company_leftovers():
    template = tenant.ROOT / "tenants" / "_template"
    prompt = (template / "prompt.md").read_text(encoding="utf-8")
    assert "[КОМПАНИЯ]" in prompt and "МТС" not in prompt
    assert json.loads((template / "tenant.json").read_text(encoding="utf-8"))["agent_name"]
    assert json.loads((template / "site_map.json").read_text(encoding="utf-8"))
    assert list((template / "knowledge").glob("*.md"))


def test_branding_merges_over_defaults_and_survives_bad_file(tmp_path, monkeypatch):
    (tmp_path / "tenants" / "x").mkdir(parents=True)
    monkeypatch.setattr(tenant, "ROOT", tmp_path)
    monkeypatch.setattr(settings, "tenant", "x")
    assert tenant.load_branding() == tenant.DEFAULT_BRANDING  # файла нет
    (tmp_path / "tenants" / "x" / "tenant.json").write_text("{не json", encoding="utf-8")
    assert tenant.load_branding() == tenant.DEFAULT_BRANDING  # файл битый
    (tmp_path / "tenants" / "x" / "tenant.json").write_text(json.dumps({"agent_name": "Ира", "lol": "x"}), encoding="utf-8")
    branding = tenant.load_branding()
    assert branding["agent_name"] == "Ира" and "lol" not in branding and branding["greeting"]


def test_missing_prompt_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="TENANT"):
        tenant.read_tenant_file("prompt.md")


def test_tenant_endpoint_requires_key_and_returns_branding():
    assert client.get("/api/tenant").status_code == 401
    body = client.get("/api/tenant", headers=HEADERS).json()
    assert body["agent_name"] == "Алина" and body["company"] == "МТС"


def test_speech_models_are_flagged_as_non_chat():
    from src.config import non_chat_model

    assert non_chat_model("openai/gpt-4o-transcribe") and non_chat_model("elevenlabs/text-to-speech-multilingual-v2")
    assert not non_chat_model("anthropic/claude-haiku-5.5") and not non_chat_model("google/gemini-3.5-flash-lite")
