from django.conf import settings
from django.shortcuts import render, redirect
from django.http import JsonResponse, Http404
from django.urls import reverse
from django.core.exceptions import PermissionDenied, BadRequest
from django.core.cache import cache
from django.core.mail import mail_admins
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.templatetags.static import static as _staticurl
from .forms import InteractForm
from .fediverse import Fediverse
import requests
import json
from os import path
from urllib.parse import urlparse
#from pprint import pprint

class ActivityResponse(JsonResponse):
    def __init__(self, data):
        super().__init__(data, content_type='application/activity+json')


sentinel = object()
__cache__ = {}

def staticurl(request, path):
    path = _staticurl(path)
    if not path.startswith('http://') and not path.startswith('https://'):
        path = f'https://{request.site.domain}/{path.lstrip("/")}'
    return path

def is_json_request(request):
    '''Check if client wants json'''
    accept = request.META.get('HTTP_ACCEPT', '')
    return 'application/json' in accept or 'application/activity+json' in accept

def is_post_json(request):
    '''Check if request posts json'''
    return 'application/' in request.content_type and 'json' in request.content_type

def request_protocol(request):
    proto = 'http'
    if request.is_secure():
        proto = 'https'
    return proto

def log_request(request):
    return mail_admins(
        subject=f'SOCIAL {request.method} REQUEST: {request.path}',
        fail_silently=not settings.DEBUG,
        message=f'''
        GET: {request.META['QUERY_STRING']}
        
        POST: {request.POST.__str__()}
        
        META: {request.META.__str__()}
        
        BODY: {request.body.decode('utf-8', 'replace')}
        '''
    )

def fediverse_factory(request):
    if 'fediverse' not in __cache__:
        proto = request_protocol(request)
        #proto = 'https' ## FIXME probably they don't accept non https
        #if request.is_secure():
        #    proto = 'https'
        
        headers = {
            'Referer': f'{proto}://{request.site.domain}/',
            'Content-Type': 'application/activity+json',
            'User-Agent': f'Messy Fediverse +{proto}://{request.site.domain}',
            'Accept': 'application/activity+json, application/ld+json, application/json'
        }
        
        user = {
            "@context": [
                "https://www.w3.org/ns/activitystreams",
                #"https://w3id.org/security/v1",
                staticurl(request, 'messy/fediverse/litepub.json'),
                #f"{proto}://{request.site.domain}/schemas/litepub-0.1.jsonld",
                {
                    "@language": "und"
                }
            ],
            "published": "1970-01-01T00:00:00Z",
            "alsoKnownAs": [],
            "attachment": [],
            "capabilities": {
                "acceptsChatMessages": False
            },
            "discoverable": False,
            "endpoints": {
                #"oauthAuthorizationEndpoint": f"{proto}://{request.site.domain}{reverse('messy-fediverse:auth')}",
                #"oauthRegistrationEndpoint": f"{proto}://{request.site.domain}/social/api/apps/",
                "oauthTokenEndpoint": f"{proto}://{request.site.domain}{reverse('messy-fediverse:auth-token')}",
                "sharedInbox": f"{proto}://{request.site.domain}{reverse('messy-fediverse:inbox')}",
                #"uploadMedia": f"{proto}://{request.site.domain}/social/upload_media/"
            },
            "featured": f"{proto}://{request.site.domain}{reverse('messy-fediverse:featured')}",
            #"featured": {
            #    "type":"OrderedCollection",
            #    "totalItems":0,
            #    "orderedItems":[]
            #},
            "followers": f"{proto}://{request.site.domain}{reverse('messy-fediverse:followers')}",
            "following": f"{proto}://{request.site.domain}{reverse('messy-fediverse:following')}",
            "id": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}",
            "inbox": f"{proto}://{request.site.domain}{reverse('messy-fediverse:inbox')}",
            "manuallyApprovesFollowers": False,
            "name": settings.MESSY_FEDIVERSE.get('DISPLAY_NAME', f"{request.site.domain}"),
            "outbox": f"{proto}://{request.site.domain}{reverse('messy-fediverse:outbox')}",
            "preferredUsername": settings.MESSY_FEDIVERSE.get('USERNAME', f"{request.site.domain}"),
            "publicKey": {
                "id": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}#main-key",
                "owner": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}",
                "publicKeyPem": settings.MESSY_FEDIVERSE['PUBKEY']
            },
            "summary": "",
            "tag": [],
            "type": "Person",
            "url": settings.MESSY_FEDIVERSE.get('HOME', f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}")
        }
        
        ## If url defined without hostname
        userUrl = urlparse(user['url'])
        if not userUrl.netloc:
            user['url'] = userUrl._replace(scheme=proto, netloc=request.site.domain).geturl()
        
        
        for k in settings.MESSY_FEDIVERSE:
            if k in user:
                user[k] = settings.MESSY_FEDIVERSE[k]
        
        __cache__['fediverse'] = Fediverse(
            cache=cache,
            headers=headers,
            user=user,
            privkey=settings.MESSY_FEDIVERSE['PRIVKEY'],
            pubkey=settings.MESSY_FEDIVERSE['PUBKEY'],
            datadir=settings.MESSY_FEDIVERSE.get('DATADIR', settings.MEDIA_ROOT),
            debug=settings.DEBUG
        )
    return __cache__['fediverse']

@csrf_exempt
def main(request):
    if is_json_request(request):
        return root_json(request)
    else:
        return redirect('/')

def root_json(request):
    log_request(request)
    return ActivityResponse(fediverse_factory(request).user)

@csrf_exempt
def outbox(request):
    return dumb(request)

@csrf_exempt
def auth(request):
    return dumb(request)

@csrf_exempt
def auth_token(request):
    return dumb(request)

@csrf_exempt
def dumb(request, *args, **kwargs):
    proto = request_protocol(request)
    request_query_string = request.META.get('QUERY_STRING', '')
    if request_query_string:
        request_query_string = f'?{request_query_string}'
    
    return ActivityResponse({
        '@context': 'https://www.w3.org/ns/activitystreams',
        'id': f"{proto}://{request.site.domain}{request.path}{request_query_string}",
        'type': 'OrderedCollection',
        'totalItems': 0,
        'orderedItems': [],
        'success': log_request(request)
    })

@csrf_exempt
def featured(request, *args, **kwargs):
    proto = request_protocol(request)
    
    return ActivityResponse({
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{proto}://{request.site.domain}{reverse('messy-fediverse:featured')}",
        "type": "OrderedCollection",
        "totalItems": 0,
        "orderedItems": []
    })

def replies(request, rpath):
    if not is_json_request(request):
        raise PermissionDenied
    
    proto = request_protocol(request)
    request_query_string = request.META.get('QUERY_STRING', '')
    if request_query_string:
        request_query_string = f'?{request_query_string}'
    
    content = 'content' in request.GET
    
    return ActivityResponse({
        '@context': "https://www.w3.org/ns/activitystreams",
        'id': f"{proto}://{request.site.domain}{reverse('messy-fediverse:replies', kwargs={'rpath': rpath})}{request_query_string}",
        'type': 'CollectionPage',
        'partOf': f"{proto}://{request.site.domain}{reverse('messy-fediverse:replies', kwargs={'rpath': rpath})}",
        'items': fediverse_factory(request).get_replies(rpath, content=content)
    })

@method_decorator(csrf_exempt, name='dispatch')
class Inbox(View):
    def post(self, request):
        result = None
        if is_post_json(request):
            data = json.loads(request.body)
            if 'object' in data:
                data['object']['requestMeta'] = {}
                for k in request.META:
                    if k.startswith('HTTP_'):
                        data['object']['requestMeta'][k] = request.META[k]
                
                result = fediverse_factory(request).process_object(data['object'])
        
        log_request(request)
        
        return JsonResponse({'success': bool(result)})

def webfinger(request):
    '''
    Configure your web server so that request to "/.well-known/webfinger" points here.
    '''
    resource = request.GET.get('resource', None)
    result = None
    
    proto = request_protocol(request)
    
    if resource and resource.startswith('acct:') and '@' in resource:
        resource = resource.replace('acct:', '',  1)
        username, hostname = resource.split('@')
        if hostname == request.site.domain:
            fedUser = fediverse_factory(request)
            if fedUser.preferredUsername == username:
                result = {
                    'subject': f'acct:{username}@{hostname}',
                    'aliases': [
                        fedUser.url,
                        fedUser.id
                    ],
                    'links': [
                        {
                            'href': fedUser.url,
                            'rel': 'http://webfinger.net/rel/profile-page',
                            'type': 'text/html'
                        },
                        {
                            'href': fedUser.id,
                            'rel': 'self',
                            'type': 'application/activity+json'
                        },
                        {
                            'rel': 'http://ostatus.org/schema/1.0/subscribe',
                            'template': f'{proto}://{request.site.domain}{reverse("messy-fediverse:interact")}?acct={{uri}}'
                        }
                    ]
                }
    
    if not result:
        response = JsonResponse({'error': 'Resource not found'})
    else:
        response = JsonResponse(result)
    return response

@csrf_exempt
def status(request, rpath):
    filepath = fediverse_factory(request).normalize_file_path(f'{request.path.strip("/")}.json')
    
    if not path.isfile(filepath):
        raise Http404(f'Status {path} not found.')
    
    data = {}
    with open(filepath, 'rt', encoding='utf-8') as f:
        data = json.load(f)
    
    if is_json_request(request):
        if '@context' not in data:
            data['@context'] = [
                "https://www.w3.org/ns/activitystreams",
                #staticurl(request, 'messy/fediverse/litepub.json'),
                "https://litepub.social/litepub/litepub-v0.1.jsonld",
                {
                    "@language": "und"
                }
            ]
        return ActivityResponse(data)
        #return redirect(path.join(settings.MEDIA_URL, request.path.strip('/') + '.json'))
    
    if request.user.is_staff:
        return render(request, 'messy/fediverse/status.html', data)
    elif 'object' in data and 'inReplyTo' in data['object']:
        return redirect(data['object']['inReplyTo'])
    elif 'inReplyTo' in data:
        return redirect(data['inReplyTo'])
    else:
        raise Http404(f'Status {path} not found.')

class Interact(View):
    def get(self, request):
        if not request.user.is_staff:
            raise PermissionDenied

        url = request.GET.get('acct', None)
        if not url:
            raise BadRequest('What to interact with?')
        
        fediverse = fediverse_factory(request)
        data = fediverse.get(url)
        if type(data) is not dict:
            raise BadRequest(f'Got unexpected data from {url}')
        
        data['form'] = InteractForm(initial={'link': url})
        
        return render(request, 'messy/fediverse/interact.html', data)
    
    def post(self, request):
        form = InteractForm(request.POST)
        
        form_is_valid = form.is_valid()
        data = cache.get(form.cleaned_data['link'], sentinel)
        if not form.cleaned_data['link'] or data is sentinel:
            raise BadRequest(f'Object "{form.cleaned_data["link"]}" has been lost, try again.')
        
        if form_is_valid:
            ## do processing
            fediverse = fediverse_factory(request)
            result = fediverse.reply(data, form.cleaned_data['content'])
            
            #return redirect('/') ## FIXME
        
        data['form'] = form
        return render(request, 'messy/fediverse/interact.html', data)
