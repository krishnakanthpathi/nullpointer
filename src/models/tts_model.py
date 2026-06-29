import httpx
import src.config as config

logger = config.logger

async def generate_tts(text: str, voice: str = "af_heart", speed: float = 1.0) -> bytes:
    """Calls the local Kokoro TTS endpoint to generate speech audio."""
    logger.info(f"Generating TTS using voice '{voice}', speed {speed} for text: '{text[:30]}...'")
    async with httpx.AsyncClient() as httpx_client:
        try:
            lang_code = voice[0] if (voice and len(voice) > 0) else "a"
            response = await httpx_client.post(
                config.TTS_URL,
                json={
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "lang_code": lang_code
                },
                timeout=30.0
            )
            if response.status_code != 200:
                raise Exception(f"TTS service returned status {response.status_code}: {response.text}")
            return response.content
        except httpx.ConnectError:
            raise ConnectionError(f"Failed to connect to Kokoro TTS service at {config.TTS_URL}. Is it running?")
