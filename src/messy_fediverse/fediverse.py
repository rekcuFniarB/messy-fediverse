import requests
from os import path
from datetime import datetime
import json
from urllib.parse import urlparse
from email import utils as emailutils
from base64 import b64encode
from OpenSSL import crypto
from hashlib import sha256

class Fediverse:
    def __init__(self, user, privkey, pubkey, headers=None, cache=None):
        '''
        :cache object: optional cache object used for ceching requests
        '''
        self.__cache = cache
        self.__sentinel = object()
        self.__headers = headers
        self.user = user
        self.__privkey = privkey
        self.__pubken = pubkey
    
    def uniqid(self):
        return hex(int(str(datetime.now().timestamp()).replace('.', '')))[2:]
        
    
    def get(self, url, *args, **kwargs):
        '''
        Making request to specified URL.
        Requests are cached if a cache object was passed to constructor.
        '''
        data = None
        
        if self.__cache is not None:
            data = self.__cache.get(url, None)
        
        if data is None:
            if self.__headers is not None:
                headers = self.__headers.copy()
                if 'headers' in kwargs:
                    headers.update(kwargs['headers'])
                kwargs['headers'] = headers
            
            if url.endswith('.json.json'):
                url = url[:-len('.json')]
            
            r = requests.get(url, *args, **kwargs)
            if not r.ok:
                r.raise_for_status()
            if 'application/' in r.headers['content-type'] and 'json' in r.headers['content-type']:
                data = r.json()
            else:
                data = r.content
            
            if self.__cache is not None and data:
                self.__cache.set(url, data)
        
        return data
    
    def post(self, url, *args, **kwargs):
        '''
        Make POST request to fediverse server
        url: string URL
        Accepts same args as requests's module "post" method.
        '''
        
        if self.__headers is not None:
            headers = self.__headers.copy()
            if 'headers' in kwargs:
                headers.update(kwargs['headers'])
            kwargs['headers'] = headers
        
        if url.endswith('.json.json'):
            url = url[:-len('.json')]
        
        if 'json' in kwargs and type(kwargs['json']) is dict:
            if 'object' in kwargs['json'] and 'published' in kwargs['json']['object']:
                if 'headers' not in kwargs:
                    kwargs['headers'] = {}
                request_date = emailutils.format_datetime(datetime.fromisoformat(kwargs['json']['object']['published'])).replace(' -0000', ' GMT')
                kwargs['headers']['Date'] = request_date
                kwargs['headers']['Digest'] = 'sha-256=' + sha256(json.dumps(kwargs['json']).encode('utf-8')).hexdigest()
                kwargs['headers']['Signature'] = self.sign(url, kwargs['headers'])
        
        r = requests.post(url, *args, **kwargs)
        if not r.ok:
            try:
                r.raise_for_status()
            except BaseException as e:
                if r.content and e.args:
                    e.args = (*e.args, r.content.decode('utf-8'))
                raise e
        
        if 'application/' in r.headers['content-type'] and 'json' in r.headers['content-type']:
            data = r.json()
        else:
            data = r.content
        
        return data
    
    def reply(self, source, message):
        '''
        Send "reply" request to remote fediverse server.
        source: dict, sending source information.
        message: string
        Returns string or dict if response was JSON.
        '''
        remote_author = self.get(source['attributedTo'] + '.json')
        
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        
        data = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": path.join(self.user['id'], 'activity', datepath, uniqid, ''),
            "type": "Create",
            "actor": self.user['id'],
            
            "object": {
                "id": path.join(self.user['id'], 'status', datepath, uniqid, ''),
                "type": "Note",
                "published": now.isoformat(timespec='seconds'),
                "attributedTo": self.user['id'],
                "inReplyTo": source['id'],
                "content": message,
                "to": "https://www.w3.org/ns/activitystreams#Public"
            }
        }
        
        data['result'] = self.post(remote_author['endpoints']['sharedInbox'], json=data)
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
        pkey = crypto.load_privatekey(crypto.FILETYPE_PEM, self.__privkey)
        sign = b64encode(crypto.sign(pkey, str2sign, 'sha256')).decode('utf-8')
        return f'keyId="{self.user["publicKey"]["id"]}",headers="(request-target) host date digest", signature="{sign}"'
