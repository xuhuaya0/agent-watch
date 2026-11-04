"""Bounded JSON RPC transport for an Antigravity-managed loopback context.

Only verified native methods are available; callers enforce action scope and
authorization. This transport never searches for credentials or launches an engine.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from .broker import ControlError
from .worker import NoRedirect


class NativeRpc:
    READ_METHODS = frozenset({'GetConversationMetadata', 'GetCascadeTrajectory', 'ReadProject',
                              'FetchAdminControls', 'GetServerConfiguration', 'GetMendelFlags'})
    WRITE_METHODS = frozenset({'HandleCascadeUserInteraction', 'UpdateProject', 'StartCascade', 'SendUserCascadeMessage'})

    def __init__(self, environ, opener=None):
        address = environ.get('ANTIGRAVITY_LS_ADDRESS', '')
        token = environ.get('ANTIGRAVITY_CSRF_TOKEN', '')
        if not isinstance(address, str) or not isinstance(token, str):
            raise ControlError('antigravity_sidecar_required')
        try:
            parsed = urllib.parse.urlsplit('//'+address)
            port = parsed.port
        except ValueError:
            raise ControlError('antigravity_sidecar_required') from None
        if (parsed.hostname not in ('localhost','127.0.0.1','::1') or not port or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or
            any(c.isspace() or ord(c)<32 for c in address) or
            not token or any(c in token for c in '\r\n\x00')):
            raise ControlError('antigravity_sidecar_required')
        # Resolve localhost to the loopback literal, not through an ambient proxy
        # or DNS search domain. The managed RPC port is separate from the UI port.
        host = '[::1]' if parsed.hostname=='::1' else '127.0.0.1'
        self.base = f'http://{host}:{port}/exa.language_server_pb.LanguageServerService/'
        self.token = token
        self.client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, method, body):
        if method not in self.READ_METHODS | self.WRITE_METHODS:
            raise ControlError('native_action_unsupported')
        write = method in self.WRITE_METHODS
        request = urllib.request.Request(self.base+method, data=json.dumps(body).encode(), method='POST',
            headers={'Content-Type':'application/json', 'X-Codeium-Csrf-Token':self.token,
                     'Connect-Protocol-Version':'1'})
        try:
            with self.client.open(request, timeout=8) as response:
                raw = response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:
                    raise ValueError('oversized native response')
                result = json.loads(raw)
                if not isinstance(result,dict) or 'error' in result or 'code' in result:
                    raise ValueError('invalid native response')
                return result
        except (OSError, ValueError) as error:
            # Record only protocol diagnostics locally, never the native body,
            # user message, token, or credentials in exception representations.
            logging.getLogger(__name__).warning('Antigravity RPC %s failed: %s HTTP %s',
                method, type(error).__name__, getattr(error, 'code', '-'))
            if write:
                # A native side effect may have happened before the connection
                # failed. Never retry, or fall back to a second transport.
                return None
            raise ControlError('native_session_unavailable') from None
