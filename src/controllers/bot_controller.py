import discord
from discord.ext import commands
import re
import asyncio
import src.config as config
from src.models.llm_model import llm_manager, build_user_content
from src.models.tts_model import generate_tts
from src.views.discord_view import DiscordView

logger = config.logger

class BotController:
    """Orchestrates control flows, command routing, and interactions between Models and Views."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle_ready(self):
        """Initializes logging and command synchronization on bot ready."""
        logger.info(f'Logged in as {self.bot.user.name} ({self.bot.user.id})')
        logger.info('Syncing slash commands globally...')
        try:
            synced = await self.bot.tree.sync()
            logger.info(f"Successfully synced {len(synced)} global command(s)")
        except Exception as e:
            logger.error(f"Failed to sync global slash commands: {e}")
            
        logger.info('Syncing slash commands to active guilds instantly...')
        for guild in self.bot.guilds:
            try:
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
                logger.info(f"Successfully synced command tree to guild: {guild.name} ({guild.id})")
            except Exception as e:
                logger.error(f"Failed to sync command tree to guild {guild.name}: {e}")
        try:
            await self.bot.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(type=discord.ActivityType.listening, name="!ask | /ask")
            )
            logger.info("Successfully set bot status to online with activity.")
        except Exception as e:
            logger.error(f"Failed to set bot presence: {e}")
        logger.info('Bot is ready!')

    async def _stream_sentences(self, chunk_generator):
        """Async generator that yields complete sentences from streamed text chunks."""
        buffer = ""
        sentence_end_re = re.compile(
            r'(?<!\bMr)(?<!\bMs)(?<!\bDr)(?<!\bSt)(?<!\bJr)(?<!\bSr)(?<!\bvs)'
            r'(?<=[.!?])\s+|'
            r'(?<=\n)\s*'
        )
        async for chunk in chunk_generator:
            buffer += chunk
            parts = sentence_end_re.split(buffer)
            if len(parts) > 1:
                for part in parts[:-1]:
                    part = part.strip()
                    if part:
                        yield part
                buffer = parts[-1]
        if buffer.strip():
            yield buffer.strip()

    async def handle_ask(self, ctx_or_interaction, question: str, attachment: discord.Attachment = None, provider: str = None):
        """Handles streaming LLM response, updating message text and streaming TTS concurrently."""
        view = DiscordView(self.bot, ctx_or_interaction)
        
        # Parse provider from question if not explicitly provided (e.g., in prefix commands or mentions)
        if not provider and question:
            # Check for --provider <name> or -p <name> at the start of the question
            match = re.match(r'^(?:--provider|-p)\s+(\w+)\s*(.*)$', question, re.IGNORECASE)
            if match:
                parsed_provider = match.group(1).lower().strip()
                if parsed_provider in {"gemini", "openai", "ollama"}:
                    provider = parsed_provider
                    question = match.group(2)
            else:
                # Check if the first word is a provider name
                parts = question.split(maxsplit=1)
                first_word = parts[0].lower().strip()
                if first_word in {"gemini", "openai", "ollama"}:
                    provider = first_word
                    question = parts[1] if len(parts) > 1 else ""
        
        # 1. Send placeholder message
        await view.send_placeholder()
        
        # 2. Check voice connection
        voice_channel = view.get_author_voice_channel()
        voice_client = view.get_connected_voice_client()
        
        play_queue = None
        voice_connected = False
        if voice_channel or (voice_client and voice_client.is_connected()):
            target_channel = voice_client.channel if (voice_client and voice_client.is_connected()) else voice_channel
            play_queue = await view.play_tts_stream_in_voice(target_channel)
            if play_queue is not None:
                voice_connected = True
                
        # 3. Stream data structures
        user_content = await build_user_content(question, attachment)
        chunk_queue = asyncio.Queue()
        
        async def llm_stream_feeder():
            try:
                async for chunk in llm_manager.generate_response_stream(view.channel_id, user_content, provider=provider):
                    await view.update_stream_text(chunk)
                    await chunk_queue.put(chunk)
            except Exception as e:
                logger.error(f"Error in LLM stream feeder: {e}")
                await view.send_error(str(e))
            finally:
                await chunk_queue.put(None)
                
        async def chunk_generator_wrapper():
            while True:
                chunk = await chunk_queue.get()
                if chunk is None:
                    chunk_queue.task_done()
                    break
                yield chunk
                chunk_queue.task_done()
                
        async def sentence_processor():
            try:
                async for sentence in self._stream_sentences(chunk_generator_wrapper()):
                    if voice_connected and play_queue is not None:
                        clean_text = re.sub(r'```.*?```', '[code block]', sentence, flags=re.DOTALL)
                        clean_text = clean_text.replace('*', '').replace('_', '').replace('#', '').strip()
                        if clean_text:
                            try:
                                logger.info(f"Generating TTS stream segment: '{clean_text[:30]}...'")
                                audio_data = await generate_tts(clean_text)
                                await play_queue.put(audio_data)
                            except Exception as tts_err:
                                logger.error(f"Error in TTS streaming generation: {tts_err}")
            finally:
                if voice_connected and play_queue is not None:
                    await play_queue.put(None)

        # Gather tasks
        await asyncio.gather(llm_stream_feeder(), sentence_processor())
        
        # Final message edit
        await view.finalize_stream()

    async def handle_speak(self, ctx_or_interaction, text: str, voice: str = None, speed: float = 1.0):
        """Generates TTS audio and plays it in a voice channel or sends it as an attachment."""
        view = DiscordView(self.bot, ctx_or_interaction)
        if not text.strip():
            await view.send_message("Please provide text to speak. Example: `!speak hello world` or `!speak af_bella hello`.")
            return
            
        supported_voices = {
            "af_heart", "af_bella", "bf_emma", "bf_clara", "am_adam", "am_michael",
            "pm_alex", "ef_dora", "ff_siwis", "hf_alpha", "hf_beta", "if_sara",
            "jf_alpha", "zf_xiaoxiao"
        }
        
        # If voice is not explicitly set (e.g. prefix command), try to parse from the input text
        if voice is None:
            parts = text.split(maxsplit=1)
            if parts[0] in supported_voices:
                voice = parts[0]
                if len(parts) > 1:
                    text = parts[1]
                else:
                    await view.send_message(f"Please provide text after the voice name '{voice}'.")
                    return
            else:
                voice = "af_heart"
                
        if not view.is_interaction:
            # Prefix command: show typing indicator
            async with ctx_or_interaction.typing():
                try:
                    audio_data = await generate_tts(text, voice, speed)
                    await view.play_tts_in_voice(audio_data)
                except Exception as e:
                    await view.send_error(str(e))
        else:
            try:
                audio_data = await generate_tts(text, voice, speed)
                await view.play_tts_in_voice(audio_data)
            except Exception as e:
                await view.send_error(str(e))

    async def handle_clear(self, ctx_or_interaction):
        """Clears conversation history for the current channel."""
        view = DiscordView(self.bot, ctx_or_interaction)
        llm_manager.clear_history(view.channel_id)
        await view.send_message("🧹 Conversation history for this channel has been cleared!")

    async def handle_provider(self, ctx_or_interaction, name: str = None):
        """Gets or sets the current channel's LLM provider."""
        view = DiscordView(self.bot, ctx_or_interaction)
        if not name:
            current = llm_manager.get_provider(view.channel_id)
            await view.send_message(f"Current provider for this channel is: **{current}**")
            return
            
        name = name.lower().strip()
        if name not in {"gemini", "openai", "ollama"}:
            await view.send_message("Invalid provider. Choose from: gemini, openai, ollama")
            return
            
        llm_manager.set_provider(view.channel_id, name)
        await view.send_message(f"✅ LLM provider for this channel set to **{name}**.")

    async def handle_model(self, ctx_or_interaction, model_name: str = None):
        """Gets or sets the current channel's model."""
        view = DiscordView(self.bot, ctx_or_interaction)
        if not model_name:
            provider = llm_manager.get_provider(view.channel_id)
            current = llm_manager.get_model(view.channel_id, provider)
            await view.send_message(f"Current model for this channel (provider: {provider}) is: **{current}**")
            return
            
        llm_manager.set_model(view.channel_id, model_name)
        provider = llm_manager.get_provider(view.channel_id)
        await view.send_message(f"✅ Model for this channel (provider: {provider}) set to **{model_name}**.")

    async def handle_leave(self, ctx_or_interaction):
        """Leaves the current voice channel."""
        view = DiscordView(self.bot, ctx_or_interaction)
        voice_client = view.get_connected_voice_client()
        if voice_client and voice_client.is_connected():
            await view.stop_voice_playback()
            await voice_client.disconnect()
            await view.send_message("👋 Disconnected from the voice channel!")
        else:
            await view.send_message("I am not connected to any voice channel.", ephemeral=True)

    async def handle_message(self, message: discord.Message):
        """Filters user message updates, processes mentions, and executes normal bot prefix commands."""
        # Don't let the bot reply to itself
        if message.author == self.bot.user:
            return
            
        # Handle custom /leave plain text message shortcut
        if message.content.strip().lower() == '/leave':
            view = DiscordView(self.bot, message)
            voice_client = view.get_connected_voice_client()
            if voice_client and voice_client.is_connected():
                await view.stop_voice_playback()
                await voice_client.disconnect()
                await message.reply("👋 Disconnected from the voice channel!")
            else:
                await message.reply("I am not connected to any voice channel.")
            return
            
        # Respond to mentions
        if self.bot.user in message.mentions:
            question = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
            question = question.replace(f'<@!{self.bot.user.id}>', '').strip()
            
            attachment = None
            if message.attachments:
                for att in message.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        attachment = att
                        break
                        
            if question or attachment:
                try:
                    ctx = await self.bot.get_context(message)
                    await self.handle_ask(ctx, question, attachment)
                except Exception as e:
                    logger.error(f"Error in on_message llm stream: {e}")
            else:
                await message.reply("How can I help you today? Ask a question, upload an image, or use `/ask`.")
                
        # Process prefix commands
        await self.bot.process_commands(message)
