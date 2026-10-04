from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api import routes
from src.utils import logger

app = FastAPI(
    title="MTS Call Processing Agent",
    version="1.0.0",
    description="AI Agent for processing MTS incoming calls with multi-turn dialog"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(routes.router, prefix="/api")

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
