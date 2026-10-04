# Quick Start

## 1. Init

```bash
cd mts-call-agent
bash scripts/setup.sh
source venv/bin/activate
```

## 2. Config

```bash
# Fill .env with your API keys
cat .env
```

## 3. Run

```bash
# Option A: Local
uvicorn src.main:app --reload

# Option B: Docker
docker-compose -f docker/docker-compose.yml up
```

## 4. Test Dialog API

**Шаг 1: Абонент пишет первое сообщение**
```bash
curl -X POST http://localhost:8000/api/dialog \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "call_001",
    "caller_phone": "+79999999999",
    "user_message": "Мне нужна помощь с интернетом",
    "history": []
  }'
```

**Шаг 2: Агент отвечает, абонент отвечает снова**
```bash
curl -X POST http://localhost:8000/api/dialog \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "call_001",
    "caller_phone": "+79999999999",
    "user_message": "Скорость очень низкая",
    "history": [
      {"role": "user", "content": "Мне нужна помощь с интернетом"},
      {"role": "assistant", "content": "Чем я вам помочь? Какие проблемы с интернетом?"}
    ]
  }'
```

**Получить историю диалога:**
```bash
curl http://localhost:8000/api/dialog/history/call_001
```

## 5. Deploy to GitLab

```bash
git init
git remote add origin https://gitlab.com/your-group/mts-call-agent.git
git add .
git commit -m "Initial commit: MTS call agent structure"
git push -u origin main
```

GitLab CI/CD will auto-trigger tests and build Docker image.

---

## Project Checklist for Hackathon

- [ ] Fill .env with actual API keys (Anthropic, OpenAI, Supabase, Redis)
- [ ] Implement actual `determine_action()` logic (or use Claude)
- [ ] Test audio transcription with real calls
- [ ] Add database migrations (Supabase schema)
- [ ] Implement rate limiting (Redis)
- [ ] Write more unit tests
- [ ] Document API endpoints
- [ ] Set up error monitoring (Sentry)

## Architecture

See `docs/ARCHITECTURE.md` for full flow and design.

## Files Structure

```
src/
  ├─ main.py          (FastAPI app)
  ├─ config.py        (Settings)
  ├─ constants.py     (Prompts, enums)
  ├─ agent/           (Core logic)
  │  ├─ orchestrator.py
  │  └─ prompts.py
  ├─ api/             (HTTP layer)
  │  ├─ routes.py
  │  └─ models.py
  ├─ services/        (External integrations)
  │  ├─ llm.py        (Claude)
  │  ├─ audio.py      (Whisper)
  │  ├─ storage.py    (Supabase)
  │  └─ cache.py      (Redis)
  └─ utils/           (Helpers)
     ├─ logger.py
     └─ errors.py
```

**Total lines of code:** ~500 (MVP ready)
