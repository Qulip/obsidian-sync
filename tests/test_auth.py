from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from fastapi.security import HTTPAuthorizationCredentials

from obsidian_sync.core.auth import require_bearer_token
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.repositories.tokens import _utcnow_naive


class BearerAuthTests(IsolatedAsyncioTestCase):
    async def test_admin_token_is_not_accepted_as_api_token(self) -> None:
        credentials = HTTPAuthorizationCredentials(
            scheme='Bearer',
            credentials='admin-secret',
        )

        with patch('obsidian_sync.core.auth.TokenRepository') as repository_class:
            repository = Mock()
            repository.find_by_hash = AsyncMock(return_value=None)
            repository_class.return_value = repository

            with self.assertRaises(AppError) as raised:
                await require_bearer_token(credentials, Mock())

        self.assertEqual(raised.exception.code, ErrorCode.UNAUTHORIZED)
        repository.find_by_hash.assert_awaited_once()

    def test_token_repository_uses_naive_utc_timestamp(self) -> None:
        self.assertIsNone(_utcnow_naive().tzinfo)
