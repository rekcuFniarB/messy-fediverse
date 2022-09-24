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
from . import html

class Fediverse:
    def __init__(self, user, privkey, pubkey, headers=None, datadir='/tmp', cache=None, debug=False):
        '''
        :cache object: optional cache object used for ceching requests
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
    
    def cache_set(self, name, value):
        if self.__cache__ is not None:
            if hasattr(value, 'result') and callable(value.result):
                ## For future like objects
                value = value.result()
                if type(value) is list:
                    value = value[0]
                
                ## FIXME cannot reuse already awaited coroutine
            return self.__cache__.set(name, value)
    
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
                filename = '/'.join(filenameparts)
        
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
    
    def get(self, url, session=None, *args, **kwargs):
        '''
        Making request to specified URL.
        Requests are cached if a cache object was passed to constructor.
        '''
        
        if session is None:
            session = self.http_session()
        
        return self.request(url, session, method='get', *args, **kwargs)
    
    def post(self, url, session=None, *args, **kwargs):
        '''
        Make POST request to fediverse server
        url: string URL
        Accepts same args as requests's module "post" method.
        '''
        
        if session is None:
            session = self.http_session()
        
        return self.request(url, session, method='post', *args, **kwargs)
    
    def is_coroutine(self, something):
        return str(something).startswith('<coroutine object ')
    
    def mkcoroutine(self, result=None):
        '''
        Creates coroutine
        result: what coroutine should return
        '''
        if self.is_coroutine(result):
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
        #loop = asyncio.get_running_loop()
        
        #tasks = await asyncio.gather(*tasks, return_exceptions=not self.__DEBUG__)
        tasks = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        
        for response in tasks:
            result = None
            if isinstance(response, BaseException):
                ## For gather
                result = self.mkcoroutine(response)
            elif not response.ok:
                #response_text = await response.text()
                try:
                    #self.syslog(f'BAD RESPONSE FOR POST TO URL "{response.url}": "{response_text}"')
                    response.raise_for_status()
                except BaseException as e:
                    e.args = (*e.args, f'URL: {response.url}')
                    result = self.mkcoroutine(e)
            elif 'application/' in response.headers['content-type'] and 'json' in response.headers['content-type']:
                ## FIXME Probably try/catch will not work here
                try:
                    result = response.json()
                except:
                    result = response.text()
            else:
                result = response.text()
            
            results.append(result)
        
        results = await asyncio.gather(*results, return_exceptions=True)
        
        for response in tasks:
            ## Calling close() if object has such method
            ## response may be an exception object. In this case we just call bool()
            getattr(response, 'close', bool)()
        
        for n, result in enumerate(results):
            if isinstance(result, BaseException):
                ## We return exception as string FIXME
                results[n] = str(result)
            elif type(result) is dict:
                if 'type' in result and result['type'] == 'Person':
                    url = urlparse(result['id'])
                    if 'preferredUsername' in result:
                        result['user@host'] = f'{result["preferredUsername"]}@{url.hostname}'
        
        return results
    
    def http_session(self, session=None):
        if not hasattr(self, '__http_session__'):
            setattr(self, '__http_session__', None)
        
        if session is not None:
            if self.__http_session__ is not None and not self.__http_session__.closed:
                ## FIXME RuntimeWarning: coroutine 'ClientSession.close' was never awaited
                self.__http_session__.close()
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
        
        if self.__headers__ is not None:
            headers = self.__headers__.copy()
            if 'headers' in kwargs:
                headers.update(kwargs['headers'])
            kwargs['headers'] = headers
        
        if url.endswith('.json.json'):
            url = url[:-len('.json')]
        
        data = None
        
        ## Plume may return multiple values in "attributedTo"
        if type(url) is list:
            url = url[0]
        
        #if self.__cache__ is not None and method == 'get':
            #data = self.__cache__.get(url, None)
        
        ## If got result from the cache
        if data is not None:
            return self.mkcoroutine(data)
            #return data
        
        if 'json' in kwargs and type(kwargs['json']) is dict:
            if 'object' in kwargs['json'] and 'published' in kwargs['json']['object']:
                pub_datetime = kwargs['json']['object']['published']
            else:
                pub_datetime = datetime.now().isoformat()
            
            if 'headers' not in kwargs:
                kwargs['headers'] = {}
            
            ## Removind zulu timezone indicator from the end of string
            if pub_datetime.endswith('Z'):
                pub_datetime = pub_datetime[:-1]
            
            request_date = emailutils.format_datetime(datetime.fromisoformat(pub_datetime)).replace(' -0000', ' GMT')
            kwargs['data'] = json.dumps(kwargs['json'])
            del(kwargs['json'])
            kwargs['headers']['Date'] = request_date
            kwargs['headers']['Digest'] = 'sha-256=' + b64encode(sha256(kwargs['data'].encode('utf-8')).digest()).decode('utf-8')
            kwargs['headers']['Signature'] = self.sign(url, kwargs['headers'])
        
        ## Returns coroutine
        result_coro = getattr(session, method)(url, timeout=10.0, *args, **kwargs)
        
        ## FIXME need workaround for cache support
        #if method == 'get':
            #response = self.gather_http_responses(result_coro)
            #task = asyncio.create_task(response)
            #task.add_done_callback(partial(self.cache_set, url))
            #return asyncio.create_task(result_coro)
        
        return result_coro
    
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
        
        for word in words:
            word = word.strip('@# \n\t')
            if word.startswith('https://') or word.startswith('http://'):
                if word not in links:
                    #links.append(word)
                    pass
            elif '@' in word:
                ## Probably user@host
                if word not in userids:
                    userids.append(word)
        
        links = html.TagA.findall(content)
        
        for link in links:
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
                tasks.append(self.get(f'https://{server}/.well-known/webfinger?resource=acct:{userid}'))
        userids = _userids
        
        tasks = await self.gather_http_responses(*tasks)
        
        for n, response in enumerate(tasks):
            userid = userids[n]
            username, server = userid.split('@')
            
            if type(response) is dict and 'aliases' in response and type(response['aliases']) is list and len(response['aliases']) > 0:
                if (len([x for x in result['tag'] if type(x) is dict and x.get('href', None) == response['aliases'][0]]) == 0):
                    result['tag'].append({
                        'href': response['aliases'][0],
                        'name': f'@{userid}',
                        'type': 'Mention'
                    })
                
                content = content.replace(userid, f'<span class="h-card"><a class="u-url mention" href="{response["aliases"][0]}" rel="ugc">@<span>{username}</span></a></span>')
        
        result['content'] = content
        return result
    
    async def mention(self, activity):
        '''
        Sends copy of status to all mentioned users.
        activity: dict, activity data.
        '''
        if 'object' not in activity or 'tag' not in activity['object']:
            return False
        
        endpoints = []
        results = []
        for tag in activity['object']['tag']:
            if tag['type'] == 'Mention':
                results.append(self.get(tag['href']))
        results = await self.gather_http_responses(*results)
        
        for user in results:
            if type(user) is dict and 'endpoints' in user and 'sharedInbox' in user['endpoints']:
                if user['endpoints']['sharedInbox'] not in endpoints:
                    endpoints.append(user['endpoints']['sharedInbox'])
                if user['id'] not in activity['object']['cc'] and user['id'] not in activity['object']['to']:
                    activity['object']['cc'].append(user['id'])
        
        activity['cc'] = activity['object']['cc']
        
        results = []
        activity['object']['mentionResults'] = []
        
        for endpoint in endpoints:
            results.append(self.post(endpoint, json=activity))
        
        results = await self.gather_http_responses(*results)
        for n, result in enumerate(results):
            activity['object']['mentionResults'].append((endpoints[n], result))
        
        ## For debug
        activity['object']['mentionEndpoints'] = endpoints
        
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
            for k in activity['object']:
                if k in ('actor', 'to', 'cc', 'directMessage', 'context', 'conversation'):
                    activity[k] = activity['object'][k]
        
        return activity
    
    async def new_status(self, message, subject='', url=None):
        '''
        Create new status.
        message: string message text
        '''
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        status_id = url or path.join(self.id, 'status', datepath, uniqid, '')
        context = path.join(self.id, 'context', urlparse(status_id).path.strip('/'), '')
        
        data = {
            'id': status_id,
            'type': "Note",
            'actor': self.id,
            'url': status_id,
            'published': now.isoformat() + 'Z',
            'attributedTo': self.id,
            'inReplyTo': None,
            ## FIXME WTF
            #"context":"tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
            #"conversation": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
            'context': context,
            'conversation': context,
            'content': message,
            'source': message,
            'sensitive': False,
            'summary': subject,
            'to': [
                'https://www.w3.org/ns/activitystreams#Public',
                self.followers
            ],
            'cc': [],
            'tag': [],
            "attachment": []
        }
        
        parse_result = await self.parse_tags(data['content'])
        data['content'] = parse_result['content']
        data['tag'].extend(parse_result['tag'])
        data['attachment'].extend(parse_result['attachment'])
        
        ## Presave, if receiving side wants to check if status exists
        self.save(data['id'] + '.json', data)
        activity = self.activity(object=data)
        ## Send mentions
        await self.mention(activity)
        ## Resave with result
        self.save(data['id'] + '.json', data)
        return activity
    
    async def reply(self, source, message, subject='', url=None):
        '''
        Send "reply" request to remote fediverse server.
        source: dict, sending source information.
        message: string
        Returns string or dict if response was JSON.
        '''
        attributedTo = ''
        
        if type(source['attributedTo']) is list:
            for attr in source['attributedTo']:
                if type(attr) is dict and 'type' in attr and attr['type'] == 'Person':
                    attributedTo = attr['id']
                    break
        else:
            attributedTo = source['attributedTo']
        
        if not attributedTo:
            raise AttributeError('Source has no "attributedTo" value.')
        
        remote_author, = await self.gather_http_responses(self.get(attributedTo))
        remote_author_url = urlparse(remote_author['id'])
        
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        status_id = url or path.join(self.id, 'status', datepath, uniqid, '')
        ## Use context of source if exists
        context = path.join(self.id, 'context', urlparse(status_id).path.strip('/'), '')
        context = source.get('context', source.get('conversation', context))
        
        
        data = {
            "id": status_id,
            "type": "Note",
            "actor": self.id,
            "url": status_id,
            "published": now.isoformat() + 'Z',
            "attributedTo": self.id,
            "inReplyTo": source['id'],
            ## FIXME WTF
            #"context":"tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
            #"conversation": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
            'context': context,
            'conversation': context,
            "content": message,
            "source": message,
            "sensitive": False,
            "summary": subject,
            "to": [
                source.get('attributedTo', None),
                "https://www.w3.org/ns/activitystreams#Public"
            ],
            "cc": [],
            "tag": [],
            "attachment": []
        }
        
        parse_result = await self.parse_tags(data['content'])
        data['content'] = parse_result['content']
        data['tag'].extend(parse_result['tag'])
        originMention = {
            "href": remote_author.get('url', remote_author.get('id', None)),
            "name": f"@{remote_author['preferredUsername']}@{remote_author_url.hostname}",
            "type": "Mention"
        }
        ## Appending parent author to mentions
        if (len([x for x in data['tag'] if x.get('href', None) == originMention['href']]) == 0):
            data['tag'].append(originMention)
        
        data['attachment'].extend(parse_result['attachment'])
        
        save_path = f'{data["id"]}.json'
        
        reply_save_path = None
        if 'context' in data and data['context'].startswith(self.id):
            reply_save_path = data['context']
        elif 'conversation' in data and data['conversation'].startswith(self.id):
            reply_save_path = data['conversation']
        
        ## Presave, if receiving side wants to check if status exists
        self.save(save_path, data)
        
        if reply_save_path:
            reply_save_path = path.join(reply_save_path, path.basename(data['id'].strip('/')) + '.reply.json')
            self.symlink(save_path, reply_save_path)
        
        activity = self.activity(object=data)
        ## Send mentions
        await self.mention(activity)
        ## Resave with result
        self.save(save_path, data)
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
    
    async def save_reply(self, apobject):
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
                author_info, = await self.gather_http_responses(self.get(apobject['attributedTo']))
            except:
                pass
            
            if author_info:
                apobject['authorInfo'] = author_info
        
        savepath = path.join(inReplyTo.path, f'{self.uniqid()}.reply.json')
        self.save(savepath, apobject)
        return savepath
    
    async def process_object(self, apobject):
        '''
        Process object (usually activity from federated instances).
        apobject: activitypub object, dict
        '''
        
        result = None
        
        if 'type' in apobject:
            if apobject['type'] == 'Note':
                ## If we've received a reply message
                if apobject.get('inReplyTo', None):
                    result = await self.save_reply(apobject)
        
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
                            reply = json.load(f)
                            
                            if reply['id'] in ids:
                                continue
                            
                            ids.append(reply['id'])
                            
                            if content:
                                if 'authorInfo' not in reply:
                                    if 'attributedTo' in reply:
                                        if reply['attributedTo'] == self.id:
                                            reply['authorInfo'] = {'preferredUsername': self.preferredUsername}
                                        else:
                                            try:
                                                reply['authorInfo'], = await self.gather_http_responses(self.get(reply['attributedTo']))
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
        remote_author, = await self.gather_http_responses(self.get(user_id))
        
        activity = self.activity(type='Follow', object=remote_author['id'], to=[remote_author['id']])
        
        remote_author['followRequest'] = activity
        
        activity['result'], = await self.gather_http_responses(self.post(remote_author['inbox'], json=activity))
        ## Not checking result, mastodon just replies with empty response
        filename = path.join('following', sha256(user_id.encode('utf-8')).hexdigest() + '.json')
        return self.save(filename, remote_author)
    
    async def unfollow(self, user_id):
        remote_author, = await self.gather_http_responses(self.get(user_id))
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
