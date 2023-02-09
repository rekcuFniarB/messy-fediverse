from django.conf import settings
from django.shortcuts import render, redirect
from django.http import JsonResponse, Http404
from django.core.exceptions import PermissionDenied, BadRequest
from django.core.cache import cache
from django.core.mail import mail_admins
from django.core.files.base import ContentFile
from django.views import View
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.utils.decorators import method_decorator
from django.templatetags.static import static as _staticurl
from django.utils.html import strip_tags
from .forms import InteractForm, InteractSearchForm, ReplyForm
from .fediverse import Fediverse
from . import html
import requests
import json
from os import path
from urllib.parse import urlparse
from django.utils.http import urlencode
from datetime import datetime
import sys
from asgiref.sync import sync_to_async
import aiohttp
from .models import Activity, Follower, FederatedEndpoint
#from pprint import pprint

class ActivityResponse(JsonResponse):
    def __init__(self, data, request=None):
        content_type = 'application/activity+json'
        
        if request:
            ## Return content type they want
            accept = request.META.get('HTTP_ACCEPT', '').lower().split(',')[0].split(';')[0]
            if accept.startswith('application/') and 'json' in accept:
                content_type = accept
        
        super().__init__(data, content_type=content_type)


sentinel = object()
__cache__ = {}

def stderrlog(*msg):
    if settings.DEBUG:
        print(*msg, file=sys.stderr, flush=True)

@sync_to_async
def request_user_is_staff(request):
    ## We can't just use request.user.is_staff from async views
    ## due to lazy executions, so we get errors.
    return request.user.is_staff

def reverse(name, args=None, kwargs=None):
    '''
    Django's url reverse wrapper
    '''
    ## Lazy imports to avoid circular import issues
    if 'app_name' not in __cache__:
        from .urls import app_name
        __cache__['app_name'] = app_name
    if 'reverse' not in __cache__:
        from django.urls import reverse
        __cache__['reverse'] = reverse
    
    if type(kwargs) is dict and 'rpath' in kwargs:
        kwargs['rpath'] = kwargs['rpath'].strip('/')
    
    result =  __cache__['reverse'](f"{__cache__['app_name']}:{name}", args=args, kwargs=kwargs)
    if result and result.endswith('//'):
        result = result.rstrip('/') + '/'
    
    return result

def reversepath(name, path):
    '''
    reverse() wrapper
    name: url name
    path: rpath param.
    '''
    return reverse(name, kwargs={'rpath': path})

def staticurl(request, path):
    path = _staticurl(path)
    if not path.startswith('http://') and not path.startswith('https://'):
        path = f'https://{request.site.domain}/{path.lstrip("/")}'
    return path

def is_json_request(request):
    '''Check if client wants JSON'''
    accept = request.META.get('HTTP_ACCEPT', '').lower()
    return 'application/json' in accept or 'application/activity+json' in accept or 'application/ld+json' in accept

def is_post_json(request):
    '''Check if request posts json'''
    return 'application/' in request.content_type and 'json' in request.content_type

def is_ajax(request):
    '''
    Check if request is AJAX (if header X-Requested-With is XMLHttpRequest).
    '''
    return request.META.get('HTTP_X_REQUESTED_WITH', '').upper() == 'XMLHTTPREQUEST'

def request_protocol(request):
    proto = 'http'
    if request.is_secure():
        proto = 'https'
    return proto

async def log_request(request, data=None):
    if settings.MESSY_FEDIVERSE.get('LOG_REQUESTS_TO_MAIL', False) and request.method == 'POST':
        subject=f'SOCIAL {request.method} REQUEST: {request.path}'
        body = None
        fediverse = fediverse_factory(request)
        content = ''
        
        if data:
            user_host = ''
            if 'authorInfo' in data and 'user@host' in data['authorInfo']:
                user_host = data['authorInfo']['user@host']
            
            subject = f'Fediverse {data["type"]} {user_host}'
            body = json.dumps(data, indent=4)
            body = f'<code><pre>{body}</pre></code>'
            
            apobject = data.get('object', None)
            
            if (
                type(apobject) is str and
                (
                    apobject.startswith('http://') or
                    apobject.startswith('https://')
                )
            ):
                ## Trying to get object content
                async with aiohttp.ClientSession() as session:
                    apobject, = await fediverse.gather_http_responses(fediverse.get(apobject, session))
            
            if type(apobject) is dict:
                content = apobject.get('content', '')
        
        if not body:
            body = request.body.decode('utf-8', 'replace')
        
        message = f'''{content}<br><br>
            GET: {request.META['QUERY_STRING']}
            <br>
            POST: {request.POST.__str__()}
            <br>
            BODY:{body}
            <br>
            META: {request.META.__str__()}
        '''
        
        return mail_admins(
            subject=subject,
            fail_silently=not settings.DEBUG,
            message=strip_tags(message),
            html_message=message
        )
    else:
        return False

async def email_notice(request, activity):
    ap_object = activity.get('object', {})
    if 'type' in ap_object:
        fediverse = fediverse_factory(request)
        subj_parts = ['Fediverse', ap_object['type']]
        summary = ap_object.get('summary', None)
        if summary:
            subj_parts.append(summary)
        attributedTo = ap_object.get('attributedTo', None)
        if attributedTo:
            if 'authorInfo' not in activity:
                activity['authorInfo'] = {}
                try:
                    async with aiohttp.ClientSession() as session:
                        activity['authorInfo'], = await fediverse.gather_http_responses(fediverse.get(attributedTo, session))
                except:
                    pass
            
            if 'authorInfo' in activity and type(activity['authorInfo']) is dict:
                if 'preferredUsername' in activity['authorInfo'] and not activity['authorInfo'].get('user@host', None):
                    activity['authorInfo']['user@host'] = ''
                    author_url = urlparse(activity['authorInfo']['id'])
                    activity['authorInfo']['user@host'] = f'{activity["authorInfo"]["preferredUsername"]}@{author_url.netloc}'
                subj_parts.append('by')
                subj_parts.append(activity['authorInfo'].get('user@host', ''))
        
        content = ap_object.get('content', '')
        
        url = ap_object.get('inReplyTo', ap_object.get('url', ap_object.get('id', '')))
        if url and url.startswith('https://') and '"' not in url:
            url = f'<a href="{url}" target="_blank">{url}</a>'
        
        message=f'''{content}<br>
            {url}
            <br>
            <h2>Raw data:</h2>
            <code><pre>{json.dumps(activity, indent=4)}</pre></code>
            
            <h2>Request debug info:</h2>
            <code><pre>
            GET: {request.META['QUERY_STRING']}
            
            POST: {request.POST.__str__()}
            
            META: {request.META.__str__()}
            
            BODY: {request.body.decode('utf-8', 'replace')}
            </code></pre>
        '''
        
        return mail_admins(
            subject=' '.join(subj_parts),
            fail_silently=not settings.DEBUG,
            message=strip_tags(message),
            html_message=message
        )
    else:
        return False

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
                #staticurl(request, 'messy/fediverse/litepub.json'),
                #'https://litepub.social/litepub/litepub-v0.1.jsonld',
                'https://cloudflare-ipfs.com/ipfs/QmUt2rFamEsBxSkUd7DwE7SXr5BVxTQviGMH6Hwj9bKzTE/litepub-0.1.jsonld',
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
                #"oauthAuthorizationEndpoint": f"{proto}://{request.site.domain}{reverse('auth')}",
                #"oauthRegistrationEndpoint": f"{proto}://{request.site.domain}/social/api/apps/",
                "oauthTokenEndpoint": f"{proto}://{request.site.domain}{reverse('auth-token')}",
                "sharedInbox": f"{proto}://{request.site.domain}{reverse('inbox')}?shared=y",
                #"uploadMedia": f"{proto}://{request.site.domain}/social/upload_media/"
            },
            "featured": f"{proto}://{request.site.domain}{reverse('featured')}",
            #"featured": {
            #    "type":"OrderedCollection",
            #    "totalItems":0,
            #    "orderedItems":[]
            #},
            "followers": f"{proto}://{request.site.domain}{reverse('followers')}",
            "following": f"{proto}://{request.site.domain}{reverse('following')}",
            "id": f"{proto}://{request.site.domain}{reverse('root')}",
            "inbox": f"{proto}://{request.site.domain}{reverse('inbox')}?direct=y",
            "manuallyApprovesFollowers": True,
            "name": settings.MESSY_FEDIVERSE.get('DISPLAY_NAME', f"{request.site.domain}"),
            "outbox": f"{proto}://{request.site.domain}{reverse('outbox')}",
            "preferredUsername": settings.MESSY_FEDIVERSE.get('USERNAME', f"{request.site.domain}"),
            "publicKey": {
                "id": f"{proto}://{request.site.domain}{reverse('root')}#main-key",
                "owner": f"{proto}://{request.site.domain}{reverse('root')}",
                "publicKeyPem": settings.MESSY_FEDIVERSE['PUBKEY']
            },
            "summary": "",
            "tag": [],
            "type": "Person",
            "url": settings.MESSY_FEDIVERSE.get('HOME', f"{proto}://{request.site.domain}{reverse('root')}")
        }
        
        ## If url defined without hostname
        userUrl = urlparse(user['url'])
        if not userUrl.netloc:
            user['url'] = userUrl._replace(scheme=proto, netloc=request.site.domain).geturl()
        
        
        for k in settings.MESSY_FEDIVERSE:
            if k != k.upper():
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
    
    __cache__['fediverse'].federated_endpoints = FederatedEndpoint.objects.filter(disabled=False)
    
    return __cache__['fediverse']

@csrf_exempt
def main(request):
    if is_json_request(request):
        return root_json(request)
    else:
        return redirect('/')

def root_json(request):
    ## FIXME never awaited warning
    log_request(request)
    return ActivityResponse(fediverse_factory(request).user, request)

#@csrf_exempt
async def outbox(request):
    return await dumb(request)

#@csrf_exempt
async def auth(request):
    return await dumb(request)

#@csrf_exempt
async def auth_token(request):
    return await dumb(request)

#@csrf_exempt
async def dumb(request, *args, **kwargs):
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
        'success': await log_request(request)
    }, request)

## Temporary workaround due to error
## "View didn't return an HttpResponse object. It returned an unawaited coroutine instead. You may need to add an 'await' into your view."
## Waiting for fix in Django https://code.djangoproject.com/ticket/31949
dumb.csrf_exempt = True
auth_token.csrf_exempt = True
auth.csrf_exempt = True
outbox.csrf_exempt = True


async def save_activity(request, activity):
    '''
    Saving activity.
    request: django HttpRequest instance
    activity: dict
    '''
    act = None
    json_path = activity.get('_json', None)
    apobject = activity.get('object', {})
    activity_id = activity.get('id', None)
    incoming = True
    
    if type(apobject) is dict:
        object_id = apobject.get('id', None)
    else:
        object_id = apobject
        apobject = {}
    
    if not json_path:
        json_path = apobject.get('_json', None)
    
    actType = None
    act_type = activity.get('type', apobject.get('type', ''))
    for def_type in Activity.TYPES:
        if act_type == def_type[1]:
            actType = def_type[0]
    
    if actType and activity_id:
        fediverse = fediverse_factory(request)
        
        if activity.get('actor', None) == fediverse.id:
            ## Is outgoing
            incoming = False
        
        act = await Activity.objects.filter(uri=activity_id).afirst()
        ## FIXME maybe we should quit entire processing if activity already exists
        if not act:
            act = Activity(
                uri = activity_id,
                activity_type = actType,
                actor_uri = activity.get('actor', None),
                object_uri = object_id,
                context = apobject.get('context', apobject.get('conversation', activity.get('context', activity.get('conversation', apobject.get('id', ''))))),
                incoming = incoming
            )
            
            ## If json already stored
            if json_path and json_path.endswith('.json'):
                if not path.isfile(json_path):
                    ## Tryint to normalize
                    json_path = fediverse.normalize_file_path(json_path)
                if path.isfile(json_path):
                    act.self_json.name = path.relpath(json_path, settings.MEDIA_ROOT)
            else:
                act.self_json.save('activity.json', content=ContentFile(json.dumps(activity)), save=False)
            
            await sync_to_async(act.save)()
        
        if actType == 'FOL':
            ## If follow request
            follower = await Follower.objects.filter(uri=act.actor_uri, object_uri=object_id).afirst()
            ## Retrieving actor info from the net
            ## Getting existing session
            session = await fediverse.http_session()
            actorInfo, = await fediverse.gather_http_responses(fediverse.get(act.actor_uri, session=session))
            ## If actor info is valid
            endpoint_url = None
            if type(actorInfo) is dict:
                if 'endpoints' in actorInfo and type(actorInfo['endpoints']) is dict and 'sharedInbox' in actorInfo['endpoints']:
                    endpoint_url = actorInfo['endpoints']['sharedInbox']
                elif 'inbox' in actorInfo:
                    endpoint_url = actorInfo['inbox']
            
            if endpoint_url:
                ## Creating endpoint if not exists yet
                endpoint = await FederatedEndpoint.objects.filter(uri=endpoint_url).afirst()
                if not endpoint:
                    ## Not created yet
                    endpoint = FederatedEndpoint(uri=endpoint_url)
                    await sync_to_async(endpoint.save)()
                
                if not follower:
                    follower = Follower(
                        uri=act.actor_uri,
                        object_uri=object_id,
                        disabled=False,
                        accepted=False
                    )
                
                follower.endpoint = endpoint
                follower.activity = act
                
                if not fediverse.manuallyApprovesFollowers:
                    apobject = {
                        'id': activity['id'],
                        'type': activity['type'],
                        'actor': activity['actor'],
                        'object': object_id
                    }
                    acceptActivity = fediverse.activity(
                        type='Accept',
                        object=apobject,
                        to=[actorInfo['id']],
                        inReplyTo=activity_id,
                        actor=fediverse.id
                    )
                    response, = await fediverse.gather_http_responses(
                        fediverse.post(actorInfo['inbox'], session, json=acceptActivity)
                    )
                    acceptActivity['_response'] = response
                    ## Saving outgoing too
                    await save_activity(request, acceptActivity)
                    follower.accepted = True
                
                await sync_to_async(follower.save)()
                    
        ## endif 'FOL' (follow request)
        elif actType == 'UND':
            if apobject.get('type', None) == 'Follow' and object_id:
                ## if unfollow request
                followers = Follower.objects.filter(uri=object_id)
                await followers.aupdate(disabled=True)
    
    return act


@csrf_exempt
def featured(request, *args, **kwargs):
    proto = request_protocol(request)
    
    return ActivityResponse({
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{proto}://{request.site.domain}{reverse('featured')}",
        "type": "OrderedCollection",
        "totalItems": 0,
        "orderedItems": []
    }, request)

class Replies(View):
    def parent_uri(self, request, rpath):
        rpath = rpath.strip('/')
        proto = request_protocol(request)
        # request_query_string = request.META.get('QUERY_STRING', '')
        # if request_query_string:
        #     request_query_string = f'?{request_query_string}'
        request_query_string = ''
        #return f"{proto}://{request.site.domain}{reversepath('replies', rpath)}{request_query_string}"
        return f"{proto}://{request.site.domain}/{rpath}/{request_query_string}"
    
    @sync_to_async
    def render_page(self, request, rpath, data_update={}):
        ## Have to use sync_to_async
        ## due to 'request.user.is_staff' is used in the template
        ## and in async mode an error occurs:
        ##      You cannot call this from an async context - use a thread or sync_to_async.
        data = {
            'parent_uri': self.parent_uri(request, rpath),
            'rpath': rpath
        }
        data.update(data_update)
        
        return render(
            request,
            'messy/fediverse/replies.html',
            data
        )
    
    async def get_replies(self, request, rpath, content=False):
        rpath = rpath.strip('/')
        replies = []
        if not rpath.startswith('http://') and not rpath.startswith('https://'):
            ## legacy method
            replies = await fediverse_factory(request).get_replies(rpath, content=content)
            context = self.parent_uri(request, rpath)
        else:
            context = rpath
        
        items = Activity.objects.filter(context=context, disabled=False)
        async for item in items:
            if item.object_uri == context:
                continue
            
            if content:
                replies.append(item.get_dict())
            else:
                replies.append(item.object_uri)
            
            replies.extend(await self.get_replies(request, item.object_uri, content))
        
        return replies
    
    async def get(self, request, rpath):
        rpath = rpath.strip('/')
        proto = request_protocol(request)
        
        content = 'content' in request.GET
        fediverse = fediverse_factory(request)
        
        if is_json_request(request):
            async with aiohttp.ClientSession() as session:
                await fediverse.http_session(session)
                # items = await fediverse.get_replies(rpath, content=content)
                items = await self.get_replies(request, rpath, content=content)
            
            return ActivityResponse({
                '@context': "https://www.w3.org/ns/activitystreams",
                'id': f"{proto}://{request.site.domain}{reversepath('replies', rpath)}",
                'type': 'CollectionPage',
                'partOf': f"{proto}://{request.site.domain}{reversepath('replies', rpath)}",
                'items': items
            }, request)
        else:
            async with aiohttp.ClientSession() as session:
                await fediverse.http_session(session)
                data = {
                    'items': await self.get_replies(request, rpath, content=True),
                    'form': ReplyForm(),
                    'summary': ''
                }
            
            context_root_url = reversepath('dumb', 'context').rstrip('/')
            context = None
            
            for n, apobject in enumerate(data['items']):
                if 'object' in apobject and type(apobject['object']) is dict:
                    ## apobject is actually an activity
                    apobject.update(apobject['object'])
                    data['items'][n] = apobject
                
                if not data['summary'] and 'summary' in apobject and apobject['summary']:
                    data['summary'] = apobject['summary']
                
                if apobject['published']:
                    apobject['published'] = datetime.fromisoformat(apobject['published'].rstrip('Z'))
                
                if not context:
                    if 'context' in apobject and apobject['context'].startswith(fediverse.id):
                        context = apobject['context']
                    elif 'conversation' in apobject and apobject['conversation'].startswith(fediverse.id):
                        context = apobject['conversation']
            
            if context:
                data['parent_path'] = '/' + urlparse(context).path.replace(context_root_url, '', 1).lstrip('/')
                data['parent_object'] = fediverse.read(data['parent_path'] + '.json')
            
            if not data['summary']:
                path_parts = urlparse(rpath).path.strip('/').split('/')
                if len(path_parts) > 0:
                    data['summary'] = path_parts[-1].capitalize()
            
            data['meta_json'] = json.dumps({
                'title': f'Comments for {data["summary"]}'
            })
            
            return await self.render_page(request, rpath, data)
    
    async def post(self, request, rpath):
        rpath = rpath.strip('/')
        ## If federated reply
        if 'account' in request.POST:
            request_post = request.POST.copy()
            uri = request_post.get('uri', None)
            if not uri:
                request_post['uri'] = self.parent_uri(request, rpath)
            
            form = ReplyForm(request_post)
            if form.is_valid():
                fediverse = fediverse_factory(request)
                link_template = None
                
                username, host = form.cleaned_data['account'].split('@')
                
                async with aiohttp.ClientSession() as session:
                    webfinger, = await fediverse.gather_http_responses(fediverse.get(f'https://{host}/.well-known/webfinger?resource=acct:{form.cleaned_data["account"]}', session=session))
                
                if type(webfinger) is dict and 'links' in webfinger and type(webfinger['links']) is list:
                    for link in webfinger['links']:
                        if type(link) is dict and 'template' in link and link['template']:
                            link_template = link['template']
                            break
                
                ## Validating webfinger response
                if not link_template or '{uri}' not in link_template or not (link_template.startswith('https://') or link_template.startswith('http://')):
                    raise BadRequest(f'Webfinger for {host} failed.\n{link_template}')
                
                uri = form.cleaned_data.get('uri', None)
                if not uri:
                    ## Reply to thread root.
                    uri = self.parent_uri(request, rpath)
                
                ## Redirecting to federated instance's form
                if is_ajax(request):
                    return JsonResponse({'popup': link_template.format(uri=form.cleaned_data['uri'])})
                else:
                    return redirect(link_template.format(uri=form.cleaned_data['uri']))
            else:
                async with aiohttp.ClientSession() as session:
                    await fediverse.http_session(session)
                    items = await self.get_replies(request, rpath, content=True)
                return await self.render_page(request, rpath, {'items': items, 'form': form})
    
        else:
            raise BadRequest('Unknown form was submitted.')
    
    async def delete(self, request, rpath):
        if not await request_user_is_staff(request):
            raise PermissionDenied
        
        fediverse = fediverse_factory(request)
        object_id = request.GET.get('id')
        object_uri = request.GET.get('uri')
        result = {'success': False}
        activity = None
        
        try:
            if object_uri:
                activity = await Activity.objects.filter(object_uri=object_uri).afirst()
            if activity:
                activity.disabled = True
                await sync_to_async(activity.save)()
                result['success'] = True
            elif object_id:
                ## Old version fallback
                fediverse.delete_reply(object_id)
                result['success'] = True
            else:
                result['error'] = 'ID required'
        except BaseException as error:
                result['error'] = '\n'.join(error.args)
        
        return JsonResponse(result)

@method_decorator(csrf_exempt, name='dispatch')
class Inbox(View):
    async def post(self, request):
        result = None
        saveResult = None
        should_log_request = True
        data = None
        responseData = {'success': False}
        
        ## If we've received a JSON
        if is_post_json(request):
            data = json.loads(request.body)
            fediverse = fediverse_factory(request)
            async with aiohttp.ClientSession() as session:
                await fediverse.http_session(session)
                
                if 'requestMeta' not in data:
                    data['requestMeta'] = {}
                for k in request.META:
                    if k.startswith('HTTP_'):
                        data['requestMeta'][k] = request.META[k]
                
                # result = await fediverse.process_object(data)
                # data['_json'] = result
                if 'actor' in data and 'authorInfo' not in data and 'authorInfo' not in data.get('object', {}):
                    data['authorInfo'], = await fediverse.gather_http_responses(fediverse.get(data['actor'], session))
                
                await email_notice(request, data)
                should_log_request = False
                
                saveResult = await save_activity(request, data)
                
        
        if 'type' in data and data['type'] == 'Delete':
            should_log_request = False
            return JsonResponse({
                'success': True,
                'status': 'success',
                'message': "Fuck off, I don't give a fuck what you delete."
            })
        
        if should_log_request:
            await log_request(request, data)
        
        responseData['success'] = bool(saveResult)
        if saveResult:
            responseData['activity'] = saveResult.get_dict()
        
        return JsonResponse(responseData)

class Following(View):
    
    async def get(self, request):
        if is_json_request(request):
            return await dumb(request)
        elif not await request_user_is_staff(request):
            raise PermissionDenied
        
        fediverse = fediverse_factory(request)
        async with aiohttp.ClientSession() as session:
            await fediverse.http_session(session)
            data = {'following': fediverse.get_following()}
        
        for item in data['following']:
            item['fediverseInstance'] = urlparse(item['id']).hostname
        
        return render(request, 'messy/fediverse/following.html', data)
    
    async def post(self, request):
        if not await request_user_is_staff(request):
            raise PermissionDenied
        
        user_id = request.POST.get('id', None)
        if not user_id:
            raise BadRequest('User ID required')
        
        fediverse = fediverse_factory(request)
        
        result = None
        async with aiohttp.ClientSession() as session:
            await fediverse.http_session(session)
            if request.POST.get('follow', None):
                result = await fediverse.follow(user_id)
            elif request.POST.get('unfollow', None):
                result = await fediverse.unfollow(user_id)
        
        return redirect(reverse('interact') + '?' + urlencode({'acct': user_id}))

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
                            'template': f'{proto}://{request.site.domain}{reverse("interact")}?acct={{uri}}'
                        }
                    ]
                }
    
    if not result:
        response = JsonResponse({'error': 'Resource not found'})
    else:
        response = JsonResponse(result)
    return response

#@method_decorator(csrf_exempt, name='dispatch')
class Status(View):
    async def get(self, request, rpath):
        proto = request_protocol(request)
        object_uri = f'{proto}://{request.site.domain}{reversepath("status", rpath)}'
        data = {}
        fediverse = fediverse_factory(request)
        activity = await Activity.get_note_activity(object_uri, fediverse)
        
        if not activity:
            filepath = fediverse.normalize_file_path(f'{request.path.strip("/")}.json')
            
            if not path.isfile(filepath):
                raise Http404(f'Status {rpath} not found.')
            
            with open(filepath, 'rt', encoding='utf-8') as f:
                activity = json.load(f)
        else:
            ## Got object from model
            activity = activity.get_dict()
        
        apobject = activity
        
        if 'object' in activity and type(activity['object']) is dict:
            apobject = activity['object']
        
        if 'conversation' not in apobject and 'context' not in apobject:
            apobject['context'] = apobject['conversation'] = apobject['id']
        
        data['deleted'] = False
        object_uri = f'{proto}://{request.site.domain}{reversepath("status", rpath)}'
        delActivity = await Activity.objects.filter(object_uri=object_uri, activity_type='DEL', incoming=False, actor_uri=fediverse.id).afirst()
        
        if is_json_request(request):
            if delActivity:
                raise Http404(f'Status {rpath} was deleted.')
            
            if '@context' not in apobject:
                apobject['@context'] = fediverse.user.get('@context')
            
            if 'replies' not in apobject:
                apobject['replies'] = f'{proto}://{request.site.domain}{reversepath("replies", request.path)}'
            
            return ActivityResponse(apobject, request)
        
        is_staff = await request_user_is_staff(request)
        
        if is_staff:
            data['raw_json'] = json.dumps(activity, indent=4)
            if delActivity:
                data['deleted'] = {
                    'id': delActivity.pk,
                    'uri': delActivity.uri,
                    'meta': delActivity._meta,
                    #'url': reverse(f'admin:{delActivity._meta.app_label}_{delActivity._meta.model_name}_change',  args=[delActivity.pk])
                }
        else:
            if delActivity:
                raise Http404(f'Status {rpath} was deleted.')
            elif 'inReplyTo' in apobject and apobject['inReplyTo']:
                return redirect(apobject['inReplyTo'])
            elif 'url' in apobject and apobject['url'] and apobject['url'] != apobject['id']:
                return redirect(apobject['url'])
        
        if apobject['context'].startswith(fediverse.id):
            apobject['reply_path'] = reversepath('replies', urlparse(apobject['id']).path)
        
        if 'published' in apobject and apobject['published']:
            apobject['published'] = datetime.fromisoformat(apobject['published'].rstrip('Z'))
        
        data['activity'] = activity
        data['object'] = apobject
        data['rpath'] = rpath
        
        return render(request, 'messy/fediverse/status.html', data)
        #raise Http404(f'Status {path} not found.')
    
    async def delete(self, request, rpath):
        if not await request_user_is_staff(request):
            raise PermissionDenied
        
        proto = request_protocol(request)
        object_uri = f'{proto}://{request.site.domain}{reversepath("status", rpath)}'
        fediverse = fediverse_factory(request)
        async with aiohttp.ClientSession() as session:
            await fediverse.http_session(session)
            ## FIXME should also send requests to mentioned instances
            activity = await fediverse.delete_status(object_uri)
            await save_activity(request, activity)
        
        # return redirect(reversepath('status', rpath))
        return JsonResponse({'alert': f'Delete activity sent.', 'activity': activity});

class Interact(View):
    async def get(self, request):
        if not await request_user_is_staff(request):
            raise PermissionDenied
        
        form_textarea_content = []
        url = request.GET.get('acct', None)
        #if not url:
        #    raise BadRequest('What to interact with?')
        
        fediverse = fediverse_factory(request)
        data = {}
        if url:
            async with aiohttp.ClientSession() as session:
                #await fediverse.http_session(session)
                fresponse = fediverse.get(url, session=session)
                data, = await fediverse.gather_http_responses(fresponse)
            if type(data) is not dict:
                raise BadRequest(f'Got unexpected data from {url}: {data}')
        
        if 'url' not in data and 'id' in data:
            data['url'] = data['id']
        
        if 'id' in data:
            data['fediverseInstance'] = urlparse(data['id']).hostname
        
        ## If is an user profile
        if 'publicKey' in data:
            data['weFollow'] = fediverse.doWeFollow(data['id'])
        else:
            user_ids = []
            for attr in ['to', 'cc']:
                if attr in data and type(data[attr]) is list:
                    for link in data[attr]:
                        if link not in user_ids:
                            user_ids.append(link)
            
            if 'attributedTo' in data and data['attributedTo'] not in user_ids:
                user_ids.append(data['attributedTo'])
            
            ## Getting users info
            tasks = []
            async with aiohttp.ClientSession() as session:
                for user_id in user_ids:
                    if type(user_id) is str:
                        tasks.append(fediverse.get(user_id, session=session))
                    elif type(user_id) is list:
                        ## Peertube?
                        for item in user_id:
                            if type(item) is str:
                                tasks.append(fediverse.get(item, session=session))
                            elif type(item) is dict and 'id' in item:
                                tasks.append(fediverse.get(item['id'], session=session))
                tasks = await fediverse.gather_http_responses(*tasks)
            
            for user_obj in tasks:
                ## If is user object
                if type(user_obj) is dict and 'preferredUsername' in user_obj and 'id' in user_obj:
                    user_host = urlparse(user_obj['id']).hostname
                    if user_host:
                        user_at_host = f'{user_obj["preferredUsername"]}@{user_host}'
                        form_textarea_content.append(user_at_host)
        
        if 'tag' in data and type(data['tag']) is list:
            for tag in data['tag']:
                if type(tag) is dict:
                    if tag.get('type', None) == 'Hashtag':
                        link_href = tag.get('href', None)
                        link_name = tag.get('name', None)
                        if link_href and link_name:
                            link = html.TagA(attr_href=link_href, name=link_name, attr_rel='tag noopener', attr_class=['mention', 'hashtag'], attr_target='_blank')
                            form_textarea_content.append(str(link))
        
        data['rawJson'] = json.dumps(data, indent=4)
        data['replyDirect'] = request.GET.get('reply_direct', None)
        
        get_context = request.GET.get('context', '')
        if get_context:
            data['context'] = get_context
        
        data['form'] = InteractForm(initial={
            'link': url,
            'content': ' '.join(form_textarea_content),
            'reply_direct': data['replyDirect'],
            'context': data.get('context', '')
        })
        data['search_form'] = InteractSearchForm(initial={'acct': url})
        
        return render(request, 'messy/fediverse/interact.html', data)
    
    async def post(self, request):
        form = InteractForm(request.POST)
        data = None
        result = None
        form_is_valid = form.is_valid()
        
        if form_is_valid:
            ## do processing
            fediverse = fediverse_factory(request)
            async with aiohttp.ClientSession() as session:
                await fediverse.http_session(session)
                
                ## If we are replying
                if 'link' in form.cleaned_data and form.cleaned_data['link']:
                    data, = await fediverse.gather_http_responses(fediverse.get(form.cleaned_data['link'], session=session))
                    #data = cache.get(form.cleaned_data['link'], sentinel)
                    if data is sentinel:
                        raise BadRequest(f'Object "{form.cleaned_data["link"]}" has been lost, try again.')
                
                if data:
                    if 'type' in data and data['type'] == 'Person':
                        #and form.cleaned_data['reply_direct']:
                        ## It'a a direct message FIXME
                        person_to_reply = data
                        ## Preparing source
                        data = {
                            'attributedTo': person_to_reply['id'],
                            'attributedToPerson': person_to_reply
                        }
                    if form.cleaned_data['reply_direct']:
                        data['id'] = form.cleaned_data['reply_direct']
                        data['directMessage'] = True
                    if form.cleaned_data['context']:
                        data['context'] = form.cleaned_data['context']
                
                result = await fediverse.new_status(
                    message=form.cleaned_data['content'],
                    subject=form.cleaned_data.get('subject'),
                    url=form.cleaned_data.get('custom_url'),
                    tags=form.cleaned_data.get('tags'),
                    replyToObj=data
                )
            
            if result:
                ## FIXME This became a little bit messy
                redirect_path = result['object']['id']
                context = result['object'].get('context', result['object'].get('conversation', None))
                if context and context.startswith(fediverse.id):
                    context = urlparse(context).path
                    redirect_path = context.replace(reversepath('dumb', 'context'), '').strip('/')
                    redirect_path = reversepath('replies', redirect_path)
                
                await save_activity(request, result)
                return redirect(redirect_path)
        
        data['form'] = form
        return render(request, 'messy/fediverse/interact.html', data)
