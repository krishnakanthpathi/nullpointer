# Nullpointer Bot

Nullpointer is a multi-LLM and Text-to-Speech (TTS) Discord bot. It supports interacting with various language models (Gemini, OpenAI, Ollama) and includes text-to-speech capabilities via a local Kokoro TTS service.

## Features

- **Multi-LLM Support**: Configurable per-channel LLM provider.
  - **Gemini**: Powered by Google's Gemini 2.5 Flash Lite (default).
  - **OpenAI**: Support for OpenAI models (e.g., GPT-4o-mini).
  - **Ollama**: Connects to a local Ollama instance (defaulting to Qwen 2.5 Coder 7B).
- **Text-to-Speech (TTS)**: Integration with local Kokoro TTS.
  - Generates speech audio and plays it directly in Discord voice channels or sends it as `.wav` attachments.
  - Supports multiple voices (US/UK, male/female).
- **Image Analysis**: Attachment support for multimodal inputs.
- **Dockerized**: Easy containerization and deployment.

## Commands

### Prefix Commands
- `!ask [question]`: Ask the LLM a question (remembers context, attach images to analyze).
- `!speak [voice] [text]`: Speak text in a voice channel or generate a WAV file.
- `!clear`: Clear conversation history for the current channel.
- `!provider [name]`: View or set the current channel's LLM provider.
- `!model [name]`: View or set the model for the current channel.

### Slash Commands
- `/ask [question] [attachment]`: Interactive command to ask questions.
- `/speak [text] [voice] [speed]`: Generate voice audio with custom options.
- `/clear`: Clear conversation history.
- `/provider [name]`: Set the LLM provider.
- `/model [model_name]`: Set the model.

## Setup and Run

1. Clone this repository.
2. Configure `.env` with your Discord token and API keys:
   ```env
   DISCORD_TOKEN=your_discord_token
   GEMINI_API_KEY=your_gemini_api_key
   OPENAI_API_KEY=your_openai_api_key
   OLLAMA_HOST=http://localhost:11434
   OLLAMA_MODEL=qwen2.5-coder:7b
   TTS_URL=http://localhost:8998/tts
   ```
3. Run locally using Python:
   ```bash
   pip install -r requirements.txt
   python bot.py
   ```

## Docker

Build and run the Docker image:
```bash
docker build -t nullpointer-bot .
docker run --env-file .env nullpointer-bot
```
