import os
import io
import tempfile
import discord
import httpx
import src.config as config

logger = config.logger

async def generate_tts(text: str, voice: str = "af_heart", speed: float = 1.0) -> bytes:
    """Calls the local Kokoro TTS endpoint to generate speech audio."""
    logger.info(f"Generating TTS using voice '{voice}', speed {speed} for text: '{text[:30]}...'")
    async with httpx.AsyncClient() as httpx_client:
        try:
            response = await httpx_client.post(
                config.TTS_URL,
                json={
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "lang_code": "a"
                },
                timeout=30.0
            )
            if response.status_code != 200:
                raise Exception(f"TTS service returned status {response.status_code}: {response.text}")
            return response.content
        except httpx.ConnectError:
            raise ConnectionError(f"Failed to connect to Kokoro TTS service at {config.TTS_URL}. Is it running?")

async def play_tts_in_voice(bot, ctx_or_interaction, audio_data: bytes):
    """Plays audio in the author's voice channel or uploads it as a WAV attachment if not possible."""
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    author = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild if is_interaction else ctx_or_interaction.guild
    
    voice_channel = author.voice.channel if (author.voice and author.voice.channel) else None
    
    if not voice_channel:
        file = discord.File(io.BytesIO(audio_data), filename="speech.wav")
        msg = "Here is the spoken audio! (Join a voice channel to have me speak it live)"
        if is_interaction:
            await ctx_or_interaction.followup.send(content=msg, file=file)
        else:
            await ctx_or_interaction.send(content=msg, file=file)
        return
        
    try:
        voice_client = discord.utils.get(bot.voice_clients, guild=guild)
        if voice_client and voice_client.is_connected():
            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
        else:
            voice_client = await voice_channel.connect()
            
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
            temp_wav.write(audio_data)
            temp_path = temp_wav.name
            
        if voice_client.is_playing():
            voice_client.stop()
            
        def after_playing(error):
            if error:
                logger.error(f"Error playing voice audio: {error}")
            try:
                os.remove(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {temp_path}: {e}")
                
        voice_client.play(discord.FFmpegPCMAudio(temp_path), after=after_playing)
        
        msg = f"🗣️ Playing audio in **{voice_channel.name}**!"
        if is_interaction:
            await ctx_or_interaction.followup.send(content=msg)
        else:
            await ctx_or_interaction.send(content=msg)
            
    except Exception as e:
        logger.error(f"Failed to play in voice: {e}. Falling back to attachment.")
        file = discord.File(io.BytesIO(audio_data), filename="speech.wav")
        msg = f"Failed to play in voice ({e}). Sending audio file instead:"
        if is_interaction:
            await ctx_or_interaction.followup.send(content=msg, file=file)
        else:
            await ctx_or_interaction.send(content=msg, file=file)
