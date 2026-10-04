#!/usr/bin/env python3
"""Test script for local call agent testing"""

import asyncio
import httpx
import json
from typing import Optional


async def test_process_call(
    call_id: str = "test-001",
    audio_url: str = "https://example.com/test_audio.wav",
    caller_phone: str = "+7-999-999-99-99",
    context: Optional[str] = None
):
    """Test call processing"""
    
    payload = {
        "call_id": call_id,
        "audio_url": audio_url,
        "caller_phone": caller_phone,
        "context": context or "general inquiry"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://localhost:8000/api/process_call",
                json=payload,
                timeout=30.0
            )
            print(f"Status: {response.status_code}")
            print(f"Response:\n{json.dumps(response.json(), indent=2)}")
        except Exception as e:
            print(f"Error: {str(e)}")


async def test_health():
    """Test health check"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get("http://localhost:8000/health")
            print(f"Health: {response.json()}")
        except Exception as e:
            print(f"Health check failed: {str(e)}")


async def main():
    print("Testing MTS Call Agent...")
    print("\n1. Health Check:")
    await test_health()
    
    print("\n2. Processing Call:")
    await test_process_call()


if __name__ == "__main__":
    asyncio.run(main())
