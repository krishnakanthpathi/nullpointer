import discord
from discord.ext import commands
import time
import os
import io
import tempfile
import asyncio
import src.config as config

logger = config.logger

# Global tracker for active stream playbacks per guild: guild_id -> (queue, task)
active_stream_playbacks = {}

class DiscordView:
    """Manages presentation and interaction rendering to Discord."""
    
    def __init__(self, bot: commands.Bot, ctx_or_interaction: discord.Interaction | commands.Context):
        self.bot = bot
        self.ctx_or_interaction = ctx_or_interaction
        self.is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        self.guild = ctx_or_interaction.guild
        self.channel_id = ctx_or_interaction.channel_id if self.is_interaction else ctx_or_interaction.channel.id
        self.author = ctx_or_interaction.user if self.is_interaction else ctx_or_interaction.author
        
        self.msg = None
        self.current_segment_text = ""
        self.last_edit_time = 0.0

    async def send_placeholder(self):
        """Sends a thinking/placeholder message to user."""
        if self.is_interaction:
            self.msg = await self.ctx_or_interaction.followup.send("⏳ *Thinking...*")
        else:
            self.msg = await self.ctx_or_interaction.send("⏳ *Thinking...*")
        self.last_edit_time = time.time()
        return self.msg

    async def update_stream_text(self, chunk: str):
        """Accumulates chunks, splits them if they are too long, and edits or sends new messages."""
        self.current_segment_text += chunk
        
        # Check if current segment is close to the 2000 character limit
        if len(self.current_segment_text) >= 1900:
            # Try to split at a newline or space to avoid cutting off words/code blocks
            split_idx = self.current_segment_text.rfind('\n', 1500, 1900)
            if split_idx == -1:
                split_idx = self.current_segment_text.rfind(' ', 1500, 1900)
            if split_idx == -1:
                split_idx = 1900
            
            text_to_send = self.current_segment_text[:split_idx]
            self.current_segment_text = self.current_segment_text[split_idx:]
            
            # Edit the current message
            if self.is_interaction:
                await self.ctx_or_interaction.followup.edit_message(self.msg.id, content=text_to_send)
            else:
                await self.msg.edit(content=text_to_send)
            
            # Start a new placeholder message
            if self.is_interaction:
                self.msg = await self.ctx_or_interaction.followup.send("⏳ *Thinking...*")
            else:
                self.msg = await self.ctx_or_interaction.send("⏳ *Thinking...*")
            
            self.last_edit_time = time.time()
        else:
            # Update text periodically
            current_time = time.time()
            if current_time - self.last_edit_time > 1.5:
                if self.is_interaction:
                    await self.ctx_or_interaction.followup.edit_message(self.msg.id, content=self.current_segment_text)
                else:
                    await self.msg.edit(content=self.current_segment_text)
                self.last_edit_time = current_time

    async def finalize_stream(self):
        """Edits the final message with the final response, or deletes it if empty."""
        if self.current_segment_text.strip():
            if self.is_interaction:
                await self.ctx_or_interaction.followup.edit_message(self.msg.id, content=self.current_segment_text[:2000])
            else:
                await self.msg.edit(content=self.current_segment_text[:2000])
        else:
            try:
                if self.is_interaction:
                    await self.ctx_or_interaction.followup.delete_message(self.msg.id)
                else:
                    await self.msg.delete()
            except Exception as delete_err:
                logger.warning(f"Could not delete empty final message: {delete_err}")

    async def send_error(self, error_msg: str):
        """Displays an error message to the user."""
        formatted_error = f"Sorry, I encountered an error: {error_msg[:1900]}"
        if self.is_interaction:
            await self.ctx_or_interaction.followup.send(formatted_error)
        else:
            await self.ctx_or_interaction.send(formatted_error)

    async def send_message(self, content: str, ephemeral: bool = False):
        """Sends a plain message."""
        if self.is_interaction:
            await self.ctx_or_interaction.response.send_message(content, ephemeral=ephemeral)
        else:
            await self.ctx_or_interaction.send(content)

    async def reply_message(self, content: str):
        """Replies to the user's message."""
        if self.is_interaction:
            await self.ctx_or_interaction.followup.send(content)
        else:
            await self.ctx_or_interaction.reply(content)

    def get_author_voice_channel(self):
        """Returns the voice channel of the command author, if any."""
        return self.author.voice.channel if (self.author.voice and self.author.voice.channel) else None

    def get_connected_voice_client(self):
        """Returns the bot's voice client for this guild, if any."""
        return discord.utils.get(self.bot.voice_clients, guild=self.guild)

    async def stop_voice_playback(self):
        """Stops any active voice playback and cancels the worker task for the guild."""
        guild_id = self.guild.id
        voice_client = self.get_connected_voice_client()
        
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

    async def play_tts_stream_in_voice(self, voice_channel) -> asyncio.Queue:
        """Connects to the voice channel, cancels running playbacks, starts a new streaming worker."""
        guild_id = self.guild.id
        voice_client = self.get_connected_voice_client()
        try:
            if not voice_client or not voice_client.is_connected():
                voice_client = await voice_channel.connect()
            elif voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
        except Exception as e:
            logger.error(f"Failed to connect to voice channel: {e}")
            return None
            
        await self.stop_voice_playback()
        
        queue = asyncio.Queue()
        task = asyncio.create_task(self._playback_worker(voice_client, queue))
        active_stream_playbacks[guild_id] = (queue, task)
        
        return queue

    async def _playback_worker(self, voice_client, queue: asyncio.Queue):
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

    async def play_tts_in_voice(self, audio_data: bytes, suppress_message: bool = False):
        """Plays audio in the author's voice channel or uploads it as a WAV attachment if not possible."""
        voice_client = self.get_connected_voice_client()
        await self.stop_voice_playback()
        
        # Use bot's current voice channel if already connected, else fall back to author's channel
        if voice_client and voice_client.is_connected():
            voice_channel = voice_client.channel
        else:
            voice_channel = self.get_author_voice_channel()
        
        if not voice_channel:
            if suppress_message:
                logger.info("No voice channel found and suppress_message is True. Skipping playback/attachment.")
                return
            file = discord.File(io.BytesIO(audio_data), filename="speech.wav")
            msg = "Here is the spoken audio! (Join a voice channel to have me speak it live)"
            if self.is_interaction:
                await self.ctx_or_interaction.followup.send(content=msg, file=file)
            else:
                await self.ctx_or_interaction.send(content=msg, file=file)
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
                if self.is_interaction:
                    await self.ctx_or_interaction.followup.send(content=msg)
                else:
                    await self.ctx_or_interaction.send(content=msg)
                
        except Exception as e:
            logger.error(f"Failed to play in voice: {e}")
            if not suppress_message:
                file = discord.File(io.BytesIO(audio_data), filename="speech.wav")
                msg = f"Failed to play in voice ({e}). Sending audio file instead:"
                if self.is_interaction:
                    await self.ctx_or_interaction.followup.send(content=msg, file=file)
                else:
                    await self.ctx_or_interaction.send(content=msg, file=file)
