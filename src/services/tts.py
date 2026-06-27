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

import asyncio

# Track active streaming playbacks per guild: guild_id -> (queue, task)
active_stream_playbacks = {}

async def stop_voice_playback(guild_id: int, voice_client):
    """Stops any active voice playback and cancels the worker task for the guild."""
    if guild_id in active_stream_playbacks:
        queue, task = active_stream_playbacks[guild_id]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                break
        del active_stream_playbacks[guild_id]

    if voice_client and voice_client.is_playing():
        voice_client.stop()

async def play_tts_stream_in_voice(bot, ctx_or_interaction, voice_channel) -> asyncio.Queue:
    """
    Connects to the voice channel, terminates any running playbacks,
    starts a new streaming worker task, and returns the queue.
    """
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    guild = ctx_or_interaction.guild if is_interaction else ctx_or_interaction.guild
    guild_id = guild.id
    
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    try:
        if not voice_client or not voice_client.is_connected():
            voice_client = await voice_channel.connect()
        elif voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)
    except Exception as e:
        logger.error(f"Failed to connect to voice channel: {e}")
        return None
        
    await stop_voice_playback(guild_id, voice_client)
    
    queue = asyncio.Queue()
    task = asyncio.create_task(playback_worker(voice_client, queue))
    active_stream_playbacks[guild_id] = (queue, task)
    
    return queue

async def playback_worker(voice_client, queue: asyncio.Queue):
    """Pulls WAV bytes from the queue and plays them sequentially on the voice client."""
    try:
        while True:
            audio_data = await queue.get()
            if audio_data is None:  # Sentinel indicating end of stream
                queue.task_done()
                break
                
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
                temp_wav.write(audio_data)
                temp_path = temp_wav.name
                
            playing_done = asyncio.Event()
            
            def after_playing(error):
                if error:
                    logger.error(f"Error in streaming voice playback: {error}")
                try:
                    os.remove(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {temp_path}: {e}")
                voice_client.loop.call_soon_threadsafe(playing_done.set)
                
            if voice_client.is_playing():
                voice_client.stop()
                
            if not voice_client.is_connected():
                logger.info("Voice client disconnected. Exiting playback worker.")
                queue.task_done()
                break
                
            voice_client.play(discord.FFmpegPCMAudio(temp_path), after=after_playing)
            await playing_done.wait()
            queue.task_done()
    except asyncio.CancelledError:
        logger.info("Voice streaming playback cancelled.")
    except Exception as e:
        logger.error(f"Error in voice playback_worker: {e}")

async def play_tts_in_voice(bot, ctx_or_interaction, audio_data: bytes, suppress_message: bool = False):
    """Plays audio in the author's voice channel or uploads it as a WAV attachment if not possible."""
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    author = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    guild = ctx_or_interaction.guild if is_interaction else ctx_or_interaction.guild
    guild_id = guild.id
    
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    await stop_voice_playback(guild_id, voice_client)
    
    # Use bot's current voice channel if already connected, else fall back to author's channel
    if voice_client and voice_client.is_connected():
        voice_channel = voice_client.channel
    else:
        voice_channel = author.voice.channel if (author.voice and author.voice.channel) else None
    
    if not voice_channel:
        if suppress_message:
            logger.info("No voice channel found and suppress_message is True. Skipping playback/attachment.")
            return
        file = discord.File(io.BytesIO(audio_data), filename="speech.wav")
        msg = "Here is the spoken audio! (Join a voice channel to have me speak it live)"
        if is_interaction:
            await ctx_or_interaction.followup.send(content=msg, file=file)
        else:
            await ctx_or_interaction.send(content=msg, file=file)
        return
        
    try:
        if not voice_client or not voice_client.is_connected():
            voice_client = await voice_channel.connect()
        elif voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)
            
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
        
        if not suppress_message:
            msg = f"🗣️ Playing audio in **{voice_channel.name}**!"
            if is_interaction:
                await ctx_or_interaction.followup.send(content=msg)
            else:
                await ctx_or_interaction.send(content=msg)
            
    except Exception as e:
        logger.error(f"Failed to play in voice: {e}")
        if not suppress_message:
            file = discord.File(io.BytesIO(audio_data), filename="speech.wav")
            msg = f"Failed to play in voice ({e}). Sending audio file instead:"
            if is_interaction:
                await ctx_or_interaction.followup.send(content=msg, file=file)
            else:
                await ctx_or_interaction.send(content=msg, file=file)
