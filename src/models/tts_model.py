import os
import urllib.parse
import httpx
import src.config as config

logger = config.logger

def get_docker_gateway():
    """Attempts to find the default gateway IP inside a Linux container."""
    try:
        if os.path.exists("/proc/net/route"):
            with open("/proc/net/route", "r") as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) > 2 and fields[1] == "00000000":
                        val = fields[2]
                        return ".".join([str(int(val[i:i+2], 16)) for i in (6, 4, 2, 0)])
    except Exception as e:
        logger.warning(f"Could not read default gateway: {e}")
    return None

async def generate_tts(text: str, voice: str = "af_heart", speed: float = 1.0) -> bytes:
    """Calls the local Kokoro TTS endpoint to generate speech audio."""
    logger.info(f"Generating TTS using voice '{voice}', speed {speed} for text: '{text[:30]}...'")
    
    # Try the configured URL first
    urls_to_try = [config.TTS_URL]
    
    # If the URL is localhost/127.0.0.1 and we might be in Docker, add container fallbacks
    parsed = urllib.parse.urlparse(config.TTS_URL)
    if parsed.hostname in ("localhost", "127.0.0.1") and os.path.exists("/.dockerenv"):
        # 1. Try default gateway IP
        gateway = get_docker_gateway()
        if gateway:
            gateway_url = parsed._replace(netloc=f"{gateway}:{parsed.port or 80}").geturl()
            if gateway_url not in urls_to_try:
                urls_to_try.append(gateway_url)
        # 2. Try host.docker.internal
        host_internal_url = parsed._replace(netloc=f"host.docker.internal:{parsed.port or 80}").geturl()
        if host_internal_url not in urls_to_try:
            urls_to_try.append(host_internal_url)
        # 3. Try common default gateway
        common_url = parsed._replace(netloc=f"172.17.0.1:{parsed.port or 80}").geturl()
        if common_url not in urls_to_try:
            urls_to_try.append(common_url)

    last_err = None
    for url in urls_to_try:
        try:
            async with httpx.AsyncClient() as httpx_client:
                lang_code = voice[0] if (voice and len(voice) > 0) else "a"
                response = await httpx_client.post(
                    url,
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
        except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError) as e:
            logger.warning(f"Failed to connect to TTS at {url}: {e}")
            last_err = e
            continue
            
    raise ConnectionError(f"Failed to connect to Kokoro TTS service at any of {urls_to_try}. Last error: {last_err}")
