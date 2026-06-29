import discord
from discord import app_commands
from discord.ext import commands
import src.config as config
from src.controllers.bot_controller import BotController

logger = config.logger

# Configure Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True  # Required for joining and speaking in voice channels

bot = commands.Bot(command_prefix='!', intents=intents)
controller = BotController(bot)

@bot.event
async def on_ready():
    await controller.handle_ready()

# --- prefix commands ---

@bot.command(name='ask', help='Ask the bot a question (remembers context, attach image to analyze).')
async def ask(ctx, *, question: str = ""):
    attachment = ctx.message.attachments[0] if ctx.message.attachments else None
    await controller.handle_ask(ctx, question, attachment)

@bot.command(name='speak', help='Generate text-to-speech. Usage: !speak [voice_name] [text]')
async def speak(ctx, *, args: str = ""):
    await controller.handle_speak(ctx, text=args)

@bot.command(name='clear', help='Clear conversation history for this channel.')
async def clear(ctx):
    await controller.handle_clear(ctx)

@bot.command(name="provider", help="Set the LLM provider for this channel (gemini, openai, ollama).")
async def provider_prefix(ctx, name: str = None):
    await controller.handle_provider(ctx, name)

@bot.command(name="model", help="Set the model name for this channel.")
async def model_prefix(ctx, model_name: str = None):
    await controller.handle_model(ctx, model_name)

@bot.command(name="leave", help="Disconnect the bot from the voice channel.")
async def leave(ctx):
    await controller.handle_leave(ctx)

# --- slash commands ---

@bot.tree.command(name="ask", description="Ask the bot a question (remembers context, accepts image attachment).")
@app_commands.describe(question="The question to ask the bot", attachment="An optional image to analyze")
async def ask_slash(interaction: discord.Interaction, question: str, attachment: discord.Attachment = None):
    await interaction.response.defer()
    await controller.handle_ask(interaction, question, attachment)

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
    app_commands.Choice(name="pm_alex (Brazilian Portuguese Male)", value="pm_alex"),
    app_commands.Choice(name="ef_dora (Spanish Female)", value="ef_dora"),
    app_commands.Choice(name="ff_siwis (French Female)", value="ff_siwis"),
    app_commands.Choice(name="hf_alpha (Hindi Female)", value="hf_alpha"),
    app_commands.Choice(name="hf_beta (Hindi Female)", value="hf_beta"),
    app_commands.Choice(name="if_sara (Italian Female)", value="if_sara"),
    app_commands.Choice(name="jf_alpha (Japanese Female)", value="jf_alpha"),
    app_commands.Choice(name="zf_xiaoxiao (Mandarin Chinese Female)", value="zf_xiaoxiao"),
])
async def speak_slash(
    interaction: discord.Interaction,
    text: str,
    voice: app_commands.Choice[str] = None,
    speed: float = 1.0
):
    await interaction.response.defer()
    voice_val = voice.value if voice else "af_heart"
    await controller.handle_speak(interaction, text, voice_val, speed)

@bot.tree.command(name="clear", description="Clear conversation history for this channel.")
async def clear_slash(interaction: discord.Interaction):
    await controller.handle_clear(interaction)

@bot.tree.command(name="provider", description="Set the LLM provider for this channel.")
@app_commands.describe(name="The provider to use (gemini, openai, ollama)")
@app_commands.choices(name=[
    app_commands.Choice(name="Gemini (Google)", value="gemini"),
    app_commands.Choice(name="OpenAI", value="openai"),
    app_commands.Choice(name="Ollama (Local)", value="ollama")
])
async def provider_slash(interaction: discord.Interaction, name: app_commands.Choice[str]):
    await controller.handle_provider(interaction, name.value)

@bot.tree.command(name="model", description="Set the model name for this channel.")
@app_commands.describe(model_name="The model identifier (e.g. gpt-4o, llama3.1)")
async def model_slash(interaction: discord.Interaction, model_name: str):
    await controller.handle_model(interaction, model_name)

@bot.tree.command(name="leave", description="Disconnect the bot from the voice channel.")
async def leave_slash(interaction: discord.Interaction):
    await controller.handle_leave(interaction)

# --- event listeners ---

@bot.event
async def on_message(message):
    await controller.handle_message(message)

if __name__ == "__main__":
    bot.run(config.DISCORD_TOKEN)
