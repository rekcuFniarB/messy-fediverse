import requests
from os import path
from datetime import datetime
import json
from urllib.parse import urlparse
from email import utils as emailutils
import base64
from OpenSSL import crypto

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
                url = url.replace('.json.json', '.json')
            
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
        if self.__headers is not None:
            headers = self.__headers.copy()
            if 'headers' in kwargs:
                headers.update(kwargs['headers'])
            kwargs['headers'] = headers
        
        if url.endswith('.json.json'):
            url = url.replace('.json.json', '.json')
        
        if 'json' in kwargs and type(kwargs['json']) is dict:
            if 'object' in kwargs['json'] and 'published' in kwargs['json']['object']:
                if 'headers' not in kwargs:
                    kwargs['headers'] = {}
                kwargs['headers']['signature'] = self.sign(url, datetime.fromisoformat(kwargs['json']['object']['published']))
                #kwargs['json']['object']['published'] = kwargs['json']['object']['published'].isoformat(timespec='seconds')
        
        r = requests.post(url, *args, **kwargs)
        if not r.ok:
            r.raise_for_status()
        if 'application/' in r.headers['content-type'] and 'json' in r.headers['content-type']:
            data = r.json()
        else:
            data = r.content
        
        return data
    
    def reply(self, source, message):
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
    
    def sign(self, url, date):
        parsed_url = urlparse(url)
        str2sign = '\n'.join((
            f'(request-target): post {parsed_url.path}',
            f'host: {parsed_url.netloc}',
            f'date: {emailutils.formatdate(date.timestamp())}'
        ))
        pkey = crypto.load_privatekey(crypto.FILETYPE_PEM, self.__privkey)
        sign = base64.b64encode(crypto.sign(pkey, str2sign, 'sha256'))
        return f'keyId="{self.user["publicKey"]["id"]}",headers="(request-target) host date", signature="{sign}"'
