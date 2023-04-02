import requests
import aiohttp
import asyncio
from os import path
from datetime import datetime
import json
from urllib.parse import urlparse
from email import utils as emailutils
from base64 import b64encode
from OpenSSL import crypto
from hashlib import sha256
import re
import syslog
import sys
from functools import partial
from asgiref.sync import sync_to_async
from . import html

class Fediverse:
    def __init__(self, user, privkey, pubkey, headers=None, datadir='/tmp', cache=None, debug=False):
        '''
        :cache object: optional cache object used for caching requests
        '''
        self.__cache__ = cache
        self.__sentinel__ = object()
        self.__headers__ = headers
        self.__user__ = user
        self.__privkey__ = privkey
        self.__pubkey__ = pubkey
        self.__datadir__ = datadir
        self.__DEBUG__ = debug
        self._rewhitespace = re.compile(r'\s+')
    
    def __getattr__(self, name):
        return self.__user__.get(name, None)
    
    def uniqid(self):
        return hex(int(str(datetime.now().timestamp()).replace('.', '')))[2:]
    
    def syslog(self, *msg):
        if self.__DEBUG__:
            msg = '\n '.join(msg)
            syslog.syslog(syslog.LOG_INFO, f'MESSY SOCIAL: {msg}')
    
    def stderrlog(self, *msg):
        if self.__DEBUG__:
            print(*msg, file=sys.stderr, flush=True)
    
    def mk_cache_key(self, key):
        if key.startswith('http://'):
            ## Redirected urls workaround
            key = key.replace('http://', 'https://', 1)
        return key.split('#')[0]
    
    async def cache_set(self, name, value):
        if self.__cache__ is not None:
            if hasattr(value, 'result') and callable(value.result):
                ## For future like objects
                value = value.result()
                if type(value) is list:
                    value = value[0]
                
                ## FIXME cannot reuse already awaited coroutine
            
            name = self.mk_cache_key(name)
            return await sync_to_async(self.__cache__.set)(name, value)
    
    @property
    def user(self):
        return self.__user__
    
    def normalize_file_path(self, filename):
        '''
        Normalizing file path. Making it absolute and other sanitizings.
        filename: string, relative file path
        Returns absolute file path string.
        '''
        ## Fixing filename
        if filename.startswith('http://') or filename.startswith('https://'):
            filename = urlparse(filename).path
        filename = filename.lstrip('/') ## removing leading slash
        
        if filename.endswith('.json.json'):
            filename = filename[:-len('.json')]
        if filename.endswith('/.json'):
            filename = filename[:-len('/.json')] + '.json'
        
        ## Removing repeating subdirs if any
        filenameparts = filename.split('/')
        if (len(filenameparts) > 1 and
            (self.__datadir__.endswith(f'/{filenameparts[0]}')
            or self.__datadir__.endswith(f'/{filenameparts[0]}/') )):
                filenameparts = filenameparts[1:]
                filename = path.join(*filenameparts)
        
        filepath = path.join(self.__datadir__, filename)
        
        return filepath
    
    def symlink(self, source, destination):
        source = self.normalize_file_path(source)
        destination = self.normalize_file_path(destination)
        dirpath = path.dirname(destination)
        path.os.makedirs(dirpath, mode=0o775, exist_ok=True)
        ## Relative path
        source = path.relpath(source, path.dirname(destination))
        return path.os.symlink(src=source, dst=destination)
    
    def save(self, filename, data):
        '''
        Store object in storage.
        filename: string, relative file path
        data: string or any data serializable to json.
        '''
        
        filepath = self.normalize_file_path(filename)
        
        dirpath = path.dirname(filepath)
        filename = path.basename(filename)
        path.os.makedirs(dirpath, mode=0o775, exist_ok=True)
        
        with open(filepath, 'wt', encoding='utf-8') as f:
            datatype = type(data)
            if (datatype is str):
                f.write(data)
            else:
                json.dump(data, f)
        
        return filepath
    
    def read(self, filename):
        '''
        Get data from local storage.
        filename: string file path.
        '''
        data = None
        filepath = self.normalize_file_path(filename)
        if path.isfile(filepath):
            with open(filepath, 'rt', encoding='utf-8') as f:
                data = json.load(f)
        return data
    
    def remove(self, filename):
        filepath = self.normalize_file_path(filename)
        result = False
        if path.isfile(filepath):
            result = path.os.unlink(filepath)
        return result
    
    async def aget(self, url, session=None, *args, **kwargs):
        '''
        Async version of get()
        '''
        data = None
        cache_key = None
        
        if self.__cache__ is not None:
            cache_key = self.mk_cache_key(url)
            data = await sync_to_async(self.__cache__.get)(cache_key, None)
        
        if data is None:
            ## Retrns coroutine
            ## We don't await here because of batch requests gathered at once
            result = self.get(url, session, *args, **kwargs)
            self.stderrlog('NO CACHE FOR', cache_key)
        else:
            ## Got data from cache
            if type(data) is dict:
                ## Mark that we got if from the cache
                ## to skip caching again
                data['_cached'] = True
            result = self.mkcoroutine(data)
            self.stderrlog('GOT FROM CACHE:', url);
        
        return result
    
    def get(self, url, session=None, *args, **kwargs):
        '''
        Making request to specified URL.
        Requests are cached if a cache object was passed to constructor.
        '''
        
        # if session is None:
        #     session = self.http_session()
        
        return self.request(url, session, method='get', *args, **kwargs)
    
    def post(self, url, session=None, *args, **kwargs):
        '''
        Make POST request to fediverse server
        url: string URL
        Accepts same args as requests's module "post" method.
        '''
        
        # if session is None:
        #     session = self.http_session()
        
        return self.request(url, session, method='post', *args, **kwargs)
    
    def is_coroutine(self, something):
        ## FIXME it's very stupid way, there is asyncio.iscoroutine
        return str(something).startswith('<coroutine object ')
    
    def mkcoroutine(self, result=None):
        '''
        Creates coroutine
        result: what coroutine should return
        '''
        if asyncio.iscoroutine(result):
            return result
        else:
            async def coro(result):
                return result
            return coro(result)
    
    async def gather_http_responses(self, *tasks):
        '''
        Gathers multiple async requests.
        tasks: list of tasks or coroutines
        '''
        if not len(tasks):
            return tasks
        
        #loop = asyncio.get_running_loop()
        urls = []
        #tasks = await asyncio.gather(*tasks, return_exceptions=not self.__DEBUG__)
        tasks = await asyncio.gather(*tasks, return_exceptions=True)
        if all([asyncio.iscoroutine(x) for x in tasks]):
            ## Probably all are results of self.aget()
            return await self.gather_http_responses(*tasks)
        
        for n, response in enumerate(tasks):
            while asyncio.iscoroutine(response):
                ## self.get() and self.aget() are mixed in one batch request?
                response = await response
                tasks[n] = response
                self.stderrlog('WARNING UNEXPECTED COROUTNE:', response)
            
            result = None
            
            if not hasattr(response, 'headers') and not hasattr(response, 'ok'):
                ## Not a respone object. May be a dict from cache or exception
                result = self.mkcoroutine(response)
            elif not response.ok:
                response_text = '-'
                try:
                    response_text = await response.text()
                except BaseException as e:
                    response_text = str(e)
                
                try:
                    #self.syslog(f'BAD RESPONSE FOR POST TO URL "{response.url}": "{response_text}"')
                    response.raise_for_status()
                except BaseException as e:
                    e.args = (*e.args, f'URL: {response.url}', response_text[:128])
                    result = self.mkcoroutine(e)
            elif 'content-type' in response.headers and 'application/' in response.headers['content-type'] and 'json' in response.headers['content-type']:
                ## FIXME Probably try/catch will not work here
                try:
                    result = response.json()
                except:
                    result = response.text()
            else:
                result = response.text()
            
            tasks[n] = result
            urls.append(str(getattr(response, 'url', '')))
            
            ## Calling close() if object has such method
            ## response may be an exception object. In this case we just call bool()
            #getattr(response, 'close', bool)()
        
        tasks = await asyncio.gather(*tasks, return_exceptions=True)
        
        # for response in tasks:
        #     ## Calling close() if object has such method
        #     ## response may be an exception object. In this case we just call bool()
        #     getattr(response, 'close', bool)()
        
        for n, result in enumerate(tasks):
            if isinstance(result, BaseException):
                ## We return exception as string FIXME
                tasks[n] = str(result) + ' ' + str(result.args[1:])
            elif type(result) is dict:
                ## FIXME why is it here?
                if 'type' in result and result['type'] == 'Person':
                    url = urlparse(result['id'])
                    if 'preferredUsername' in result:
                        result['user@host'] = f'{result["preferredUsername"]}@{url.hostname}'
                
                if '_cached' not in result and urls[n]:
                    self.stderrlog('SETTING CACHE:', urls[n])
                    await self.cache_set(urls[n], result)
        
        return tasks
    
    async def http_session(self, session=None):
        if not hasattr(self, '__http_session__'):
            setattr(self, '__http_session__', None)
        
        if session is not None:
            if self.__http_session__ is not None and not self.__http_session__.closed:
                await self.__http_session__.close()
            self.__http_session__ = session
        elif self.__http_session__ is None or self.__http_session__.closed:
            self.__http_session__ = aiohttp.ClientSession()
        
        return self.__http_session__
    
    def request(self, url, session, method='get', *args, **kwargs):
        '''
        Make async request to fediverse server
        url: string URL
        session: aiohttp session
        method: 'get' | 'post' | e.t.c.
        **kwargs: other kwargs that aiohttp accepts
        '''
        
        if session is None:
            ## Maybe created earlier, trying to reuse
            session = getattr(self, '__http_session__', None)
        
        if self.__headers__ is not None:
            headers = self.__headers__.copy()
            if 'headers' in kwargs:
                headers.update(kwargs['headers'])
            kwargs['headers'] = headers
        
        if url.endswith('.json.json'):
            url = url[:-len('.json')]
        
        ## Plume may return multiple values in "attributedTo"
        if type(url) is list:
            url = url[0]
        
        if 'json' in kwargs and type(kwargs['json']) is dict:
            if 'object' in kwargs['json'] and 'published' in kwargs['json']['object']:
                pub_datetime = kwargs['json']['object']['published']
                ## Removind zulu timezone indicator from the end of string
                if pub_datetime.endswith('Z'):
                    pub_datetime = pub_datetime[:-1]
                
                request_date = datetime.fromisoformat(pub_datetime)
                nowtime = datetime.now()
                
                ## more than 10 minutes
                if nowtime.timestamp() - request_date.timestamp() > 10 * 60:
                    request_date = nowtime
            else:
                request_date = datetime.now()
            
            if 'headers' not in kwargs:
                kwargs['headers'] = {}
            
            kwargs['data'] = json.dumps(kwargs['json'])
            del(kwargs['json'])
            kwargs['headers']['Date'] = emailutils.format_datetime(request_date).replace(' -0000', ' GMT')
            kwargs['headers']['Digest'] = 'SHA-256=' + b64encode(sha256(kwargs['data'].encode('utf-8')).digest()).decode('utf-8')
            kwargs['headers']['Signature'] = self.sign(url, kwargs['headers'])
        
        ## Returns coroutine
        return getattr(session, method)(url, timeout=5.0, *args, **kwargs)
    
    async def parse_tags(self, content):
        words = self._rewhitespace.split(content)
        userids = []
        links = []
        result = {
            'tag': [],
            'attachment': []
        }
        
        file_types = {
            'png':  'image/png',
            'jpg':  'image/jpeg',
            'jpeg': 'image/jpeg',
            'svg':  'image/svg',
            'gif':  'image/gif',
            'webp': 'image/webp',
            'webm': 'video/webm',
            'mp4':  'video/mp4',
            'm4a':  'audio/m4a',
            'mp3':  'audio/mpeg',
            'ogg':  'audio/ogg',
            'opus': 'audio/ogg',
            'mkv':  'video/mkv',
            'html': 'text/html',
            'txt':  'text/plain'
        }
        
        links = html.TagA.findall(content)
        
        for word in words:
            word = word.strip('@# \n\t')
            if word.startswith('https://') or word.startswith('http://'):
                if word not in links:
                    #links.append(word)
                    pass
            elif '@' in word and word.count('@') == 1:
                ## Probably user@host
                if word not in userids:
                    userids.append(word)
        
        for link in links:
            if type(link) is str:
                ## TODO need to improve hashtags parsing
                link = html.TagA(attr_href=link, name=link)
            url = urlparse(link.attr_href)
            basename = path.basename(url.path.strip('/'))
            if basename:
                link_ext = basename.split('.')[-1].lower()
                
                if 'attachment' in link.attr_rel:
                    result['attachment'].append({
                        'type': 'Document',
                        'mediaType': file_types.get(link_ext, 'application/octet-stream'),
                        'url': link.attr_href
                    })
                elif 'tag' in link.attr_rel:
                    hashtag = link.name
                    if not hashtag.startswith('#'):
                        hashtag = f'#{hashtag}'
                    result['tag'].append({
                        'href': link.attr_href,
                        'name': hashtag,
                        'type': 'Hashtag'
                    })
        
        tasks = []
        _userids = []
        for userid in userids:
            username, server = userid.split('@')
            if username and server:
                _userids.append(userid)
                tasks.append(self.aget(f'https://{server}/.well-known/webfinger?resource=acct:{userid}'))
        userids = _userids
        
        tasks = await self.gather_http_responses(*tasks)
        
        for n, response in enumerate(tasks):
            userid = userids[n]
            username, server = userid.split('@')
            userUrl = None
            if type(response) is dict:
                if 'links' in response and type(response['links']) is list:
                    for link in response['links']:
                        linkType = link.get('type', None)
                        if type(link) is dict and link.get('rel', None) == 'self' and linkType:
                            if 'application/activity' in linkType or 'json' in linkType:
                                userUrl = link.get('href', None)
                                break
            
            if userUrl:
                ## If not added to tags yet
                if (len([x for x in result['tag'] if type(x) is dict and x.get('href', None) == userUrl]) == 0):
                    result['tag'].append({
                        'href': userUrl,
                        'name': f'@{userid}',
                        'type': 'Mention'
                    })
                
                content = content.replace(userid, f'<span class="h-card"><a class="u-url mention" href="{userUrl}" rel="ugc">@<span>{username}</span></a></span>')
        
        result['content'] = content
        return result
    
    async def federate(self, activity):
        '''
        Sends activity to other instances.
        activity: dict, activity data.
        '''
        # if 'object' not in activity or 'tag' not in activity['object']:
        #     return False
        
        endpoints = []
        results = []
        
        if hasattr(self, 'federated_endpoints'):
            async for endpoint in self.federated_endpoints.aiterator():
                if endpoint.uri not in endpoints:
                    endpoints.append(endpoint.uri)
        
        if 'tag' in activity['object']:
            for tag in activity['object']['tag']:
                if tag['type'] == 'Mention':
                    results.append(self.aget(tag['href']))
        
        if len(results) > 0:
            results = await self.gather_http_responses(*results)
        
        for user in results:
            endpoint = None
            if type(user) is dict and 'endpoints' in user and 'sharedInbox' in user['endpoints']:
                endpoint = user['endpoints']['sharedInbox']
            elif 'inbox' in user:
                ## I saw instances without sharedInbox, at least Honk
                endpoint = user['inbox']
            
            if type(endpoint) is list and len(endpoint) > 0:
                ## Never saw such case but anyway...
                endpoint = endpoint[0]
            
            if endpoint:
                if endpoint not in endpoints:
                    endpoints.append(endpoint)
                if user['id'] not in activity['object']['cc'] and user['id'] not in activity['object']['to']:
                    activity['object']['cc'].append(user['id'])
        
        results = []
        activity['endpointsResults'] = []
        
        for endpoint in endpoints:
            start_ts = datetime.now().timestamp()
            results.append(self.post(endpoint, json=activity))
            diff_ts = datetime.now().timestamp() - start_ts
            self.stderrlog('REQUEST TS:', endpoint, diff_ts)
        
        start_ts = datetime.now().timestamp()
        results = await self.gather_http_responses(*results)
        diff_ts = datetime.now().timestamp() - start_ts
        self.stderrlog('REQUESTS GATHER TS:', diff_ts)
        
        for n, result in enumerate(results):
            activity['endpointsResults'].append((endpoints[n], result))
        
        ## For debug
        activity['requestEndpoints'] = endpoints
        
        return results
    
    def activity(self, **activity_upd):
        '''
        Creating an activity dict.
        Example usage: activity(object=object, type='Create', ...)
        Returns dict.
        '''
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        
        activity = {
            '@context': self.user.get('@context'),
            
            'id': path.join(self.id, 'activity', datepath, uniqid, ''),
            'type': 'Create',
            "actor": self.id,
            'published': now.isoformat() + 'Z',
            'to': [
                'https://www.w3.org/ns/activitystreams#Public',
                self.followers
            ],
            'cc': [],
            'directMessage': False
        }
        
        activity.update(activity_upd)
        
        if 'object' in activity and type(activity['object']) is dict:
            ## Using some values from object
            for k in ('actor', 'to', 'cc', 'directMessage', 'context', 'conversation'):
                if k in activity['object'] and k not in activity_upd:
                    activity[k] = activity['object'][k]
            for k in ('to', 'cc', 'actor'):
                if k not in activity_upd:
                    activity['object'][k] = activity[k]
        
        return activity
    
    async def new_status(self, replyToObj=None, **kwargs):
        '''
        Create new status.
        content: string message text
        summary: string subject (optional)
        url: string custom url (optional)
        replyToObj: dict AP object if new status is a reply to (optional)
        tags: string, space separated list of tags (hashtags or users to mention)
        '''
        
        data = {
            'type': kwargs.get('type', 'Note'),
            'attributedTo': self.id,
            'inReplyTo': kwargs.get('inReplyTo') or None,
            'content': kwargs.get('content'),
            'source': kwargs.get('content'),
            'sensitive': bool(kwargs.get('sensitive')),
            'summary': kwargs.get('summary'),
            'tag': [],
            "attachment": []
        }
        
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        data['id'] = kwargs.get('url') or path.join(self.id, 'status', datepath, uniqid, '')
        data['url'] = data['id']
        data['published'] = now.isoformat() + 'Z'
        ## Example mastodon context/conversation:
        #"context":"tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
        #"conversation": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
        data['context'] = data['conversation'] = data['id']
        
        ## If content language defined
        if 'language' in kwargs and kwargs['language']:
            data['contentMap'] = {
                kwargs['language']: data['content']
            }
        
        tasks = [self.parse_tags(data['content'])]
        if 'tags' in kwargs:
            tasks.append(self.parse_tags(kwargs['tags']))
        
        parse_results = await asyncio.gather(*tasks, return_exceptions=True)
        if type(parse_results[0]) is dict:
            data['content'] = parse_results[0].get('content')
        for parse_result in parse_results:
            if type(parse_result) is dict:
                for tag in parse_result.get('tag', []):
                    if tag not in data['tag']:
                        data['tag'].append(tag)
                for tag in parse_result.get('attachment', []):
                    if tag not in data['attachment']:
                        data['attachment'].append(tag)
        
        reply_save_path = None
        ## If we are replying to some status
        if type(replyToObj) is dict:
            attributedTo = replyToObj.get('attributedTo')
            
            if type(attributedTo) is list:
                for attr in attributedTo:
                    if type(attr) is dict and 'type' in attr and attr['type'] == 'Person':
                        attributedTo = attr['id']
                        break
            
            #if not attributedTo:
            #    raise AttributeError('Source has no "attributedTo" value.')
            
            remote_author = None
            
            if 'attributedToPerson' in replyToObj and type(replyToObj['attributedToPerson']) is dict:
                remote_author = replyToObj['attributedToPerson']
            elif attributedTo:
                remote_author, = await self.gather_http_responses(self.aget(attributedTo))
            
            if attributedTo:
                data['to'] = [
                    "https://www.w3.org/ns/activitystreams#Public",
                    attributedTo,
                    self.followers
                ]
            
            if remote_author:
                remote_author_url = urlparse(remote_author['id'])
                
                originMention = {
                    "href": remote_author.get('url', remote_author.get('id', None)),
                    "name": f"@{remote_author['preferredUsername']}@{remote_author_url.hostname}",
                    "type": "Mention"
                }
                ## Appending parent author to mentions if it isn't there yet
                if (len([x for x in data['tag'] if x.get('href', None) == originMention['href'] or x.get('name', None) == originMention['name']]) == 0):
                    data['tag'].append(originMention)
            
            ## Use context of source if exists
            #context = path.join(self.id, 'context', urlparse(status_id).path.strip('/'), '')
            data['context'] = replyToObj.get('context', replyToObj.get('conversation', replyToObj.get('id')))
            data['conversation'] = data['context']
            ## Not all engines use context though, for example Misskey not.
            
            if 'context' in data and data['context'].startswith(self.id):
                reply_save_path = data['context']
            elif 'conversation' in data and data['conversation'].startswith(self.id):
                reply_save_path = data['conversation']
            
            data['inReplyTo'] = replyToObj.get('id')
            data['directMessage'] = replyToObj.get('directMessage', False)
        
        save_path = f'{data["id"]}.json'
        activity = self.activity(object=data)
        
        ## Presave, if receiving side wants to check if status exists
        # self.save(save_path, activity)
        
        # if reply_save_path:
        #     reply_save_path = path.join(reply_save_path, path.basename(data['id'].strip('/')) + '.reply.json')
        #     self.symlink(save_path, reply_save_path)
        
        ## Send mentions
        await self.federate(activity)
        ## Resave with result
        # activity['_json'] = self.save(save_path, activity)
        return activity
    
    async def delete_status(self, activity):
        '''
        Sends Tombstone activity to federated instances.
        Returns activity dict.
        '''
        ## FIXME we get "401 unauthorized" response from mastodons.
        apobject = activity.get('object', {});
        if type(apobject) is dict:
            apobject['type'] = 'Tombstone'
        
        activity = self.activity(type='Delete', object=apobject)
        await self.federate(activity)
        return activity
    
    def sign(self, url, headers):
        '''
        Make fediverse signature.
        url: string URL
        date: string date in email format.
        '''
        parsed_url = urlparse(url)
        str2sign = '\n'.join((
            f'(request-target): post {parsed_url.path}',
            f'host: {parsed_url.netloc}',
            f'date: {headers["Date"]}',
            f'digest: {headers["Digest"]}'
        ))
        pkey = crypto.load_privatekey(crypto.FILETYPE_PEM, self.__privkey__)
        sign = b64encode(crypto.sign(pkey, str2sign, 'sha256')).decode('utf-8')
        return f'keyId="{self.__user__["publicKey"]["id"]}",algorithm="rsa-sha256",headers="(request-target) host date digest",signature="{sign}"'
    
    async def save_reply(self, activity):
        '''
        Saving incoming reply activity to json file
        activity: activity dict
        '''
        ## Backward compatibility
        ## We saved objects rather than activity in the past
        if '@context' in activity and 'object' in activity:
            apobject = activity['object']
        else:
            apobject = activity
        
        context = apobject.get('context', None)
        conversation = apobject.get('conversation', None)
        
        if context and context.startswith(self.id):
            inReplyTo = urlparse(context)
        elif conversation and conversation.startswith(self.id):
            inReplyTo = urlparse(conversation)
        else:
            inReplyTo = urlparse(apobject['inReplyTo'])
        
        author_info = None
        if 'attributedTo' in apobject:
            try:
                author_info, = await self.gather_http_responses(self.aget(apobject['attributedTo']))
            except:
                pass
            
            if author_info:
                activity['authorInfo'] = author_info
        
        activity['_save_reply'] = True
        
        savepath = path.join(inReplyTo.path, f'{self.uniqid()}.reply.json')
        self.save(savepath, activity)
        return savepath
    
    async def process_object(self, activity):
        '''
        Process object (usually activity from federated instances).
        apobject: activitypub object, dict
        '''
        
        if '@context' in activity and 'object' in activity:
            apobject = activity['object']
        else:
            ## In the past an object was being supplied
            apobject = activity
        
        result = None
        
        if 'type' in apobject:
            if apobject['type'] == 'Note':
                ## If we've received a reply message
                if apobject.get('inReplyTo', None):
                    #result = await self.save_reply(activity)
                    pass
        
        return result
    
    async def get_replies(self, object_id, content=False):
        '''
        Get replies for status
        id: string, status id.
        content: bool, if should return content too. Returns list of ids if False.
        Returns activity dict.
        '''
        replies = []
        object_id = urlparse(object_id).path.strip(' /')
        ids = []
        replies_dir = path.join(self.__datadir__, object_id)
        if path.isdir(replies_dir):
            for reply_file in path.os.listdir(replies_dir):
                if reply_file.endswith('.reply.json'):
                    reply_id = reply_file[:-len('.reply.json')]
                    reply_file = path.join(replies_dir, reply_file)
                    try:
                        with open(reply_file, 'rt') as f:
                            activity = json.load(f)
                            reply = activity
                            
                            ## Backward compatibility
                            ## We saved objects in the past rather than activity
                            if '@context' in reply and 'object' in reply:
                                reply = activity['object']
                            
                            if reply['id'] in ids:
                                continue
                            
                            ids.append(reply['id'])
                            
                            if content:
                                reply['authorInfo'] = reply.get('authorInfo', activity.get('authorInfo', None))
                                
                                if not reply['authorInfo']:
                                    if 'attributedTo' in reply:
                                        if reply['attributedTo'] == self.id:
                                            reply['authorInfo'] = {'preferredUsername': self.preferredUsername}
                                        else:
                                            try:
                                                ## Fixme: make requests at once
                                                reply['authorInfo'], = await self.gather_http_responses(self.aget(reply['attributedTo']))
                                            except:
                                                reply['authorInfo'] = {'preferredUsername': path.basename(reply['attributedTo'].strip('/'))}
                                if 'hash' not in reply:
                                    reply['hash'] = hex(abs(hash(reply['id'])))[2:]
                                if 'localId' not in reply:
                                    reply['localId'] = path.join('/', object_id, reply_id, '')
                                replies.append(reply)
                            else:
                                replies.append(reply['id'])
                    except:
                        pass
        
        if 'context/' not in object_id:
            replies.extend(await self.get_replies(path.join('context', object_id), content))
        
        if content:
            ## Sorting by published time
            replies.sort(key=lambda x: x['published'])
        
        return replies
    
    def delete_reply(self, localId):
        filepath = path.join(self.__datadir__, localId.strip(' /') + '.reply.json')
        if path.isfile(filepath):
            if path.islink(filepath):
                linkpath = path.realpath(filepath)
                if path.isfile(linkpath):
                    path.os.remove(linkpath)
            return path.os.remove(filepath)
        else:
            raise BaseException(f'Not found "{localId}"')
    
    def get_following(self):
        '''
        Returns list of persons whom we follow.
        '''
        result = []
        following_dir = path.join(self.__datadir__, 'following')
        if path.isdir(following_dir):
            for fileitem in path.os.listdir(following_dir):
                if fileitem.endswith('.json'):
                    fileitem = path.join(following_dir, fileitem)
                    try:
                        with open(fileitem, 'rt') as f:
                            result.append(json.load(f))
                    except:
                        pass
        return result
    
    async def follow(self, user_id):
        '''
        Send follow request
        user_id: fediverse user URI
        '''
        remote_author, = await self.gather_http_responses(self.aget(user_id))
        
        activity = self.activity(type='Follow', object=remote_author['id'], to=[remote_author['id']])
        
        remote_author['followRequest'] = activity
        
        activity['result'], = await self.gather_http_responses(self.post(remote_author['inbox'], json=activity))
        ## Not checking result, mastodon just replies with empty response
        filename = path.join('following', sha256(user_id.encode('utf-8')).hexdigest() + '.json')
        return self.save(filename, remote_author)
    
    async def unfollow(self, user_id):
        remote_author, = await self.gather_http_responses(self.aget(user_id))
        data = {
            'id': path.join(self.id, 'activity', self.uniqid(), ''),
            'actor': self.id,
            'object': remote_author['id'],
            'published': datetime.now().isoformat() + 'Z',
            'state': 'cancelled',
            'to': [remote_author['id']],
            'type': 'Follow'
        }
        
        activity = self.activity(object=data, type='Undo', to=[remote_author['id']])
        
        result, = await self.gather_http_responses(self.post(remote_author['inbox'], json=activity))
        
        filename = path.join('following', sha256(user_id.encode('utf-8')).hexdigest() + '.json')
        return self.remove(filename)
    
    def doWeFollow(self, user_id):
        '''
        Check if we follow user.
        user_id: fediverse user URI
        Returns activity dict or None.
        '''
        filename = path.join('following', sha256(user_id.encode('utf-8')).hexdigest() + '.json')
        return self.read(filename)
