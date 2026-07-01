import base64
import asyncio
import discord
from google import genai
from google.genai import types as gemini_types
import httpx
from openai import AsyncOpenAI
import src.config as config

logger = config.logger

# In-memory stores for channel-specific context
conversation_histories = {}
channel_providers = {}
channel_models = {}

class LLMManager:
    def __init__(self):
        self.default_provider = config.LLM_PROVIDER
        self.gemini_model = config.GEMINI_MODEL
        self.openai_model = config.OPENAI_MODEL
        self.ollama_model = config.OLLAMA_MODEL
        self.ollama_host = config.OLLAMA_HOST
        
        # Initialize Gemini Client
        self.gemini_client = None
        if config.GEMINI_API_KEY and not config.GEMINI_API_KEY.startswith("your_"):
            try:
                self.gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
                logger.info("Initialized Gemini client.")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}")
                
        # Initialize OpenAI Client
        self.openai_client = None
        if config.OPENAI_API_KEY and not config.OPENAI_API_KEY.startswith("your_"):
            try:
                self.openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
                logger.info("Initialized OpenAI client.")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")
                
        # Initialize Ollama client
        try:
            self.ollama_client = AsyncOpenAI(
                base_url=f"{self.ollama_host.rstrip('/')}/v1",
                api_key="ollama"
            )
            logger.info(f"Initialized Ollama client pointing to {self.ollama_host}")
        except Exception as e:
            logger.error(f"Failed to initialize Ollama client: {e}")
            
    def get_provider(self, channel_id: int) -> str:
        return channel_providers.get(channel_id, self.default_provider)
        
    def get_model(self, channel_id: int, provider: str) -> str:
        if channel_id in channel_models:
            return channel_models[channel_id]
        if provider == 'gemini':
            return self.gemini_model
        elif provider == 'openai':
            return self.openai_model
        else:
            return self.ollama_model
            
    def set_provider(self, channel_id: int, provider: str):
        channel_providers[channel_id] = provider.lower().strip()
        channel_models.pop(channel_id, None)  # Reset custom model on provider change
        
    def set_model(self, channel_id: int, model_name: str):
        channel_models[channel_id] = model_name.strip()
        
    def clear_history(self, channel_id: int):
        conversation_histories.pop(channel_id, None)
        
    async def generate_response(self, channel_id: int, user_content: dict, provider: str = None) -> str:
        if not provider:
            provider = self.get_provider(channel_id)
        model = self.get_model(channel_id, provider)
        
        logger.info(f"Generating response for channel {channel_id} using {provider} ({model})")
        
        # Get history
        history = conversation_histories.setdefault(channel_id, [])
        history.append(user_content)
        
        # Keep history within limits
        if len(history) > config.MAX_HISTORY:
            prune_count = len(history) - config.MAX_HISTORY
            history[:] = history[prune_count:]
            
        try:
            if provider == 'gemini':
                return await self._generate_gemini(history, model)
            elif provider == 'openai':
                return await self._generate_openai(history, model)
            elif provider == 'ollama':
                return await self._generate_ollama(history, model)
            else:
                raise ValueError(f"Unknown LLM provider: {provider}")
        except Exception as e:
            # Rollback history on failure
            if history and history[-1] == user_content:
                history.pop()
            raise e

    async def generate_response_stream(self, channel_id: int, user_content: dict, provider: str = None):
        if not provider:
            provider = self.get_provider(channel_id)
        model = self.get_model(channel_id, provider)
        
        logger.info(f"Streaming response for channel {channel_id} using {provider} ({model})")
        
        # Get history
        history = conversation_histories.setdefault(channel_id, [])
        history.append(user_content)
        
        # Keep history within limits
        if len(history) > config.MAX_HISTORY:
            prune_count = len(history) - config.MAX_HISTORY
            history[:] = history[prune_count:]
            
        try:
            if provider == 'gemini':
                async for chunk in self._generate_gemini_stream(history, model):
                    yield chunk
            elif provider == 'openai':
                async for chunk in self._generate_openai_stream(history, model):
                    yield chunk
            elif provider == 'ollama':
                async for chunk in self._generate_ollama_stream(history, model):
                    yield chunk
            else:
                raise ValueError(f"Unknown LLM provider: {provider}")
        except Exception as e:
            # Rollback history on failure
            if history and history[-1] == user_content:
                history.pop()
            raise e
            
    async def _generate_gemini(self, history: list, model: str) -> str:
        if not self.gemini_client:
            raise ValueError("Gemini client is not configured. Please set GEMINI_API_KEY in the .env file.")
            
        contents = []
        for msg in history:
            parts = []
            if msg.get("text"):
                parts.append(gemini_types.Part.from_text(text=msg["text"]))
            if msg.get("image_bytes"):
                parts.append(
                    gemini_types.Part.from_bytes(
                        data=msg["image_bytes"],
                        mime_type=msg["mime_type"]
                    )
                )
            contents.append(gemini_types.Content(role=msg["role"], parts=parts))
            
        config_args = gemini_types.GenerateContentConfig(
            system_instruction="You are a helpful coding assistant discord bot named null-pointer."
        )
        
        response = await asyncio.to_thread(
            self.gemini_client.models.generate_content,
            model=model,
            contents=contents,
            config=config_args
        )
        
        model_text = response.text or ""
        history.append({
            "role": "model",
            "text": model_text
        })
        return model_text

    async def _generate_gemini_stream(self, history: list, model: str):
        if not self.gemini_client:
            raise ValueError("Gemini client is not configured. Please set GEMINI_API_KEY in the .env file.")
            
        contents = []
        for msg in history:
            parts = []
            if msg.get("text"):
                parts.append(gemini_types.Part.from_text(text=msg["text"]))
            if msg.get("image_bytes"):
                parts.append(
                    gemini_types.Part.from_bytes(
                        data=msg["image_bytes"],
                        mime_type=msg["mime_type"]
                    )
                )
            contents.append(gemini_types.Content(role=msg["role"], parts=parts))
            
        config_args = gemini_types.GenerateContentConfig(
            system_instruction="You are a helpful coding assistant discord bot named null-pointer."
        )
        
        response_stream = await asyncio.to_thread(
            self.gemini_client.models.generate_content_stream,
            model=model,
            contents=contents,
            config=config_args
        )
        
        full_text = ""
        for chunk in response_stream:
            chunk_text = chunk.text or ""
            full_text += chunk_text
            yield chunk_text
            
        history.append({
            "role": "model",
            "text": full_text
        })

    async def _generate_openai(self, history: list, model: str) -> str:
        if not self.openai_client:
            raise ValueError("OpenAI client is not configured. Please set OPENAI_API_KEY in the .env file.")
            
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant discord bot named null-pointer."}
        ]
        
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            
            if msg.get("image_bytes"):
                base64_image = base64.b64encode(msg["image_bytes"]).decode('utf-8')
                content_list = [
                    {"type": "text", "text": msg.get("text", "")},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{msg['mime_type']};base64,{base64_image}"
                        }
                    }
                ]
                messages.append({"role": role, "content": content_list})
            else:
                messages.append({"role": role, "content": msg.get("text", "")})
                
        response = await self.openai_client.chat.completions.create(
            model=model,
            messages=messages
        )
        
        model_text = response.choices[0].message.content or ""
        history.append({
            "role": "model",
            "text": model_text
        })
        return model_text

    async def _generate_openai_stream(self, history: list, model: str):
        if not self.openai_client:
            raise ValueError("OpenAI client is not configured. Please set OPENAI_API_KEY in the .env file.")
            
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant discord bot named null-pointer."}
        ]
        
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            
            if msg.get("image_bytes"):
                base64_image = base64.b64encode(msg["image_bytes"]).decode('utf-8')
                content_list = [
                    {"type": "text", "text": msg.get("text", "")},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{msg['mime_type']};base64,{base64_image}"
                        }
                    }
                ]
                messages.append({"role": role, "content": content_list})
            else:
                messages.append({"role": role, "content": msg.get("text", "")})
                
        response = await self.openai_client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True
        )
        
        full_text = ""
        async for chunk in response:
            chunk_text = chunk.choices[0].delta.content or ""
            full_text += chunk_text
            yield chunk_text
            
        history.append({
            "role": "model",
            "text": full_text
        })

    async def _generate_ollama(self, history: list, model: str) -> str:
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant discord bot named null-pointer."}
        ]
        
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            
            if msg.get("image_bytes"):
                base64_image = base64.b64encode(msg["image_bytes"]).decode('utf-8')
                content_list = [
                    {"type": "text", "text": msg.get("text", "")},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{msg['mime_type']};base64,{base64_image}"
                        }
                    }
                ]
                messages.append({"role": role, "content": content_list})
            else:
                messages.append({"role": role, "content": msg.get("text", "")})
                
        try:
            response = await self.ollama_client.chat.completions.create(
                model=model,
                messages=messages
            )
            model_text = response.choices[0].message.content or ""
        except httpx.ConnectError:
            raise ConnectionError(f"Failed to connect to local Ollama service at {self.ollama_host}. Is it running?")
            
        history.append({
            "role": "model",
            "text": model_text
        })
        return model_text

    async def _generate_ollama_stream(self, history: list, model: str):
        messages = [
            {"role": "system", "content": "You are a helpful coding assistant discord bot named null-pointer."}
        ]
        
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            
            if msg.get("image_bytes"):
                base64_image = base64.b64encode(msg["image_bytes"]).decode('utf-8')
                content_list = [
                    {"type": "text", "text": msg.get("text", "")},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{msg['mime_type']};base64,{base64_image}"
                        }
                    }
                ]
                messages.append({"role": role, "content": content_list})
            else:
                messages.append({"role": role, "content": msg.get("text", "")})
                
        try:
            response = await self.ollama_client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True
            )
            full_text = ""
            async for chunk in response:
                chunk_text = chunk.choices[0].delta.content or ""
                full_text += chunk_text
                yield chunk_text
                
            history.append({
                "role": "model",
                "text": full_text
            })
        except httpx.ConnectError:
            raise ConnectionError(f"Failed to connect to local Ollama service at {self.ollama_host}. Is it running?")

llm_manager = LLMManager()

async def build_user_content(
    text: str, 
    attachment: discord.Attachment = None
) -> dict:
    """Builds a provider-agnostic dictionary format from user 
    text and an optional image attachment."""
    content = {
        "role": "user",
        "text": text,
        "image_bytes": None,
        "mime_type": None
    }
    
    if attachment:
        content_type = attachment.content_type or ""
        if content_type.startswith("image/"):
            try:
                img_bytes = await attachment.read()
                content["image_bytes"] = img_bytes
                content["mime_type"] = content_type
                logger.info(f"Loaded image attachment: {attachment.filename} ({content_type})")
            except Exception as e:
                logger.error(f"Failed to read attachment: {e}")
                
    return content
