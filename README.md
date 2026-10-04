# MTS Call Processing AI Agent

Агент для обработки входящих звонков на базе Claude API.

## Quick Start

```bash
git clone <repo>
cd mts-call-agent
python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Заполни переменные в .env

uvicorn src.main:app --reload
```

## API

**POST** `/api/process_call`

```json
{
  "call_id": "uuid",
  "audio_url": "string",
  "caller_phone": "string",
  "context": "string (optional)"
}
```

**Response:**
```json
{
  "call_id": "uuid",
  "transcript": "string",
  "agent_response": "string",
  "action": "transfer|escalate|resolve",
  "confidence": 0.95
}
```

## Architecture

- **STT**: Whisper API
- **Agent**: Claude + LangGraph
- **Storage**: Supabase + Redis
- **Framework**: FastAPI

See `docs/` for detailed docs.
