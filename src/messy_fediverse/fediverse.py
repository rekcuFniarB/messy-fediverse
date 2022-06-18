import requests
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
    
    def syslog(self, msg):
        if self.__DEBUG__:
            syslog.syslog(syslog.LOG_INFO, f'MESSY SOCIAL: {msg}')
    
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
    
    def get(self, url, *args, **kwargs):
        '''
        Making request to specified URL.
        Requests are cached if a cache object was passed to constructor.
        '''
        data = None
        
        ## Plume may return multiple values in "attributedTo"
        if type(url) is list:
            url = url[0]
        
        if self.__cache__ is not None:
            data = self.__cache__.get(url, None)
        
        if data is None:
            if self.__headers__ is not None:
                headers = self.__headers__.copy()
                if 'headers' in kwargs:
                    headers.update(kwargs['headers'])
                kwargs['headers'] = headers
            
            if url.endswith('.json.json'):
                url = url[:-len('.json')]
            
            r = requests.get(url, *args, **kwargs)
            
            if not r.ok:
                try:
                    self.syslog(f'BAD RESPONSE FOR GET FROM URL "{url}": "{r.text}"')
                    r.raise_for_status()
                except BaseException as e:
                    if r.text and e.args:
                        e.args = (*e.args, r.text)
                    raise e
            
            if 'application/' in r.headers['content-type'] and 'json' in r.headers['content-type']:
                try:
                    data = r.json()
                except:
                    data = r.text
            else:
                data = r.text
            
            if self.__cache__ is not None and data:
                self.__cache__.set(url, data)
        
        return data
    
    def post(self, url, *args, **kwargs):
        '''
        Make POST request to fediverse server
        url: string URL
        Accepts same args as requests's module "post" method.
        '''
        
        if self.__headers__ is not None:
            headers = self.__headers__.copy()
            if 'headers' in kwargs:
                headers.update(kwargs['headers'])
            kwargs['headers'] = headers
        
        if url.endswith('.json.json'):
            url = url[:-len('.json')]
        
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
        
        r = requests.post(url, *args, **kwargs)
        if not r.ok:
            try:
                self.syslog(f'BAD RESPONSE FOR POST TO URL "{url}": "{r.text}"')
                r.raise_for_status()
            except BaseException as e:
                if r.text and e.args:
                    e.args = (*e.args, r.text)
                raise e
        
        if 'application/' in r.headers['content-type'] and 'json' in r.headers['content-type']:
            try:
                data = r.json()
            except:
                data = r.text
        else:
            data = r.text
        
        return data
    
    def parse_tags(self, content):
        words = self._rewhitespace.split(content)
        userids = []
        links = []
        result = {
            'tag': []
        }
        for word in words:
            word = word.strip('@#')
            if '@' in word:
                ## Probably user@host
                if word not in userids:
                    userids.append(word)
            elif word.startswith('https://') or word.startswith('http://'):
                if word not in links:
                    links.append(word)
        for link in links:
            url = urlparse(link)
            name = path.basename(url.path.strip('/'))
            if name:
                result['tag'].append({
                    'href': link,
                    'name': f'#{name}',
                    'type': 'Hashtag'
                })
                content = content.replace(link, f'<a href="{link}" class="mention hashtag" rel="tag">#<span>{name}</span></a></p>')
        for userid in userids:
            username, server = userid.split('@')
            if username and server:
                try:
                    ## FIXME consider using async requests
                    webfinger = self.get(f'https://{server}/.well-known/webfinger?resource=acct:{userid}')
                    if type(webfinger) is dict and 'aliases' in webfinger and type(webfinger['aliases']) is list and len(webfinger['aliases']) > 0:
                        result['tag'].append({
                            'href': webfinger['aliases'][0],
                            'name': f'@{userid}',
                            'type': 'Mention'
                        })
                        content = content.replace(userid, f'<span class="h-card"><a class="u-url mention" href="{webfinger["aliases"][0]}" rel="ugc">@<span>{username}</span></a></span>')
                except:
                    pass
        
        result['content'] = content
        return result
    
    def mention(self, activity):
        '''
        Sends copy of status to all mentioned users.
        activity: dict, activity data.
        '''
        endpoints = []
        for tag in activity['object']['tag']:
            if tag['type'] == 'Mention':
                try:
                    user = self.get(tag['href'])
                    if user and 'endpoints' in user and 'sharedInbox' in user['endpoints']:
                        if user['endpoints']['sharedInbox'] not in endpoints:
                            endpoints.append(user['endpoints']['sharedInbox'])
                        if user['id'] not in activity['object']['cc'] and user['id'] not in activity['object']['to']:
                            activity['object']['to'].append(user['id'])
                except:
                    pass
        
        ## FIXME Using "to" because Pleroma doesn't show notifications for "cc"
        ## maybe will revert in future
        activity['to'] = activity['object']['to']
        
        results = []
        activity['object']['mentionResults'] = []
        
        for endpoint in endpoints:
            try:
                result = self.post(endpoint, json=activity)
                results.append(result)
                activity['object']['mentionResults'].append((endpoint, result))
            except BaseException as e:
                activity['object']['mentionResults'].append((endpoint, e.__str__()))
                pass
        
        return results
    
    def new_status(self, message, subject='', url=''):
        '''
        Create new status.
        message: string message text
        '''
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        
        data = {
            '@context': self.user.get('@context'),
            
            'id': path.join(self.__user__['id'], 'activity', datepath, uniqid, ''),
            'type': 'Create',
            "actor": self.__user__['id'],
            'published': now.isoformat() + 'Z',
            'to': [
                'https://www.w3.org/ns/activitystreams#Public',
                self.followers
            ],
            "cc": [],
            "directMessage": False,
            ## FIXME WTF
            ## https://socialhub.activitypub.rocks/t/context-vs-conversation/578/4
            #"context": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
            #"context_id": 2320494,
            "context": path.join(self.id, 'status', datepath, uniqid, ''),
            
            'object': {
                'id': path.join(self.id, 'status', datepath, uniqid, ''),
                'type': "Note",
                'actor': self.id,
                'url': url or path.join(self.id, 'status', datepath, uniqid, ''),
                'published': now.isoformat() + 'Z',
                'attributedTo': self.id,
                'inReplyTo': None,
                ## FIXME WTF
                #"context":"tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
                #"conversation": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
                'context': path.join(self.id, 'status', datepath, uniqid, ''),
                'content': message,
                'source': message,
                'senstive': None,
                'summary': subject,
                'to': [
                    'https://www.w3.org/ns/activitystreams#Public',
                    self.followers
                ],
                'cc': [],
                'tag': [],
                "attachment": []
            }
        }
        
        parse_result = self.parse_tags(data['object']['content'])
        data['object']['content'] = parse_result['content']
        data['object']['tag'].extend(parse_result['tag'])
        
        ## Presave, if receiving side wants to check if status exists
        self.save(data['object']['id'] + '.json', data['object'])
        ## Send mentions
        self.mention(data)
        ## Resave with result
        self.save(data['object']['id'] + '.json', data['object'])
        return data
    
    def reply(self, source, message, subject='', url=''):
        '''
        Send "reply" request to remote fediverse server.
        source: dict, sending source information.
        message: string
        Returns string or dict if response was JSON.
        '''
        remote_author = self.get(source['attributedTo'])
        remote_author_url = urlparse(remote_author['id'])
        
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        
        data = {
            "@context": self.user.get('@context'),
            
            "id": path.join(self.__user__['id'], 'activity', datepath, uniqid, ''),
            "type": "Create",
            "actor": self.__user__['id'],
            "published": now.isoformat() + 'Z',
            "to": [
                source.get('attributedTo', None),
                "https://www.w3.org/ns/activitystreams#Public"
            ],
            "cc": [],
            "directMessage": False,
            ## FIXME WTF
            ## https://socialhub.activitypub.rocks/t/context-vs-conversation/578/4
            #"context": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
            #"context_id": 2320494,
            "context": source.get('context', None),
            
            "object": {
                "id": path.join(self.__user__['id'], 'status', datepath, uniqid, ''),
                "type": "Note",
                "actor": self.__user__['id'],
                "url": url or path.join(self.__user__['id'], 'status', datepath, uniqid, ''),
                "published": now.isoformat() + 'Z',
                "attributedTo": self.__user__['id'],
                "inReplyTo": source['id'],
                ## FIXME WTF
                #"context":"tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
                #"conversation": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
                "context": source.get('context', path.join(self.__user__['id'], 'status', datepath, uniqid, '')),
                "content": message,
                "source": message,
                "senstive": None,
                "summary": subject,
                "to": [
                    source.get('attributedTo', None),
                    "https://www.w3.org/ns/activitystreams#Public"
                ],
                "cc": [],
                "tag": [
                    #{
                    #    "href": "https://mastodon.ml/users/rf",
                    #    "name": "@rf@mastodon.ml",
                    #    "type": "Mention"
                    #},
                    {
                        "href": remote_author.get('url', remote_author.get('id', None)),
                        "name": f"@{remote_author['preferredUsername']}@{remote_author_url.hostname}",
                        "type": "Mention"
                    }
                ],
                "attachment": []
            }
        }
        
        parse_result = self.parse_tags(data['object']['content'])
        data['object']['content'] = parse_result['content']
        data['object']['tag'].extend(parse_result['tag'])
        
        ## Presave, if receiving side wants to check if status exists
        self.save(data['object']['id'] + '.json', data['object'])
        self.mention(data)
        ## Resave with result
        self.save(data['object']['id'] + '.json', data['object'])
        return data
    
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
    
    def save_reply(self, apobject):
        inReplyTo = urlparse(apobject['inReplyTo'])
        savepath = path.join(inReplyTo.path, f'{self.uniqid()}.reply.json')
        self.save(savepath, apobject)
        return savepath
    
    def process_object(self, apobject):
        '''
        Process object.
        apobject: activity pub object, dict
        '''
        
        result = None
        
        if 'type' in apobject:
            if apobject['type'] == 'Note':
                if 'inReplyTo' in apobject and apobject['inReplyTo']:
                    result = self.save_reply(apobject)
        
        return result
    
    def get_replies(self, id, content=False):
        '''
        Get replies for status
        id: string, status id.
        content: bool, if should return content too. Rurns list of ids if False.
        Returns activity dict.
        '''
        replies = []
        
        replies_dir = path.join(self.__datadir__, id.strip(' /'))
        if path.isdir(replies_dir):
            for reply_file in path.os.listdir(replies_dir):
                if reply_file.endswith('.reply.json'):
                    reply_file = path.join(replies_dir, reply_file)
                    try:
                        with open(reply_file, 'rt') as f:
                            reply = json.load(f)
                            if content:
                                replies.append(reply)
                            else:
                                replies.append(reply['id'])
                    except:
                        pass
        
        return replies
    
    def get_following(self):
        '''
        Returns list of persons who we follow.
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
    
    def follow(self, user_id):
        '''
        Send follow request
        user_id: fediverse user URI
        '''
        remote_author = self.get(user_id)
        data = {
            '@context': self.user.get('@context'),
            'id': path.join(self.id, 'activity', self.uniqid(), ''),
            'type': 'Follow',
            'actor': self.id,
            'object': remote_author['id'],
            'to': [remote_author['id']],
            'cc': [],
            'published': datetime.now().isoformat() + 'Z'
        }
        
        remote_author['followRequest'] = data
        
        data['result'] = self.post(remote_author['inbox'], json=data)
        ## Not checking result, mastodon just replies with empty response
        filename = path.join('following', sha256(user_id.encode('utf-8')).hexdigest() + '.json')
        return self.save(filename, remote_author)
    
    def unfollow(self, user_id):
        remote_author = self.get(user_id)
        data = {
            '@context': self.user.get('@context'),
            'id': path.join(self.id, 'activity', self.uniqid(), ''),
            'type': 'Undo',
            'published': datetime.now().isoformat() + 'Z',
            'actor': self.id,
            'object': {
                'id': path.join(self.id, 'activity', self.uniqid(), ''),
                'actor': self.id,
                'object': remote_author['id'],
                'published': datetime.now().isoformat() + 'Z',
                'state': 'cancelled',
                'to': [remote_author['id']],
                'type': 'Follow'
            },
            'to': [remote_author['id']],
            'cc': []
        }
        
        result = self.post(remote_author['inbox'], json=data)
        
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
