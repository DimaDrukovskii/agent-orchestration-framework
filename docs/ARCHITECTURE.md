# MTS Call Agent Architecture

## Multi-turn Dialog Flow

```
Client (MTS АТС/API)
  ↓
POST /api/dialog {session_id, caller_phone, user_message, history}
  ↓
[CallOrchestrator.dialog()]
  │
  ├─ 1. Initialize or get session
  │  └─ sessions[session_id] → {caller_phone, messages: [...]}
  │
  ├─ 2. Append user_message to history
  │  └─ messages = [...previous messages, {role: "user", content: user_message}]
  │
  ├─ 3. LLMService.run_agent_dialog(messages)
  │  └─ Claude API → agent_response (with full context)
  │
  ├─ 4. LLMService.determine_action_from_dialog()
  │  └─ Analyze messages → action (transfer|escalate|resolve)
  │
  ├─ 5. Update session history
  │  └─ messages.append({role: "assistant", content: agent_response})
  │
  ├─ 6. If action is terminal → save_dialog()
  │  └─ Supabase → persist full conversation
  │
  └─ 7. Return DialogResponse
       └─ {session_id, agent_response, action, department, confidence, history}
```

## Components

### API Layer (`src/api/`)
- **routes.py**: FastAPI endpoints
- **models.py**: Pydantic request/response schemas

### Agent Core (`src/agent/`)
- **orchestrator.py**: Main logic coordinator
- **prompts.py**: System/few-shot prompts

### Services (`src/services/`)
- **llm.py**: Claude API wrapper
- **audio.py**: Whisper STT
- **storage.py**: Supabase persistence
- **cache.py**: Redis session/rate-limiting

### Utils (`src/utils/`)
- **logger.py**: Structured logging
- **errors.py**: Custom exceptions

## Execution Flow

### Sync (MVP)
1. Receive request → process sequentially → return response

### Async (Optimized)
1. Queue call → return call_id immediately
2. Process async → store in cache/db
3. Client polls `/call_status/{call_id}` for result

## Database Schema (Supabase)

```sql
CREATE TABLE calls (
    id UUID PRIMARY KEY,
    call_id TEXT UNIQUE,
    caller_phone TEXT,
    transcript TEXT,
    agent_response TEXT,
    action TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX calls_caller_phone ON calls(caller_phone);
CREATE INDEX calls_created_at ON calls(created_at);
```

## Rate Limiting (Redis)

- Per-phone rate: 10 calls/minute
- Per-IP rate: 100 calls/hour
- Session state: TTL 30min

## Error Handling

- **AudioTranscriptionError**: Fallback to text input
- **LLMError**: Return generic response
- **StorageError**: Continue without persistence
- **CacheError**: Skip caching, continue
