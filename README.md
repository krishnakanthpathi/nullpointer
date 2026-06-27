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
- `!ask [question]`: Ask the LLM a question (remembers context, attach images to analyze). **Automatically joins and speaks the answer if you are in a voice channel.**
- `!speak [voice] [text]`: Speak text in a voice channel or generate a WAV file.
- `!leave`: Disconnect the bot from the voice channel.
- `!clear`: Clear conversation history for the current channel.
- `!provider [name]`: View or set the current channel's LLM provider.
- `!model [name]`: View or set the model for the current channel.

### Slash Commands
- `/ask [question] [attachment]`: Interactive command to ask questions. **Automatically joins and speaks the answer if you are in a voice channel.**
- `/speak [text] [voice] [speed]`: Generate voice audio with custom options.
- `/leave`: Disconnect the bot from the voice channel (also supports typing `/leave` as a plain text chat message).
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

---

## System Design & Architecture

Here is the system design of the Nullpointer Bot showing the current execution flow and the proposed real-time streaming audio pipeline.

### 1. Current Architecture (Sequential Flow)

In the current setup, the bot generates the full text response from the LLM before sending it to the Kokoro TTS service. Once the complete audio file is synthesized, it is streamed to the voice channel.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Discord as Discord Server
    participant Bot as Nullpointer Bot
    participant LLM as LLM Provider (Gemini/Ollama)
    participant TTS as Kokoro TTS Service
    participant Voice as Discord Voice Connection

    User->>Discord: Sends message / command
    Discord->>Bot: Dispatches event (message / interaction)
    Bot->>LLM: Requests completion (generate_response)
    LLM-->>Bot: Returns full text answer
    Bot->>Discord: Sends text reply message
    
    alt Bot or User is in Voice Channel
        Bot->>Bot: Cleans markdown & formatting
        Bot->>TTS: POST /tts (full text)
        TTS-->>Bot: Returns synthesized audio bytes (.wav)
        Bot->>Voice: Play audio via FFmpegPCMAudio
        Voice-->>Bot: Playing in voice channel...
    end
```

### 2. Proposed Streaming Architecture (Real-Time Audio Sink)

To implement real-time speech synthesis while the LLM is still generating tokens, we can stream chunks from the LLM, accumulate them into sentences, request audio concurrently, and queue them to a sequential audio playback sink.

```mermaid
flowchart TD
    User([User Prompt]) --> Bot[Discord Bot]
    Bot -->|Stream Request| LLM[LLM Provider]
    LLM -->|Token Chunks| Parser[Sentence Parser]
    
    subgraph Real-Time Voice Pipeline
        Parser -->|Complete Sentence| Buffer[(Sentence Buffer)]
        Buffer -->|Concurrent Request| TTS[Kokoro TTS Service]
        TTS -->|Audio Chunk| Queue[Audio Playback Queue]
        Queue -->|Sequential Stream| Voice[Discord Voice Channel]
    end

    Parser -->|Accumulate Text| TextMsg[Dynamic Text Update]
    TextMsg -->|Edit Message| Discord[Discord Text Channel]
```

#### Detailed Sequence Flow (Streaming)

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Bot as Nullpointer Bot
    participant LLM as LLM Provider
    participant TTS as Kokoro TTS Service
    participant Voice as Discord Voice Channel

    User->>Bot: Sends question (/ask or mention)
    Bot->>LLM: Request Stream (generate_content_stream)
    
    loop Stream Generation
        LLM-->>Bot: Yields token chunks
        Bot->>Bot: Parses tokens into sentences
    end

    rect rgb(230, 240, 255)
        Note over Bot, TTS: Executed concurrently for each complete sentence
        loop Sentence to Speech
            Bot->>TTS: Request TTS (Async POST /tts)
            TTS-->>Bot: Return Audio WAV Bytes
            Bot->>Bot: Add to Sequential Queue
        end
    end

    loop Queue Player
        Bot->>Voice: Play sequential source (FFmpegPCMAudio)
        Voice-->>Bot: Trigger 'after' callback on completion
        Bot->>Bot: Fetch next segment from Queue
    end
```

---

### How to Implement the Streaming Pipeline in Python
To enable the proposed real-time streaming pipeline:
1. **LLM Client Streaming**: Switch client calls from `generate_response()` to `generate_content_stream()` (Gemini) or setting `stream=True` (OpenAI/Ollama).
2. **Regex Sentence Splitting**: Use a regex lookbehind matcher (e.g. `(?<!\bMr)(?<!\bDr)(?<=[.!?])\s+`) to capture ends of sentences from the text buffer without splitting prematurely.
3. **Async HTTP Queue**: Send concurrent requests to the TTS service for completed sentences, saving the audio segments in a thread-safe `asyncio.Queue`.
4. **Discord Audio Playback Lock/Sink**: Implement a custom playback loop that monitors the Discord `VoiceClient.is_playing()` status and utilizes the `after` callback in `voice_client.play(FFmpegPCMAudio(temp_wav), after=...)` to feed the next audio chunk from the queue seamlessly.
