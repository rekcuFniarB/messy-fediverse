import syslog
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied #, BadRequest
from .controller import fediverse_factory, root_json, request_user_is_staff, ActivityResponse
from .models import Activity
from django.conf import settings
from django.urls import resolve, reverse
from OpenSSL import crypto
from base64 import b64decode
from .urls import app_name
import re
import sys
from datetime import datetime
from django.utils.decorators import sync_and_async_middleware
import aiohttp

def stderrlog(*msg):
    if settings.DEBUG:
        print(*msg, file=sys.stderr, flush=True)

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
        stderrlog('RESPONSE BODY:', response.content.decode(response.charset, errors='replace')[:256].replace('\n', ''), '...')
        return response

class SysLog:
    '''
    Requests logger
    '''
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        
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
        self.timeregex = re.compile(b'<time [^>]*datetime="(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\+?[^"\s]*)"')
        self.titleregex = re.compile(b'<title>([^<>]+?)</title>')
    def __call__(self, request):
        ## Before view
        response = self.get_response(request)
        ## After view
        timestring = '1970-01-01T00:00:00+00:00'
        request_accept = request.headers.get('accept', '')
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
                request_accept.startswith('application/activity+json') and
                request.resolver_match and request.resolver_match.app_name != app_name):
            
            if home:
                ## Show user info
                response = root_json(request)
            else:
                proto = 'http'
                if request.is_secure():
                    proto = 'https'
                
                title = f'{proto}://{request.site.domain}{request.path}'
                
                activity = Activity.objects.filter(object_uri=title, activity_type='CRE', incoming=False).first()
                if activity:
                    activity = activity.get_dict();
                    if 'object' in activity and type(activity['object']) is dict:
                        activity['object']['@context'] = activity.get('@context')
                        return ActivityResponse(activity['object'], request)
                
                ## making status
                timesearch = self.timeregex.search(response.content)
                if timesearch:
                    timestring = timesearch.group(1).decode('utf-8', errors='replace')
                timestring = datetime.utcfromtimestamp(datetime.fromisoformat(timestring).timestamp()).isoformat() + 'Z'
                
                titlesearch = self.titleregex.search(response.content)
                if titlesearch:
                    title = titlesearch.group(1).decode('utf-8', errors='replace')
                
                context_path = reverse('messy-fediverse:dumb', kwargs={'rpath': f'context/{request.path.strip("/")}'})
                
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
                    'context': f'{proto}://{request.site.domain}{context_path}',
                    'conversation': f'{proto}://{request.site.domain}{context_path}',
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
                response = ActivityResponse(data, request)
        
        return response

@sync_and_async_middleware
class VerifySignature:
    '''
    Verifying signature
    '''
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        ## Calling next middleware or view if no errors above
        return self.get_response(request)
    
    async def process_view(self, request, view_func, view_args, view_kwargs):
        ## Whe check only views which have csrf_exempt because only those views
        ## are for requests from federated instances.
        if (request.method == 'POST' and not await request_user_is_staff(request) and
                getattr(view_func, 'csrf_exempt', False) and request.resolver_match and request.resolver_match.app_name == app_name and
                not settings.MESSY_FEDIVERSE.get('NO_VERIFY_SIGNATURE', False)):
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
            
            actor = None
            fediverse = fediverse_factory(request)
            async with aiohttp.ClientSession() as session:
                try:
                    actor, = await fediverse.gather_http_responses(fediverse.get(signature['keyId'], session=session))
                except BaseException as e:
                    if settings.DEBUG:
                        ## Raise original exception (probably HTTPError)
                        raise e
                    else:
                        raise PermissionDenied(*e.args)
            
            if type(actor) is not dict:
                return self.response_error(request, f'Actor verify failed: {type(actor)} {actor}')
            
            actorKey = actor.get('publicKey', None)
            if not actorKey:
                return self.response_error(request, 'No actor public key.')
            
            if 'id' not in actorKey or actorKey['id'] != signature['keyId']:
                return self.response_error(request, 'Bad actor key ID')
            
            str2sign = []
            
            for h in signature['headers']:
                if h == '(request-target)':
                    v = f'post {request.path}'
                else:
                    v  = request.headers.get(h, '')
                
                str2sign.append(f'{h}: {v}')
            
            str2sign = '\n'.join(str2sign)
            x509 = crypto.X509()
            x509.set_pubkey(crypto.load_publickey(crypto.FILETYPE_PEM, actorKey.get('publicKeyPem')))
            try:
                verifyResult = crypto.verify(x509, b64decode(signature['signature']), str2sign, 'sha256')
                if verifyResult is not None:
                    return self.response_error(request, 'Signature verification failed.')
            except BaseException as e:
                return self.response_error(request, e.args)
        
        ## Continue normal process
        return None
    
    def response_error(self, request, message):
        if request.content_type and 'json' in request.content_type:
            return JsonResponse({
                'success': False,
                'status': 'error',
                'error': message
            }, status=403)
        else:
            raise PermissionDenied(message)
    
    def parse_signature(self, sig_string):
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
