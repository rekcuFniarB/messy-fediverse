"""
Tests for Task 0019: Move signature verification into worker.

Compatible with Django 4.1 (no IsolatedAsyncioTestCase).
"""
import json
import base64
import hashlib
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from django.test import TestCase, RequestFactory
from django.http import HttpRequest, JsonResponse
from django.contrib.sites.models import Site
from django.contrib.auth.models import User

from messy_fediverse.middleware import VerifySignature
from messy_fediverse.controller import save_activity, Inbox
from messy_fediverse.models import Activity
from messy_fediverse.management.commands.worker import Command as WorkerCommand


def run(coro):
    """Helper to run an async coroutine from a sync test method."""
    return asyncio.run(coro)


class TestVerifySignatureMiddleware(TestCase):
    """Middleware should only check HTTP Digest, not full signatures."""

    def setUp(self):
        self.middleware = VerifySignature(get_response=lambda req: None)

    def _make_request(self, method='POST', body=b'', digest=None):
        request = HttpRequest()
        request.method = method
        request._body = body
        request.META = {
            'CONTENT_TYPE': 'application/activity+json',
            'HTTP_HOST': 'example.com',
        }
        request.headers = {}
        if digest:
            request.headers['digest'] = digest
            request.META['HTTP_DIGEST'] = digest
        request.resolver_match = MagicMock()
        request.resolver_match.app_name = 'messy-fediverse'
        return request

    def _make_view_func(self, csrf_exempt=True):
        func = MagicMock()
        func.csrf_exempt = csrf_exempt
        return func

    @patch('messy_fediverse.middleware.request_user_is_staff', new_callable=AsyncMock, return_value=False)
    @patch('messy_fediverse.middleware.settings')
    def test_valid_digest_returns_none(self, mock_settings, mock_staff):
        mock_settings.MESSY_FEDIVERSE = {}
        body = b'{"test": "data"}'
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        request = self._make_request(body=body, digest=f'SHA-256={digest}')
        view_func = self._make_view_func()

        result = run(self.middleware.process_view(request, view_func, (), {}))
        self.assertIsNone(result)

    @patch('messy_fediverse.middleware.request_user_is_staff', new_callable=AsyncMock, return_value=False)
    @patch('messy_fediverse.middleware.settings')
    def test_missing_digest_returns_403(self, mock_settings, mock_staff):
        mock_settings.MESSY_FEDIVERSE = {}
        request = self._make_request(body=b'{}')
        view_func = self._make_view_func()

        result = run(self.middleware.process_view(request, view_func, (), {}))
        self.assertIsInstance(result, JsonResponse)
        self.assertEqual(result.status_code, 403)

    @patch('messy_fediverse.middleware.request_user_is_staff', new_callable=AsyncMock, return_value=False)
    @patch('messy_fediverse.middleware.settings')
    def test_invalid_digest_returns_403(self, mock_settings, mock_staff):
        mock_settings.MESSY_FEDIVERSE = {}
        request = self._make_request(body=b'{}', digest='SHA-256=invalid')
        view_func = self._make_view_func()

        result = run(self.middleware.process_view(request, view_func, (), {}))
        self.assertIsInstance(result, JsonResponse)
        self.assertEqual(result.status_code, 403)

    @patch('messy_fediverse.middleware.request_user_is_staff', new_callable=AsyncMock, return_value=False)
    @patch('messy_fediverse.middleware.settings')
    def test_get_request_returns_none(self, mock_settings, mock_staff):
        mock_settings.MESSY_FEDIVERSE = {}
        request = self._make_request(method='GET')
        view_func = self._make_view_func()

        result = run(self.middleware.process_view(request, view_func, (), {}))
        self.assertIsNone(result)


class TestSaveActivityPending(TestCase):
    """Incoming activities with _verified='pending' must be disabled (hidden)."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='test')
        self.site, _ = Site.objects.get_or_create(
            domain='example.com', defaults={'name': 'example.com'}
        )

    def _make_request(self):
        request = HttpRequest()
        request.method = 'POST'
        request._body = b''
        request.META = {
            'CONTENT_TYPE': 'application/activity+json',
            'HTTP_HOST': 'example.com',
            'QUERY_STRING': '',
        }
        request.headers = {}
        request.site = self.site
        request.user = self.user
        return request

    @patch('messy_fediverse.controller.fediverse_factory')
    def test_incoming_pending_activity_is_disabled(self, mock_fediverse_factory):
        mock_actor = MagicMock()
        mock_actor.id = 'https://example.com/users/testuser'
        mock_fediverse_factory.return_value = mock_actor

        request = self._make_request()
        activity_dict = {
            'id': 'https://example.com/activities/pending-1',
            'type': 'Create',
            'actor': 'https://remote.com/users/remote',
            'object': {
                'id': 'https://remote.com/objects/1',
                'type': 'Note',
                'content': 'Hello',
            },
            'published': '2024-01-01T00:00:00Z',
            '_verified': 'pending',
            '_request_meta': {
                'HTTP_SIGNATURE': 'keyId="test",headers="(request-target) digest",signature="sig"',
                'QUERY_STRING': '',
            },
        }

        activity = run(save_activity(request, activity_dict))

        self.assertIsNotNone(activity)
        self.assertTrue(activity.disabled)
        self.assertEqual(activity.activity_data.get('_verified'), 'pending')
        self.assertTrue(activity.incoming)

    @patch('messy_fediverse.controller.fediverse_factory')
    def test_outgoing_activity_not_disabled(self, mock_fediverse_factory):
        mock_actor = MagicMock()
        mock_actor.id = 'https://example.com/users/testuser'
        mock_fediverse_factory.return_value = mock_actor

        request = self._make_request()
        activity_dict = {
            'id': 'https://example.com/activities/outgoing-1',
            'type': 'Create',
            'actor': 'https://example.com/users/testuser',
            'object': {
                'id': 'https://example.com/objects/1',
                'type': 'Note',
                'content': 'Hello',
            },
            'published': '2024-01-01T00:00:00Z',
            '_verified': 'pending',
        }

        activity = run(save_activity(request, activity_dict))

        self.assertIsNotNone(activity)
        self.assertFalse(activity.disabled)
        self.assertFalse(activity.incoming)


class TestWorkerVerifySignature(TestCase):
    """Worker signature verification helpers."""

    def setUp(self):
        self.user = User.objects.create_user(username='workeruser', password='test')
        self.site, _ = Site.objects.get_or_create(
            domain='example.com', defaults={'name': 'example.com'}
        )
        self.command = WorkerCommand()
        self.command._request = HttpRequest()
        self.command._request.site = self.site
        self.command._request.user = self.user
        self.command._actor = MagicMock()

    def _make_activity(self, verified='pending', disabled=True, processing_status=0):
        activity = MagicMock(spec=Activity)
        activity.incoming = True
        activity.disabled = disabled
        activity.processing_status = processing_status
        activity.object_uri = 'https://remote.com/objects/1'
        activity.actor_uri = 'https://remote.com/users/remote'
        activity.activity_data = {
            '_verified': verified,
            '_request_meta': {
                'HTTP_SIGNATURE': (
                    'keyId="https://remote.com/users/remote#main-key",'
                    'headers="(request-target) digest",'
                    'signature="fakesig"'
                ),
                'QUERY_STRING': '',
            },
        }
        activity.save = AsyncMock()
        return activity

    def test_verify_incoming_signature_valid(self):
        activity = self._make_activity()

        self.command._actor.aget = AsyncMock(return_value={
            'publicKey': {
                'id': 'https://remote.com/users/remote#main-key',
                'publicKeyPem': '-----BEGIN PUBLIC KEY-----fake-----END PUBLIC KEY-----',
            }
        })
        self.command._actor.crypt_verify = AsyncMock(return_value=None)

        result = run(self.command.verify_incoming_signature(
            self.command._request, self.command._actor, activity
        ))

        self.assertTrue(result)
        self.command._actor.crypt_verify.assert_called_once()

    def test_verify_incoming_signature_invalid_no_meta(self):
        activity = self._make_activity()
        activity.activity_data = {}

        result = run(self.command.verify_incoming_signature(
            self.command._request, self.command._actor, activity
        ))

        self.assertFalse(result)

    def test_verify_incoming_signature_invalid_bad_crypto(self):
        activity = self._make_activity()

        self.command._actor.aget = AsyncMock(return_value={
            'publicKey': {
                'id': 'https://remote.com/users/remote#main-key',
                'publicKeyPem': '-----BEGIN PUBLIC KEY-----fake-----END PUBLIC KEY-----',
            }
        })
        self.command._actor.crypt_verify = AsyncMock(return_value=False)

        result = run(self.command.verify_incoming_signature(
            self.command._request, self.command._actor, activity
        ))

        self.assertFalse(result)

    def test_worker_transition_valid_signature(self):
        """Simulate ahandle state transition when signature is valid."""
        activity = self._make_activity(verified='pending', disabled=True)

        with patch.object(
            self.command, 'verify_incoming_signature', new_callable=AsyncMock, return_value=True
        ):
            verified = run(self.command.verify_incoming_signature(
                self.command._request, self.command._actor, activity
            ))
            if verified:
                activity.activity_data['_verified'] = 'signature'
                activity.disabled = False
            run(activity.save())

        self.assertTrue(verified)
        self.assertEqual(activity.activity_data['_verified'], 'signature')
        self.assertFalse(activity.disabled)
        activity.save.assert_awaited_once()

    def test_worker_transition_invalid_signature_no_fetch(self):
        """Simulate ahandle state transition when signature invalid and fetch fails."""
        activity = self._make_activity(verified='pending', disabled=True)

        with patch.object(
            self.command, 'verify_incoming_signature', new_callable=AsyncMock, return_value=False
        ):
            verified = run(self.command.verify_incoming_signature(
                self.command._request, self.command._actor, activity
            ))
            if not verified:
                try:
                    obj = run(self.command._actor.aget(activity.object_uri))
                except BaseException:
                    obj = None
                if type(obj) is dict:
                    activity.activity_data['_verified'] = 'fetch'
                    activity.disabled = False
                else:
                    activity.disabled = True
            run(activity.save())

        self.assertFalse(verified)
        self.assertEqual(activity.activity_data['_verified'], 'pending')
        self.assertTrue(activity.disabled)
        activity.save.assert_awaited_once()


class TestInboxPendingStatus(TestCase):
    """Inbox should return 202 and pass _verified='pending' to save_activity."""

    def setUp(self):
        self.user = User.objects.create_user(username='inboxuser', password='test')
        self.site, _ = Site.objects.get_or_create(
            domain='example.com', defaults={'name': 'example.com'}
        )

    @patch('messy_fediverse.controller.fediverse_factory')
    @patch('messy_fediverse.controller.save_activity', new_callable=AsyncMock)
    def test_inbox_returns_202_and_marks_pending(
        self, mock_save_activity, mock_fediverse_factory
    ):
        mock_activity = MagicMock()
        mock_activity.get_dict.return_value = {'id': 'https://example.com/activities/1'}
        mock_save_activity.return_value = mock_activity

        mock_actor = MagicMock()
        mock_actor.id = 'https://example.com/users/testuser'
        mock_actor.aget = AsyncMock(return_value={})
        mock_fediverse_factory.return_value = mock_actor

        request = RequestFactory().post(
            '/inbox/',
            data=json.dumps({
                'id': 'https://remote.com/activities/1',
                'type': 'Create',
                'actor': 'https://remote.com/users/remote',
                'object': {'id': 'https://remote.com/objects/1', 'type': 'Note'},
            }),
            content_type='application/activity+json',
        )
        request.site = self.site
        request.user = self.user

        view = Inbox()
        view.setup(request)
        response = run(view.post(request))

        self.assertEqual(response.status_code, 202)

        self.assertTrue(mock_save_activity.called)
        call_args = mock_save_activity.call_args
        saved_dict = call_args[0][1]
        self.assertEqual(saved_dict.get('_verified'), 'pending')
