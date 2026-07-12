import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from obsidian_sync.core.exceptions import AppError, ErrorCode


@dataclass(frozen=True, slots=True)
class OllamaClient:
    base_url: str
    model: str
    timeout_seconds: float

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    async def generate(self, *, model: str, prompt: str) -> str:
        return await asyncio.to_thread(self._generate_sync, model, prompt)

    def _generate_sync(self, model: str, prompt: str) -> str:
        url = f'{self.base_url.rstrip("/")}/api/generate'
        payload = json.dumps(
            {'model': model, 'prompt': prompt, 'stream': False}
        ).encode('utf-8')
        request = Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AppError(
                ErrorCode.GENERATION_FAILED,
                'Ollama generate request failed.',
                status_code=502,
                details={'error': str(exc)},
            ) from exc

        try:
            data = json.loads(raw.decode('utf-8'))
            text_response = data['response']
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(
                ErrorCode.GENERATION_FAILED,
                'Ollama generate response was invalid.',
                status_code=502,
            ) from exc
        if not isinstance(text_response, str):
            raise AppError(
                ErrorCode.GENERATION_FAILED,
                'Ollama generate response was invalid.',
                status_code=502,
            )
        return text_response

    def _embed_sync(self, text: str) -> list[float]:
        url = f'{self.base_url.rstrip("/")}/api/embed'
        payload = json.dumps({'model': self.model, 'input': text}).encode('utf-8')
        request = Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AppError(
                ErrorCode.EMBEDDING_FAILED,
                'Ollama embedding request failed.',
                status_code=502,
                details={'error': str(exc)},
            ) from exc

        try:
            data = json.loads(raw.decode('utf-8'))
            embedding = _extract_embedding(data)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(
                ErrorCode.EMBEDDING_FAILED,
                'Ollama embedding response was invalid.',
                status_code=502,
            ) from exc

        return embedding


def _extract_embedding(data: dict[str, Any]) -> list[float]:
    raw_embedding = data.get('embedding')
    if raw_embedding is None:
        embeddings = data['embeddings']
        raw_embedding = embeddings[0]
    if not isinstance(raw_embedding, list):
        raise TypeError('embedding must be a list')
    return [float(value) for value in raw_embedding]
