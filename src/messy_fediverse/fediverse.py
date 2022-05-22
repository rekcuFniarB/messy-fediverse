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
    def __init__(self, user, privkey, pubkey, headers=None, cache=None, debug=False):
        '''
        :cache object: optional cache object used for ceching requests
        '''
        self.__cache__ = cache
        self.__sentinel__ = object()
        self.__headers__ = headers
        self.__user__ = user
        self.__privkey__ = privkey
        self.__pubkey__ = pubkey
        self.__DEBUG__ = debug
    
    def uniqid(self):
        return hex(int(str(datetime.now().timestamp()).replace('.', '')))[2:]
    
    @property
    def user(self):
        return self.__user__
    
    def get(self, url, *args, **kwargs):
        '''
        Making request to specified URL.
        Requests are cached if a cache object was passed to constructor.
        '''
        data = None
        
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
        
        uniqid = self.uniqid()
        now = datetime.now()
        datepath = now.date().isoformat().replace('-', '/')
        
        data = {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": path.join(self.__user__['id'], 'activity', datepath, uniqid, ''),
            "type": "Create",
            "actor": self.__user__['id'],
            
            "object": {
                "id": path.join(self.__user__['id'], 'status', datepath, uniqid, ''),
                "type": "Note",
                "published": now.isoformat(timespec='seconds') + 'Z',
                "attributedTo": self.__user__['id'],
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
        pkey = crypto.load_privatekey(crypto.FILETYPE_PEM, self.__privkey__)
        sign = b64encode(crypto.sign(pkey, str2sign, 'sha256')).decode('utf-8')
        return f'keyId="{self.__user__["publicKey"]["id"]}",algorithm="rsa-sha256",headers="(request-target) host date digest", signature="{sign}"'
