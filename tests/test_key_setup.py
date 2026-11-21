from pathlib import Path

from scripts import key_setup


def test_key_form_never_renders_existing_secrets(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("POLZA_API_KEY=private-polza\nTTS_API_KEY=private-voice\nTTS_VOICE_ID=voice-123\n")
    monkeypatch.setattr(key_setup, "ENV_FILE", env)

    html = key_setup.page().decode()
    assert "подключён" in html
    assert "private-polza" not in html
    assert "private-voice" not in html
    assert "voice-123" not in html


def test_saving_one_key_preserves_the_other_values_and_file_permissions(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# existing\nPOLZA_API_KEY=\nTTS_API_KEY=voice-secret\nTTS_VOICE_ID=voice-123\n")
    monkeypatch.setattr(key_setup, "ENV_FILE", env)
    monkeypatch.setattr(key_setup, "ROOT", tmp_path)

    key_setup.save_values({"POLZA_API_KEY": "new-polza-key"})

    text = env.read_text()
    assert "POLZA_API_KEY=new-polza-key" in text
    assert "TTS_API_KEY=voice-secret" in text
    assert "TTS_VOICE_ID=voice-123" in text
    assert "LLM_PROVIDER=polza" in text
    assert env.stat().st_mode & 0o777 == 0o600
