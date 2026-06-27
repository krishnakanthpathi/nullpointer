import discord
from discord import app_commands
from discord.ext import commands
import src.config as config
import src.services.llm as llm
import src.services.tts as tts
import re
import time
import asyncio

logger = config.logger

# Configure Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True  # Required for joining and speaking in voice channels

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} ({bot.user.id})')
    logger.info('Syncing slash commands globally...')
    try:
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} global command(s)")
    except Exception as e:
        logger.error(f"Failed to sync global slash commands: {e}")
        
    logger.info('Syncing slash commands to active guilds instantly...')
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info(f"Successfully synced command tree to guild: {guild.name} ({guild.id})")
        except Exception as e:
            logger.error(f"Failed to sync command tree to guild {guild.name}: {e}")
    logger.info('Bot is ready!')

async def stream_sentences(chunk_generator):
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

async def handle_llm_stream(ctx_or_interaction, question, attachment=None):
    """Streams LLM response chunks, updates text on Discord, and plays TTS concurrently."""
    is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
    guild = ctx_or_interaction.guild
    channel_id = ctx_or_interaction.channel_id if is_interaction else ctx_or_interaction.channel.id
    author = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
    
    # 1. Send placeholder message
    if is_interaction:
        msg = await ctx_or_interaction.followup.send("⏳ *Thinking...*")
    else:
        msg = await ctx_or_interaction.send("⏳ *Thinking...*")
        
    # 2. Check voice connection
    voice_channel = author.voice.channel if (author.voice and author.voice.channel) else None
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    
    play_queue = None
    voice_connected = False
    if voice_channel or (voice_client and voice_client.is_connected()):
        target_channel = voice_client.channel if (voice_client and voice_client.is_connected()) else voice_channel
        play_queue = await tts.play_tts_stream_in_voice(bot, ctx_or_interaction, target_channel)
        if play_queue is not None:
            voice_connected = True
            
    # 3. Stream data structures
    user_content = await llm.build_user_content(question, attachment)
    full_text = ""
    last_edit_time = time.time()
    chunk_queue = asyncio.Queue()
    
    async def llm_stream_feeder():
        nonlocal full_text, last_edit_time
        try:
            async for chunk in llm.llm_manager.generate_response_stream(channel_id, user_content):
                full_text += chunk
                await chunk_queue.put(chunk)
                
                # Update text periodically
                current_time = time.time()
                if current_time - last_edit_time > 1.5:
                    display_text = full_text[:1990] + "..." if len(full_text) > 1990 else full_text
                    if is_interaction:
                        await ctx_or_interaction.followup.edit_message(msg.id, content=display_text)
                    else:
                        await msg.edit(content=display_text)
                    last_edit_time = current_time
        except Exception as e:
            logger.error(f"Error in LLM stream feeder: {e}")
            error_msg = f"Sorry, I encountered an error: {str(e)[:1900]}"
            if is_interaction:
                await ctx_or_interaction.followup.send(error_msg)
            else:
                await ctx_or_interaction.send(error_msg)
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
            async for sentence in stream_sentences(chunk_generator_wrapper()):
                if voice_connected and play_queue is not None:
                    clean_text = re.sub(r'```.*?```', '[code block]', sentence, flags=re.DOTALL)
                    clean_text = clean_text.replace('*', '').replace('_', '').replace('#', '').strip()
                    if clean_text:
                        try:
                            logger.info(f"Generating TTS stream segment: '{clean_text[:30]}...'")
                            audio_data = await tts.generate_tts(clean_text)
                            await play_queue.put(audio_data)
                        except Exception as tts_err:
                            logger.error(f"Error in TTS streaming generation: {tts_err}")
        finally:
            if voice_connected and play_queue is not None:
                await play_queue.put(None)

    # Gather tasks
    await asyncio.gather(llm_stream_feeder(), sentence_processor())
    
    # Final message edit
    if full_text.strip():
        if is_interaction:
            await ctx_or_interaction.followup.edit_message(msg.id, content=full_text[:2000])
        else:
            await msg.edit(content=full_text[:2000])

# --- prefix commands ---

@bot.command(name='ask', help='Ask the bot a question (remembers context, attach image to analyze).')
async def ask(ctx, *, question: str = ""):
    attachment = ctx.message.attachments[0] if ctx.message.attachments else None
    if not question.strip() and not attachment:
        await ctx.send("Please provide a question or attach an image.")
        return
    await handle_llm_stream(ctx, question, attachment)

@bot.command(name='speak', help='Generate text-to-speech. Usage: !speak [voice_name] [text]')
async def speak(ctx, *, args: str = ""):
    if not args.strip():
        await ctx.send("Please provide text to speak. Example: `!speak hello world` or `!speak af_bella hello`.")
        return
        
    parts = args.split(maxsplit=1)
    voice = "af_heart"
    text = args
    
    supported_voices = {"af_heart", "af_bella", "bf_emma", "bf_clara", "am_adam", "am_michael", "pm_alex"}
    if parts[0] in supported_voices:
        voice = parts[0]
        if len(parts) > 1:
            text = parts[1]
        else:
            await ctx.send(f"Please provide text after the voice name '{voice}'.")
            return
            
    async with ctx.typing():
        try:
            audio_data = await tts.generate_tts(text, voice)
            await tts.play_tts_in_voice(bot, ctx, audio_data)
        except Exception as e:
            await ctx.send(f"Sorry, I encountered an error: {str(e)[:1900]}")

@bot.command(name='clear', help='Clear conversation history for this channel.')
async def clear(ctx):
    llm.llm_manager.clear_history(ctx.channel.id)
    await ctx.send("🧹 Conversation history for this channel has been cleared!")

@bot.command(name="provider", help="Set the LLM provider for this channel (gemini, openai, ollama).")
async def provider_prefix(ctx, name: str = None):
    if not name:
        current = llm.llm_manager.get_provider(ctx.channel.id)
        await ctx.send(f"Current provider for this channel is: **{current}**")
        return
    name = name.lower().strip()
    if name not in {"gemini", "openai", "ollama"}:
        await ctx.send("Invalid provider. Choose from: gemini, openai, ollama")
        return
    llm.llm_manager.set_provider(ctx.channel.id, name)
    await ctx.send(f"✅ LLM provider for this channel set to **{name}**.")

@bot.command(name="model", help="Set the model name for this channel.")
async def model_prefix(ctx, model_name: str = None):
    if not model_name:
        provider = llm.llm_manager.get_provider(ctx.channel.id)
        current = llm.llm_manager.get_model(ctx.channel.id, provider)
        await ctx.send(f"Current model for this channel (provider: {provider}) is: **{current}**")
        return
    llm.llm_manager.set_model(ctx.channel.id, model_name)
    provider = llm.llm_manager.get_provider(ctx.channel.id)
    await ctx.send(f"✅ Model for this channel (provider: {provider}) set to **{model_name}**.")

@bot.command(name="leave", help="Disconnect the bot from the voice channel.")
async def leave(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_connected():
        await tts.stop_voice_playback(ctx.guild.id, voice_client)
        await voice_client.disconnect()
        await ctx.send("👋 Disconnected from the voice channel!")
    else:
        await ctx.send("I am not connected to any voice channel.")

# --- slash commands ---

@bot.tree.command(name="ask", description="Ask the bot a question (remembers context, accepts image attachment).")
@app_commands.describe(question="The question to ask the bot", attachment="An optional image to analyze")
async def ask_slash(interaction: discord.Interaction, question: str, attachment: discord.Attachment = None):
    await interaction.response.defer()
    await handle_llm_stream(interaction, question, attachment)

@bot.tree.command(name="speak", description="Generate speech audio using local Kokoro TTS.")
@app_commands.describe(
    text="The text to convert to speech",
    voice="The speaking voice to use",
    speed="Speech speed modifier (0.5 to 2.0)"
)
@app_commands.choices(voice=[
    app_commands.Choice(name="af_heart (US Female Default)", value="af_heart"),
    app_commands.Choice(name="af_bella (US Female)", value="af_bella"),
    app_commands.Choice(name="bf_emma (UK Female)", value="bf_emma"),
    app_commands.Choice(name="bf_clara (UK Female)", value="bf_clara"),
    app_commands.Choice(name="am_adam (US Male)", value="am_adam"),
    app_commands.Choice(name="am_michael (US Male)", value="am_michael"),
    app_commands.Choice(name="pm_alex (US Male)", value="pm_alex"),
])
async def speak_slash(
    interaction: discord.Interaction,
    text: str,
    voice: app_commands.Choice[str] = None,
    speed: float = 1.0
):
    await interaction.response.defer()
    voice_val = voice.value if voice else "af_heart"
    try:
        audio_data = await tts.generate_tts(text, voice_val, speed)
        await tts.play_tts_in_voice(bot, interaction, audio_data)
    except Exception as e:
        await interaction.followup.send(f"Sorry, I encountered an error: {str(e)[:1900]}")

@bot.tree.command(name="clear", description="Clear conversation history for this channel.")
async def clear_slash(interaction: discord.Interaction):
    llm.llm_manager.clear_history(interaction.channel_id)
    await interaction.response.send_message("🧹 Conversation history for this channel has been cleared!")

@bot.tree.command(name="provider", description="Set the LLM provider for this channel.")
@app_commands.describe(name="The provider to use (gemini, openai, ollama)")
@app_commands.choices(name=[
    app_commands.Choice(name="Gemini (Google)", value="gemini"),
    app_commands.Choice(name="OpenAI", value="openai"),
    app_commands.Choice(name="Ollama (Local)", value="ollama")
])
async def provider_slash(interaction: discord.Interaction, name: app_commands.Choice[str]):
    llm.llm_manager.set_provider(interaction.channel_id, name.value)
    await interaction.response.send_message(f"✅ LLM provider for this channel set to **{name.name}**.")

@bot.tree.command(name="model", description="Set the model name for this channel.")
@app_commands.describe(model_name="The model identifier (e.g. gpt-4o, llama3.1)")
async def model_slash(interaction: discord.Interaction, model_name: str):
    llm.llm_manager.set_model(interaction.channel_id, model_name)
    provider = llm.llm_manager.get_provider(interaction.channel_id)
    await interaction.response.send_message(f"✅ Model for this channel (provider: {provider}) set to **{model_name}**.")

@bot.tree.command(name="leave", description="Disconnect the bot from the voice channel.")
async def leave_slash(interaction: discord.Interaction):
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    if voice_client and voice_client.is_connected():
        await tts.stop_voice_playback(interaction.guild.id, voice_client)
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Disconnected from the voice channel!")
    else:
        await interaction.response.send_message("I am not connected to any voice channel.", ephemeral=True)

# --- event listeners ---

@bot.event
async def on_message(message):
    # Don't let the bot reply to itself
    if message.author == bot.user:
        return
        
    # If the user types '/leave' as a plain text message, handle it as a leave command
    if message.content.strip().lower() == '/leave':
        voice_client = discord.utils.get(bot.voice_clients, guild=message.guild)
        if voice_client and voice_client.is_connected():
            await tts.stop_voice_playback(message.guild.id, voice_client)
            await voice_client.disconnect()
            await message.reply("👋 Disconnected from the voice channel!")
        else:
            await message.reply("I am not connected to any voice channel.")
        return
        
    # If the bot is mentioned, respond to the message
    if bot.user in message.mentions:
        # Clean mention tags from prompt
        question = message.content.replace(f'<@{bot.user.id}>', '').strip()
        question = question.replace(f'<@!{bot.user.id}>', '').strip()
        
        # Check for image attachments in the mention
        attachment = None
        if message.attachments:
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    attachment = att
                    break
                    
        if question or attachment:
            try:
                ctx = await bot.get_context(message)
                await handle_llm_stream(ctx, question, attachment)
            except Exception as e:
                logger.error(f"Error in on_message llm stream: {e}")
        else:
            await message.reply("How can I help you today? Ask a question, upload an image, or use `/ask`.")
            
    # Process commands normally
    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)
