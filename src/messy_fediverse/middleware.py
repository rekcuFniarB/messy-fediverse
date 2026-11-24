import syslog
from django.http import HttpRequest, JsonResponse
from django.core.exceptions import PermissionDenied #, BadRequest
from django.conf import settings
from django.urls import resolve, reverse
from hashlib import sha256
from base64 import b64encode, b64decode
import re
import sys
from datetime import datetime
from urllib.parse import urlparse
from django.utils.decorators import sync_and_async_middleware
import aiohttp
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from functools import wraps
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Dict

def stderrlog(*msg):
    if (
        settings.MESSY_FEDIVERSE.get('DEBUG', False)
        or settings.DEBUG
        or 'error' in msg or 'ERROR' in msg
        or 'warning' in msg or 'WARNING' in msg
        or 'LOG' in msg
    ):
        print(*msg, file=sys.stderr, flush=True)

def remove_private_properties(data):
    if isinstance(data, dict):
        return {
            k: remove_private_properties(v)
            for k, v in data.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    if isinstance(data, list):
        return [remove_private_properties(item) for item in data]
    return data

def is_same_site_referer(request):
    '''
    Checks if request's referer domain is the same as requested domain.
    request: Django HTTP Request objec.
    '''
    referer = request.META.get('HTTP_REFERER')
    if not referer:
        return False

    referer_netloc = urlparse(referer).netloc
    ## Requested hostnsme
    current_host = request.get_host()

    return referer_netloc == current_host

def request_is_ajax(request):
    '''
    Check if request is AJAX (if header X-Requested-With is XMLHttpRequest).
    '''
    return request.META.get('HTTP_X_REQUESTED_WITH', '').upper() == 'XMLHTTPREQUEST'

def is_json_request(request):
    '''Check if client wants JSON'''
    accept = request.META.get('HTTP_ACCEPT', '').lower()
    return 'application/' in accept and 'json' in accept

def is_post_json(request):
    '''Check if request posts json'''
    content_type = request.META.get('CONTENT_TYPE', '')
    return 'application/' in content_type and 'json' in content_type

@sync_to_async
def request_user_is_staff(request):
    ## We can't just use request.user.is_staff from async views
    ## due to lazy executions, so we get errors.
    return request.user.is_staff

@sync_to_async
def request_user_is_authenticated(request):
    ## We can't just use request.user.is_staff from async views
    ## due to lazy executions, so we get errors.
    return request.user.is_authenticated

@sync_to_async
def get_request_user_id(request):
    '''
    Gets user ID from request.
    request: HttpRequest
    returns django User object or None
    '''
    
    if hasattr(request, "user") and hasattr(request.user, "id"):
        return request.user.id
    return None

def get_request_headers_dict(request: HttpRequest) -> Dict[str, str]:
    return {
        key: value
        for key, value in request.META.items()
        if (key.startswith("HTTP_") or key in ("CONTENT_TYPE", "CONTENT_LENGTH"))
        and isinstance(value, str)
    }

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import AsyncRequestFactory, RequestFactory
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

@dataclass
class FrozenRequest:
    '''
    Used for serializing and restoring request objects.
    Useful for background workers.
    '''
    method: str
    scheme: str
    path: str
    body: str
    content_type: str
    headers: Dict[str, str] = field(default_factory=dict)
    user_id: Optional[Any] = None
    site_id: Optional[Any] = None
    
    @staticmethod
    def get_request_site_id(request: HttpRequest) -> Optional[int]:
        if not apps.is_installed("django.contrib.sites"):
            return None

        site = getattr(request, "site", None)
        if site is not None and getattr(site, "id", None) is not None:
            return int(site.id)

        try:
            from django.contrib.sites.shortcuts import get_current_site

            current_site = get_current_site(request)
            return int(current_site.id) if getattr(current_site, "id", None) is not None else None
        except Exception:
            return None
    
    @classmethod
    async def from_request(cls, request: HttpRequest, save_body=False) -> "FrozenRequest":
        '''
        Async factory, makes FrozenRequest object from request.
        request: HttpRequest
        save_body: ignore body data.
        Returns FrozenObject
        '''
        
        user_id = await get_request_user_id(request)
        site_id = await sync_to_async(cls.get_request_site_id)(request)
        ## Any value is requred otherwise content_type will be empty
        body = 'null'
        if save_body:
            body = request.body.decode('utf-8', errors='ignore')
        
        return cls(
            method=request.method,
            path=request.get_full_path(),
            scheme=request.scheme or 'https',
            body=body,
            content_type=request.content_type,
            headers=get_request_headers_dict(request),
            user_id=user_id,
            site_id=site_id,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        '''Returns a JSON-serializable dict.'''
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FrozenRequest":
        '''Rebuilds the FrozenRequest wrapper from a dict.'''
        return cls(
            method=data.get('method', 'GET'),
            path=data.get('path', '/'),
            scheme=data.get('scheme', 'https'),
            body=data.get('body', ''),
            content_type=data.get('content_type', ''),
            headers=data.get('headers', {}),
            user_id=data.get('user_id'),
            site_id=data.get('site_id')
        )
    
    def to_request(self) -> 'WSGIRequest':
        '''
        Reconstructs a synchronous WSGIRequest object.
        '''
        request_factory = RequestFactory()
        factory_method = getattr(request_factory, self.method.lower())
        User = get_user_model()

        request = factory_method(
            path=self.path,
            data=self.body,
            content_type=self.content_type,
            secure=(self.scheme == 'https'),
            **self.headers,
        )

        if self.user_id is not None:
            request.user = User.objects.get(id=self.user_id)
        else:
            request.user = AnonymousUser()
        
        if self.site_id is not None and apps.is_installed('django.contrib.sites'):
            from django.contrib.sites.models import Site

            try:
                request.site = Site.objects.get(pk=self.site_id)
            except ObjectDoesNotExist:
                pass

        return request
    
    async def ato_request(self) -> 'ASGIRequest':
        '''
        Reconstructs an async ASGIRequest for async workers.
        '''
        factory = AsyncRequestFactory()
        User = get_user_model()
        
        ## Extracting the host cleanly so AsyncRequestFactory uses it as SERVER_NAME
        ## instead of creating a duplicate 'testserver' host header
        host = self.headers.get('HTTP_HOST', 'testserver.internal')
        if ":" in host:
            server_name, server_port = host.split(":", 1)
        else:
            server_name, server_port = host, ("443" if self.scheme == "https" else "80")
        
        # factory.defaults['SERVER_NAME'] = server_name
        
        request = factory.generic(**{
            **{
                (k[5:] if k.lower().startswith('http_') else k): v
                for k, v in self.headers.items()
                if k.lower() not in (
                    'content_type', 'content_length',
                    'http_content_type', 'http_content_length',
                    'server_name', 'http_server_name'
                )
            },
            'method': self.method,
            'path': self.path,
            ## Otherwise content_type will be empty
            'data': self.body or 'null',
            'content_type': self.content_type,
            'secure': (self.scheme == 'https'),
            ## We stripped the body but we lose original content length,
            ## which is calculated by this factory on its own.
            ## So, we store original value here so that we can
            ## reuse it later, for example in signature verification.
            'x_original_content_length': self.headers.get('CONTENT_LENGTH', 0),
        })
        
        ## Otherwise request.headers.get('host') returns 'testserver'
        ## with our hostname appended
        request.META['HTTP_HOST'] = server_name
        
        # ASGIRequest in Django 4.1 reads request.scheme from self.scope['scheme']
        if hasattr(request, 'scope'):
            request.scope['scheme'] = self.scheme
        
        if self.user_id is not None:
            request.user = await User.objects.aget(id=self.user_id)
        else:
            request.user = AnonymousUser()
        
        if self.site_id is not None and apps.is_installed('django.contrib.sites'):
            from django.contrib.sites.models import Site

            try:
                request.site = await Site.objects.aget(pk=self.site_id)
            except ObjectDoesNotExist:
                pass

        return request

    ## Usage:
    ##   Saving:
    ##     serialized = await FrozenRequest.from_request(request)
    ##     task_record = await MyTaskModel.objects.acreate(request_data=serialized.to_dict())
    ##   Restoring:
    ##     task_record = await MyTaskModel.objects.aget(id=task_id)
    ##     serialized = FrozenRequest.from_dict(task_record.request_data)
    ##     live_like_request = await serialized.ato_request()
    ##     await my_downstream_handler(live_like_request)


class StdErrLogAllRequests:
    '''
    Log all requests to stderr
    '''
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        ## before view
        response = self.get_response(request)
        ## after view
        stderrlog(request.method, request.path, request.GET.__str__())
        if request.method == 'POST':
            stderrlog('POST DATA:', request.POST.__str__())
        stderrlog('REQUEST META:', request.META.__str__())
        stderrlog('RESPONSE:', response.__str__())
        stderrlog('RESPONSE BODY:', response.content.decode(response.charset, errors='replace')[:512].replace('\n', ''), '...')
        return response

class SysLog:
    '''
    Requests logger
    '''
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        from .urls import app_name
        
        ## Before view code
        response = self.get_response(request)
        ## After view code
        
        if request.resolver_match and request.resolver_match.app_name == app_name and request.method == 'POST':
            syslog.syslog(syslog.LOG_INFO, f'MESSY SOCIAL {request.method}: {request.path}')
            syslog.syslog(syslog.LOG_INFO, 'GET: ' + request.GET.__str__())
            if request.method == 'POST':
                syslog.syslog(syslog.LOG_INFO, 'POST: ' + request.POST.__str__())
            
            syslog.syslog(syslog.LOG_INFO, 'META: ' + request.META.__str__())
            syslog.syslog(syslog.LOG_INFO, 'Response: ' + response.__str__())
            syslog.syslog(syslog.LOG_INFO, response.content.decode(response.charset, errors='replace')[:256])
        
        return response

class WrapIntoStatus:
    '''
    Wraps any valid request into activity status
    '''
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeregex = re.compile(rb'<time [^>]*datetime="(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\+?[^"\s]*)"')
        self.titleregex = re.compile(rb'<title>([^<>]+?)</title>')
    def __call__(self, request):
        from .models import Activity
        from .controller import fediverse_factory, root_json, ActivityResponse
        from .urls import app_name
        
        ## Before view
        response = self.get_response(request)
        ## After view
        timestring = '1970-01-01T00:00:00+00:00'
        path = request.path
        lang_code = getattr(request, 'LANGUAGE_CODE', None)
        ## Getting rid of lang code from path
        if lang_code:
            if f'/{lang_code}' in path:
                path = path.replace(f'/{lang_code}', '', 1)
            else:
                
                ## Get first part (no lang subcode)
                lang_code = lang_code.split('-')[0]
                if f'/{lang_code}' in path:
                    path = path.replace(f'/{lang_code}', '', 1)
        
        home = settings.MESSY_FEDIVERSE.get('HOME', None)
        home = home and (path == home or request.path == home)
        
        if (request.method == 'GET' and response.status_code == 200 and
                is_json_request(request) and
                request.resolver_match and request.resolver_match.app_name != app_name):
            
            if home:
                ## Show user info
                response = async_to_sync(root_json)(request)
            else:
                proto = 'http'
                if request.is_secure():
                    proto = 'https'
                
                title = f'{proto}://{request.site.domain}{request.path}'
                
                activity = Activity.objects.filter(object_uri=title, activity_type='CRE', incoming=False).first()
                if activity:
                    activity = activity.get_dict()
                    if 'object' in activity and type(activity['object']) is dict:
                        activity['object']['@context'] = activity.get('@context')
                        if 'replies' not in activity['object']:
                            activity['object']['replies'] = f'{proto}://{request.site.domain}{reverse("messy-fediverse:replies", kwargs={"rpath": request.path.strip("/")})}'
                        return ActivityResponse(activity['object'], request)
                
                ## making status
                timesearch = self.timeregex.search(response.content)
                if timesearch:
                    timestring = timesearch.group(1).decode('utf-8', errors='replace')
                timestring = datetime.utcfromtimestamp(datetime.fromisoformat(timestring).timestamp()).isoformat() + 'Z'
                
                titlesearch = self.titleregex.search(response.content)
                if titlesearch:
                    title = titlesearch.group(1).decode('utf-8', errors='replace')
                
                fediverse = fediverse_factory(request)
                
                data = {
                    '@context': [
                        "https://www.w3.org/ns/activitystreams",
                        #staticurl(request, 'social/litepub.json'),
                        "https://cloudflare-ipfs.com/ipfs/QmUt2rFamEsBxSkUd7DwE7SXr5BVxTQviGMH6Hwj9bKzTE/litepub-0.1.jsonld",
                        {"@language": "und"}
                    ],
                    'id': f'{proto}://{request.site.domain}{request.path}',
                    'type': 'Note',
                    'actor': fediverse.id,
                    'url': f'{proto}://{request.site.domain}{request.path}',
                    "published": timestring,
                    "attributedTo": f'{proto}://{request.site.domain}{reverse("messy-fediverse:root")}',
                    'inReplyTo': None,
                    'content': f'<a href="{proto}://{request.site.domain}{request.path}">{title}</a>',
                    #'source': '',
                    'senstive': None,
                    'summary': None,
                    'to': [
                        'https://www.w3.org/ns/activitystreams#Public',
                        fediverse.followers
                    ],
                    'cc': [],
                    'tag': [],
                    'attachment': [],
                    'replies': f'{proto}://{request.site.domain}{reverse("messy-fediverse:replies", kwargs={"rpath": request.path.strip("/")})}'
                }
                
                # context_path = reverse('messy-fediverse:dumb', kwargs={'rpath': f'context/{request.path.strip("/")}'})
                #data['context'] = data['conversation'] = f'{proto}://{request.site.domain}{context_path}'
                data['context'] = data['conversation'] = data['id']
                
                response = ActivityResponse(data, request)
        
        return response

# @sync_and_async_middleware
class VerifySignature:
    '''
    Verifying signature
    '''
    async_capable = True
    sync_capable = False
    
    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            markcoroutinefunction(self)
    
    async def __call__(self, request):
        ## Calling next middleware or view if no errors above
        return await self.get_response(request)
    
    async def process_view(self, request, view_func, view_args, view_kwargs):
        from .controller import fediverse_factory
        from .urls import app_name
        
        ## Whe check only views which have csrf_exempt because only those views
        ## are for requests from federated instances.
        if (
            request.method == 'POST' and not await request_user_is_staff(request)
            and getattr(view_func, 'csrf_exempt', False) and request.resolver_match
            and request.resolver_match.app_name == app_name
            and not settings.MESSY_FEDIVERSE.get('NO_VERIFY_SIGNATURE', False)
        ):
            digest = request.headers.get('digest')
            if not digest:
                return self.response_error(request, 'Digest required')
            
            digest = digest.replace('SHA-256=', '').replace('sha-256=', '')
            
            signature_string = request.headers.get('signature', None)
            if not signature_string:
                return self.response_error(request, 'Signature required')
            
            signature = self.parse_signature(signature_string)
            
            for k in ('keyId', 'headers', 'signature'):
                if k not in signature:
                    return self.response_error(request, f'Bad signature, {k} missing')
            
            if '(request-target)' not in signature['headers']:
                return self.response_error(request, 'Signature requires request target.')
            
            if signature.get('algorithm', 'rsa-sha256') not in ('sha256', 'rsa-sha256'):
                return self.response_error(request, 'Unsupported signature algorithm')
            
            ## Checking digest
            ## We validate it after above checks because those are faster
            body_digest = b64encode(sha256(request.body).digest()).decode('utf-8')
            if digest != body_digest:
                return self.response_error(request, f'Bad digest, {digest} != {body_digest}')
        
        ## Continue normal process
        return None
    
    @staticmethod
    def response_error(request, message):
        error = message
        if hasattr(message, 'args'):
            message = error.args
        
        if is_post_json(request):
            stderrlog('DEBUG', 'RESPONSE ERROR:', error, 'REQUEST:', request, request.META)
            return JsonResponse({
                'success': False,
                'status': 'error',
                'error': message
            }, status=403)
        else:
            raise PermissionDenied(message)
    
    @classmethod
    def parse_signature(cls, sig_string: str) -> dict:
        '''
        Http signature parser.
        sig_string: value from signature header like
            Signature: keyId="https://example.social",algorithm="rsa-sha256",headers="(request-target) host date digest",signature="Y2FiYW...IxNGRiZDk4ZA=="
        Returns dict
        '''
        kv_strings = [ x.strip() for x in sig_string.split(',') ]
        signature = {}
        for kvs in sig_string.split(','):
            ## kvs is 'key="value"'
            kv = kvs.strip().split('=', 1)
            if len(kv) < 2:
                kv.append('')
            signature[kv[0]] = kv[1].strip(' "')
        if 'headers' in signature:
            headers = []
            for h in signature['headers'].split(' '):
                h = h.strip()
                if h:
                    headers.append(h)
            signature['headers'] = headers
        
        return signature
    
    @classmethod
    async def verify(cls, request, signature: str = ''):
        '''
        Verifies http signature.
        request: Django HttpRequest object
        signature: string from Signature http header. In case of empty will be taken from the request.
        Returns True or raises an exception.
        '''
        from .controller import fediverse_factory
        
        if not signature:
            signature = request.headers.get('signature', None)
            if not signature:
                raise BaseException('Signature required')
            
            signature = cls.parse_signature(signature)
        
        request_method = request.method.lower()
        actor = None
        fediverse = fediverse_factory(request)
        actor = await fediverse.aget(signature['keyId'])
        ## Commented out, just raise as is
        # try:
        #     actor = await fediverse.aget(signature['keyId'])
        # except BaseException as e:
        #     if settings.DEBUG:
        #         ## Raise original exception (probably HTTPError)
        #         raise e
        #     else:
        #         raise PermissionDenied(*e.args)
        
        if type(actor) is not dict:
            raise BaseException(f'Actor verify failed: {type(actor)} {actor}')
            # return self.response_error(request, f'Actor verify failed: {type(actor)} {actor}')
        
        actorKey = actor.get('publicKey', None)
        if not actorKey:
            raise BaseException(f'No public key found {signature["keyId"]}')
            # return self.response_error(request, 'No actor public key.')
        
        if 'id' not in actorKey or actorKey['id'] != signature['keyId']:
            raise BaseException(f'Actor key compare failed: {actorKey["id"]} vs {signature["keyId"]}')
            # return self.response_error(request, 'Bad actor key ID')
        
        verify_errors = []
        try_paths = []
        if request.META.get('QUERY_STRING'):
            try_paths.append(request.path + '?' + request.META.get('QUERY_STRING'))
        try_paths.append(request.path)
        
        ## Trying to verify signature for path with query string and without
        ## In the past path without query string was proper signature
        ## but mastodon began to use query string for signatures at some time
        for path in try_paths:
            str2sign = []
        
            for h in signature['headers']:
                if h == '(request-target)':
                    v = f'{request_method} {path}'
                else:
                    ## Some headers we store with x-original- prefix,
                    ## at least content-lenght, because we process it
                    ## in background worker and original body is stripped
                    ## to save space.
                    v  = request.headers.get(f'x-original-{h}', '') or request.headers.get(h, '')
                
                str2sign.append(f'{h}: {v}')
            
            str2sign = '\n'.join(str2sign)
            
            try:
                verifyResult = fediverse.crypt_verify(str2sign, signature['signature'], actorKey.get('publicKeyPem'))
                if verifyResult is None:
                    ## Signature check successful
                    verify_errors.clear()
                    break
            except BaseException as e:
                if not len(e.args):
                    e.args = (str2sign, signature['signature'], actorKey.get('publicKeyPem'), request.META.get('HTTP_REMOTE_ADDR'), request.headers.get('user-agent'))
                verify_errors.append(e)
        
        if len(verify_errors):
            error_text = ', '.join(map(repr, verify_errors))
            # return self.response_error(request, f'Signature verification failed: {error_text}')
            raise BaseException(f'Signature verification failed: {error_text}')
        
        return True
