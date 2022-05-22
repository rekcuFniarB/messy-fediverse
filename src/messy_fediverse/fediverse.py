import requests
from os import path
from datetime import datetime
import json
from urllib.parse import urlparse
from email import utils as emailutils
from base64 import b64encode
from OpenSSL import crypto
from hashlib import sha256
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
    
    def uniqid(self):
        return hex(int(str(datetime.now().timestamp()).replace('.', '')))[2:]
    
    def syslog(self, msg):
        if self.__DEBUG__:
            syslog.syslog(syslog.LOG_INFO, f'MESSY SOCIAL: {msg}')
    
    @property
    def user(self):
        return self.__user__
    
    def save(self, filename, data):
        ## Fixing filename
        if filename.startswith('http://') or filename.startswith('https://'):
            filename = urlparse(filename).path
        filename = filename.lstrip('/') ## removing leading slash
        if filename.endswith('.json.json'):
            filename = filename[:-len('.json')]
        if filename.endswith('/.json'):
            filename = filename[:-len('/.json')] + '.json'
        filepath = path.join(self.__datadir__, filename)
        dirpath = path.dirname(filepath)
        filename = path.basename(filename)
        path.os.makedirs(dirpath, mode=0o775, exist_ok=True)
        
        with open(filepath, 'wt', encoding='utf-8') as f:
            datatype = type(data)
            if (datatype is str):
                f.write(data)
            else:
                json.dump(data, f)
    
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
                if 'headers' not in kwargs:
                    kwargs['headers'] = {}
                
                pub_datetime = kwargs['json']['object']['published']
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
                if r.content and e.args:
                    e.args = (*e.args, r.content.decode('utf-8'))
                raise e
        
        if 'application/' in r.headers['content-type'] and 'json' in r.headers['content-type']:
            try:
                data = r.json()
            except:
                data = r.text
        else:
            data = r.text
        
        return data
    
    def reply(self, source, message):
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
            #"@context": [
            #    "https://www.w3.org/ns/activitystreams",
            #    "https://w3id.org/security/v1",
            #    staticurl(request, 'messy/fediverse/usercontext.json'),
            #    {"@language": "und"}
            #],
            
            "@context": self.user.get('@context'),
            
            #"@context": [
            #    "https://www.w3.org/ns/activitystreams",
            #    {
            #        "ostatus": "http://ostatus.org#",
            #        "atomUri": "ostatus:atomUri",
            #        "inReplyToAtomUri": "ostatus:inReplyToAtomUri",
            #        "conversation": "ostatus:conversation",
            #        "sensitive": "as:sensitive",
            #        "toot": "http://joinmastodon.org/ns#",
            #        "votersCount": "toot:votersCount"
            #    }
            #],
            
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
                "url": path.join(self.__user__['id'], 'status', datepath, uniqid, ''),
                "published": now.isoformat() + 'Z',
                "attributedTo": self.__user__['id'],
                "inReplyTo": source['id'],
                ## FIXME WTF
                #"context":"tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
                #"conversation": "tag:mastodon.ml,2022-05-21:objectId=9633346:objectType=Conversation",
                "context": source.get('context', None),
                "content": message,
                "source": message,
                "senstive": None,
                "summary": "",
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
                        "name": f"@{remote_author['name']}@{remote_author_url.hostname}",
                        "type": "Mention"
                    }
                ],
                "attachment": []
            }
        }
        
        ## Presave, if receiving side wants to check if status exists
        self.save(data['object']['id'] + '.json', data['object'])
        data['object']['result'] = self.post(remote_author['endpoints']['sharedInbox'], json=data)
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
        return f'keyId="{self.__user__["publicKey"]["id"]}",algorithm="rsa-sha256",headers="(request-target) host date digest", signature="{sign}"'
