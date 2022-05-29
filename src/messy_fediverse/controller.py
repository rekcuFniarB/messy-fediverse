from django.conf import settings
from django.shortcuts import render, redirect
from django.http import JsonResponse, Http404
from django.urls import reverse
from django.core.exceptions import PermissionDenied, BadRequest
from django.core.cache import cache
from django.core.mail import mail_admins
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.templatetags.static import static as _staticurl
from .forms import InteractForm
from .fediverse import Fediverse
import requests
import json
from os import path
from urllib.parse import urlparse
#from pprint import pprint
#import json

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
        proto = 'https' ## FIXME probably they don't accept non https
        if request.is_secure():
            proto = 'https'
        
        headers = {
            'Referer': f'{proto}://{request.site.domain}/',
            'Content-Type': 'application/activity+json',
            'User-Agent': f'Messy Fediverse Instance +{proto}://{request.site.domain}',
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
                "oauthAuthorizationEndpoint": f"{proto}://{request.site.domain}{reverse('messy-fediverse:auth')}",
                "oauthRegistrationEndpoint": f"{proto}://{request.site.domain}/social/api/apps/",
                "oauthTokenEndpoint": f"{proto}://{request.site.domain}{reverse('messy-fediverse:auth-token')}",
                "sharedInbox": f"{proto}://{request.site.domain}{reverse('messy-fediverse:inbox')}",
                "uploadMedia": f"{proto}://{request.site.domain}/social/upload_media/"
            },
            "featured": f"{proto}://{request.site.domain}{reverse('messy-fediverse:featured')}",
            #"featured": {
            #    "type":"OrderedCollection",
            #    "totalItems":0,
            #    "orderedItems":[]
            #},
            "followers": f"{proto}://{request.site.domain}/social/followers/",
            "following": f"{proto}://{request.site.domain}/social/following/",
            "id": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}",
            "inbox": f"{proto}://{request.site.domain}{reverse('messy-fediverse:inbox')}",
            "manuallyApprovesFollowers": False,
            "name": f"{request.site.domain}",
            "outbox": f"{proto}://{request.site.domain}{reverse('messy-fediverse:outbox')}",
            "preferredUsername": f"{request.site.domain}",
            "publicKey": {
                "id": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}#main-key",
                "owner": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}",
                "publicKeyPem": settings.MESSY_SOCIAL['PUBKEY']
            },
            "summary": "",
            "tag": [],
            "type": "Person",
            "url": f"{proto}://{request.site.domain}{reverse('messy-fediverse:root')}"
        }
        
        __cache__['fediverse'] = Fediverse(
            cache=cache,
            headers=headers,
            user=user,
            privkey=settings.MESSY_SOCIAL['PRIVKEY'],
            pubkey=settings.MESSY_SOCIAL['PUBKEY'],
            datadir=settings.MEDIA_ROOT,
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
    response = JsonResponse(fediverse_factory(request).user)
    response.headers['Content-Type'] = 'application/activity+json'
    return response

@csrf_exempt
def inbox(request):
    return JsonResponse({'success': log_request(request)})

@csrf_exempt
def outbox(request):
    return JsonResponse({'success': log_request(request)})

@csrf_exempt
def auth(request):
    return JsonResponse({'success': log_request(request)})

@csrf_exempt
def auth_token(request):
    return JsonResponse({'success': log_request(request)})

@csrf_exempt
def dumb(request, *args, **kwargs):
    return JsonResponse({'success': log_request(request)})

@csrf_exempt
def featured(request, *args, **kwargs):
    response = JsonResponse({
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": "https://mastodon.social/users/pashaonesided/collections/featured",
        "type": "OrderedCollection",
        "totalItems": 0,
        "orderedItems": []
    })
    response.headers['Content-Type'] = 'application/activity+json'
    return response

@csrf_exempt
def status(request, rpath):
    filepath = path.join(settings.MEDIA_ROOT, request.path.strip('/') + '.json')
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
        response = JsonResponse(data)
        response.headers['Content-Type'] = 'application/activity+json'
        return response
        #return redirect(path.join(settings.MEDIA_URL, request.path.strip('/') + '.json'))
    
    if request.user.is_staff:
        return render(request, 'messy/fediverse/status.html', data)
    elif 'object' in data and 'inReplyTo' in data['object']:
        return redirect(data['object']['inReplyTo'])
    elif 'inReplyTo' in data:
        return redirect(data['inReplyTo'])
    else:
        raise Http404(f'Status {path} not found.')

def save(filename, data):
    ## Fixing filename
    if filename.startswith('http://') or filename.startswith('https://'):
        filename = urlparse(filename).path
    filename = filename.lstrip('/') ## removing leading slash
    if filename.endswith('.json.json'):
        filename = filename[:-len('.json')]
    if filename.endswith('/.json'):
        filename = filename[:-len('/.json')] + '.json'
    filepath = path.join(settings.MEDIA_ROOT, filename)
    dirpath = path.dirname(filepath)
    filename = path.basename(filename)
    path.os.makedirs(dirpath, mode=0o775, exist_ok=True)
    
    with open(filepath, 'wt', encoding='utf-8') as f:
        datatype = type(data)
        if (datatype is str):
            f.write(data)
        else:
            json.dump(data, f)

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
        
        return render(request, 'messy_fediverse/interact.html', data)
    
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
            #if 'object' in result and 'id' in result['object']:
            #    save(result['object']['id'] + '.json', result['object'])
            
            #return redirect('/') ## FIXME
        
        data['form'] = form
        return render(request, 'messy_fediverse/interact.html', data)
