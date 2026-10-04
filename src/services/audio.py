import httpx
from openai import OpenAI
from src.config import settings
from src.utils import logger
import os


class AudioService:
    def __init__(self):
        self.openai_client = OpenAI(api_key=settings.openai_api_key)
        self.model = "whisper-1"
    
    async def transcribe(self, audio_url: str) -> str:
        """
        Convert audio file to text using Whisper API.
        Supports URL or local file path.
        """
        try:
            logger.info(f"Transcribing audio: {audio_url}")
            
            # Download audio from URL
            if audio_url.startswith("http"):
                audio_data = await self._download_audio(audio_url)
                audio_file = ("audio.wav", audio_data, "audio/wav")
            else:
                # Local file
                with open(audio_url, "rb") as f:
                    audio_file = ("audio.wav", f.read(), "audio/wav")
            
            # Call Whisper API
            transcript = self.openai_client.audio.transcriptions.create(
                model=self.model,
                file=audio_file
            )
            
            logger.info(f"Transcription complete: {len(transcript.text)} chars")
            return transcript.text
        
        except Exception as e:
            logger.error(f"Error transcribing audio: {str(e)}")
            raise
    
    async def _download_audio(self, url: str) -> bytes:
        """Download audio file from URL"""
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
