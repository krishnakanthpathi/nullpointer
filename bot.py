import discord
from discord import app_commands
from discord.ext import commands
import src.config as config
import src.services.llm as llm
import src.services.tts as tts

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

# --- prefix commands ---

@bot.command(name='ask', help='Ask the bot a question (remembers context, attach image to analyze).')
async def ask(ctx, *, question: str = ""):
    async with ctx.typing():
        attachment = ctx.message.attachments[0] if ctx.message.attachments else None
        if not question.strip() and not attachment:
            await ctx.send("Please provide a question or attach an image.")
            return
            
        try:
            user_content = await llm.build_user_content(question, attachment)
            answer = await llm.llm_manager.generate_response(ctx.channel.id, user_content)
            
            if len(answer) > 2000:
                chunks = [answer[i:i+1990] for i in range(0, len(answer), 1990)]
                for chunk in chunks:
                    await ctx.send(chunk)
            else:
                await ctx.send(answer)
                
            # If the user is connected to a voice channel, or the bot is already in one, speak the response aloud
            author = ctx.author
            voice_channel = author.voice.channel if (author.voice and author.voice.channel) else None
            voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
            if voice_channel or (voice_client and voice_client.is_connected()):
                import re
                clean_text = re.sub(r'```.*?```', '[code block]', answer, flags=re.DOTALL)
                clean_text = clean_text.replace('*', '').replace('_', '').replace('#', '').strip()
                if clean_text:
                    speech_text = clean_text[:500] + ("..." if len(clean_text) > 500 else "")
                    audio_data = await tts.generate_tts(speech_text)
                    await tts.play_tts_in_voice(bot, ctx, audio_data, suppress_message=True)
        except Exception as e:
            await ctx.send(f"Sorry, I encountered an error: {str(e)[:1900]}")

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
        await voice_client.disconnect()
        await ctx.send("👋 Disconnected from the voice channel!")
    else:
        await ctx.send("I am not connected to any voice channel.")

# --- slash commands ---

@bot.tree.command(name="ask", description="Ask the bot a question (remembers context, accepts image attachment).")
@app_commands.describe(question="The question to ask the bot", attachment="An optional image to analyze")
async def ask_slash(interaction: discord.Interaction, question: str, attachment: discord.Attachment = None):
    await interaction.response.defer()
    try:
        user_content = await llm.build_user_content(question, attachment)
        answer = await llm.llm_manager.generate_response(interaction.channel_id, user_content)
        
        if len(answer) > 2000:
            chunks = [answer[i:i+1990] for i in range(0, len(answer), 1990)]
            await interaction.followup.send(chunks[0])
            for chunk in chunks[1:]:
                await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(answer)
            
        # If the user is connected to a voice channel, or the bot is already in one, speak the response aloud
        author = interaction.user
        voice_channel = author.voice.channel if (author.voice and author.voice.channel) else None
        voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
        if voice_channel or (voice_client and voice_client.is_connected()):
            import re
            clean_text = re.sub(r'```.*?```', '[code block]', answer, flags=re.DOTALL)
            clean_text = clean_text.replace('*', '').replace('_', '').replace('#', '').strip()
            if clean_text:
                speech_text = clean_text[:500] + ("..." if len(clean_text) > 500 else "")
                audio_data = await tts.generate_tts(speech_text)
                await tts.play_tts_in_voice(bot, interaction, audio_data, suppress_message=True)
    except Exception as e:
        await interaction.followup.send(f"Sorry, I encountered an error: {str(e)[:1900]}")

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
            async with message.channel.typing():
                try:
                    user_content = await llm.build_user_content(question, attachment)
                    answer = await llm.llm_manager.generate_response(message.channel.id, user_content)
                    
                    if len(answer) > 2000:
                        chunks = [answer[i:i+1990] for i in range(0, len(answer), 1990)]
                        for chunk in chunks:
                            await message.reply(chunk)
                    else:
                        await message.reply(answer)
                        
                    # If the user is connected to a voice channel, or the bot is already in one, speak the response aloud
                    author = message.author
                    voice_channel = author.voice.channel if (author.voice and author.voice.channel) else None
                    voice_client = discord.utils.get(bot.voice_clients, guild=message.guild)
                    if voice_channel or (voice_client and voice_client.is_connected()):
                        import re
                        clean_text = re.sub(r'```.*?```', '[code block]', answer, flags=re.DOTALL)
                        clean_text = clean_text.replace('*', '').replace('_', '').replace('#', '').strip()
                        if clean_text:
                            speech_text = clean_text[:500] + ("..." if len(clean_text) > 500 else "")
                            audio_data = await tts.generate_tts(speech_text)
                            ctx = await bot.get_context(message)
                            await tts.play_tts_in_voice(bot, ctx, audio_data, suppress_message=True)
                except Exception as e:
                    error_msg = str(e)[:1900]
                    await message.reply(f"Sorry, I encountered an error: {error_msg}")
        else:
            await message.reply("How can I help you today? Ask a question, upload an image, or use `/ask`.")
            
    # Process commands normally
    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)
