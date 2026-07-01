from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from obsidian_sync.api.routes.health import OllamaHealth
from obsidian_sync.app import create_app
from obsidian_sync.core.config import Settings, get_settings


class HealthTests(TestCase):
    def test_health_uses_admin_token_without_database(self) -> None:
        settings = _settings(
            ollama_base_url='http://ollama.local',
        )

        with patch(
            'obsidian_sync.api.routes.health._check_ollama',
            return_value=OllamaHealth(
                status='unreachable',
                base_url='http://ollama.local',
                model='bge-m3',
            ),
        ):
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with TestClient(app) as client:
                response = client.get(
                    '/health',
                    headers={'Authorization': 'Bearer admin-secret'},
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['data']['status'], 'degraded')
        self.assertFalse(body['data']['database_configured'])

    def test_health_rejects_missing_admin_token(self) -> None:
        settings = _settings()

        app = create_app(settings)
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as client:
            response = client.get('/health')

        self.assertEqual(response.status_code, 401)


def _settings(*, ollama_base_url: str = 'http://localhost:11434') -> Settings:
    return Settings.model_construct(
        app_name='Obsidian Sync',
        app_version='0.1.0',
        environment='test',
        api_token=SecretStr('admin-secret'),
        embedding_model='bge-m3',
        embedding_dimension=1024,
        ollama_base_url=ollama_base_url,
        ollama_timeout_seconds=30.0,
        vault_storage_root='vaults',
        vault_archive_root='archives',
        database_url=None,
    )
