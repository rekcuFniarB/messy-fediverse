from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from messy_fediverse.controller import Replies, save_activity, fediverse_factory
from messy_fediverse.models import Activity
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import RequestFactory
import asyncio
from asgiref.sync import sync_to_async
from time import sleep
from datetime import datetime
import os
from urllib.parse import urlparse
# import tracemalloc

class Command(BaseCommand):
    help = 'Worker process'
    _done = False
    _request = None
    _actor = None
    _pid = 0
    _limit = 100
    
    def add_arguments(self, parser):
        # Optional string argument
        parser.add_argument(
            '--domain',
            type=str,
            help='Actor domain'
        )
        
        parser.add_argument(
            '--uri',
            type=str,
            help='Process specific activity of given URI'
        )
        
        parser.add_argument(
            '--sleep',
            type=int,
            help='Wait seconds between requests'
        )
        
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (more verbose messages)'
        )
        
    
    def handle(self, *args, **options):
        self._pid = os.getpid()
        url = None
        site = None
        ## For debugging forgotten to await.
        # tracemalloc.start()
        
        if options['debug']:
            settings.DEBUG = True
        
        ## Switching urlconf based on domain
        if options['domain']:
            if hasattr(settings, 'HOSTS_URLCONF'):
                urlconf = settings.HOSTS_URLCONF.get(options['domain'], None)
                if urlconf:
                    settings.ROOT_URLCONF = urlconf
            
            site = Site.objects.get(domain=options['domain'])
        
        request_factory = RequestFactory()
        self._request = request_factory.get('/social/interact/', secure=True)
        self._request.site = site
        ## FIXME for multiuser instance actor should
        ## be different.
        self._actor = fediverse_factory(self._request)
        
        return asyncio.run(self.ahandle(**options))
    
    async def ahandle(self, *args, **options):
        last_id = 0
        cycles = 0
        found = False
        
        while not self._done:
            result = None
            
            if found:
                cycles += 1
                if cycles > self._limit * 10:
                    cycles = 0
                    ## Forcing last_id recheck
                    last_id = 0
            
            if options['uri']:
                ## No infinite loop in this case
                self._done = True
                await (
                    Activity.objects
                        .filter(processing_status=0, object_uri=options['uri'])
                        .aupdate(processing_status=self._pid)
                )
                qs = Activity.objects.filter(processing_status=self._pid, object_uri=options['uri'])
            else:
                ## Last ID used for optimization
                ## so that we don't scan the whole table
                ## on every cycle.
                if not last_id:
                    last_id = (
                        await Activity.objects
                            .filter(processing_status=0)
                            .order_by('pk')
                            .values_list('pk', flat=True)
                            .afirst() or 0
                    )
                    if not last_id:
                        last_id = (
                            await Activity.objects
                            .order_by('-pk')
                            .values_list('pk', flat=True)
                            .afirst() or 0
                        )
                    last_id = max(1, last_id - self._limit * 5)
                
                ## Marking these items for processing
                target_ids = (
                    Activity.objects
                        .filter(processing_status=0, pk__gte=last_id)
                        .order_by('-pk')
                        .values_list('pk', flat=True)[:self._limit]
                )
                target_ids = [pk async for pk in target_ids]
                await (
                    Activity.objects
                        .filter(processing_status=0, pk__in=target_ids)
                        .aupdate(processing_status=self._pid)
                )
                qs = Activity.objects.filter(processing_status=self._pid).order_by('pk')
            
            found = False
            async for activity in qs:
                found = True
                
                self.stderr.write(
                    self.style.SUCCESS(f"DEBUG: Processing: #{activity.pk} {activity}")
                )
                
                ## If is outgoing
                if not activity.incoming:
                    try:
                        result = await self.federate(self._request, self._actor, activity)
                        self.stderr.write(
                            self.style.SUCCESS(f"DEBUG: Federated activity: {result}")
                        )
                    except BaseException as e:
                        self.stderr.write(
                            self.style.ERROR(f"Federating failed: #{activity.pk} {activity}: {e}")
                        )
                        if self.isSqlLostException(e):
                            ## Got exception
                            ## 'Lost connection to MySQL server during query'
                            ## Will retry in next iteration
                            await sync_to_async(close_old_connections)()
                            continue
                        else:
                            raise
                
                try:
                    result = await Replies.fetch_parents(self._request, activity.object_uri)
                    if (
                        type(result) is dict
                        and 'root' in result
                        and type(result['root']) is dict
                        and 'object' in result['root']
                        and type(result['root']['object']) is dict
                        and 'id' in result['root']['object']
                    ):
                        result = result['root']['object']['id']
                        self.stderr.write(
                            self.style.SUCCESS(f"DEBUG: Fetched root: {result}")
                        )
                    else:
                        self.stderr.write(
                            self.style.WARNING(f"ERROR: couldn't fetch root for: {activity.object_uri}")
                        )
                
                except BaseException as e:
                    self.stderr.write(
                        self.style.ERROR(f"ERROR: Fetching root failed: #{activity.pk} {activity}: {e}")
                    )
                    if self.isSqlLostException(e):
                        ## Got exception
                        ## 'Lost connection to MySQL server during query'
                        ## Will retry in next iteration
                        await sync_to_async(close_old_connections)()
                        continue
                    else:
                        raise
                
                ## If is incoming and pending signature verification
                if activity.activity_data is None:
                    activity.activity_data = {}
                if activity.incoming and activity.activity_data.get('_verified') == 'pending':
                    verified = await self.verify_incoming_signature(self._request, self._actor, activity)
                    if verified:
                        activity.activity_data['_verified'] = 'signature'
                        activity.disabled = False
                    else:
                        ## Try fetching object from origin instance
                        try:
                            obj = await self._actor.aget(activity.object_uri)
                            if type(obj) is dict:
                                activity.activity_data['_verified'] = 'fetch'
                                activity.disabled = False
                            else:
                                activity.disabled = True
                        except BaseException:
                            activity.disabled = True
                    
                    await sync_to_async(activity.save)()
                
                is_done = True
                
                ## If is outgoing
                if not activity.incoming:
                    # activity.refresh_from_db()
                    ## Until django 5.1 it requires sync_to_async,
                    ## so using aget instead.
                    activity = await Activity.objects.aget(pk=activity.pk)
                    activity_dict = activity.get_dict()
                    is_done = not self.isFederatingNeeded(activity_dict)
                    if not is_done:
                        self.stderr.write(
                            self.style.WARNING(f"Will retry activity: #{activity.pk} {activity}")
                        )
                
                if is_done:
                    ## Processing done
                    await Activity.objects.filter(pk=activity.pk).aupdate(processing_status=-self._pid)
                    self.stderr.write(
                        self.style.SUCCESS(f"Done activity: #{activity.pk} {activity}")
                    )
                
                self.stderr.flush()
                
                if options['sleep']:
                    await asyncio.sleep(options['sleep'])
            
            await asyncio.sleep((options['sleep'] or 1) + 1)
    
    async def federate(self, request, actor, activity):
        '''Federate job function.
        request: django request object.
        actor: fediverse.FediverseActor instance.
        activity: models.Activity instance.
        '''
        now = datetime.now()
        activity_dict = activity.get_dict()
        activity_ts = datetime.fromisoformat(
            activity_dict.get('published', '1970-01-01').replace('Z', '')
        )
        
        if not self.isFederatingNeeded(activity_dict):
            ## Already federated or too old
            self.stderr.write(
                self.style.SUCCESS(f"DEBUG: NOT federating: #{activity.pk} {activity}")
            )
            return None
        
        activity_dict = await actor.prepare_activity(activity_dict)
        activity_dict = await actor.federate(activity_dict)
        return await save_activity(request, activity_dict)
    
    async def verify_incoming_signature(self, request, actor, activity):
        '''
        Verify ActivityPub HTTP Signature for a pending incoming activity.
        Returns True if signature is valid, False otherwise.
        '''
        meta = activity.activity_data.get('_requestMeta', {}) if activity.activity_data else {}
        if not meta:
            return False
        
        signature_header = meta.get('HTTP_SIGNATURE', '')
        if not signature_header:
            return False
        
        ## Parse signature header
        signature = {}
        for kvs in signature_header.split(','):
            kv = kvs.strip().split('=', 1)
            if len(kv) < 2:
                kv.append('')
            signature[kv[0].strip()] = kv[1].strip().strip('"')
        
        for k in ('keyId', 'headers', 'signature'):
            if k not in signature:
                return False
        
        headers = [h.strip() for h in signature['headers'].split(' ') if h.strip()]
        
        ## Reconstruct string to sign
        path = urlparse(activity.object_uri).path if activity.object_uri else ''
        query_string = meta.get('QUERY_STRING', '')
        if query_string:
            full_path = path + '?' + query_string
        else:
            full_path = path
        
        str2sign = []
        for h in headers:
            if h == '(request-target)':
                v = f'post {full_path}'
            else:
                meta_key = 'HTTP_' + h.upper().replace('-', '_')
                v = meta.get(meta_key, '')
            str2sign.append(f'{h}: {v}')
        
        str2sign = '\n'.join(str2sign)
        
        ## Fetch actor public key
        actor_uri = signature.get('keyId', '')
        actor_info = await actor.aget(actor_uri)
        if type(actor_info) is not dict:
            return False
        
        actor_key = actor_info.get('publicKey', {})
        if not actor_key or actor_key.get('id') != actor_uri:
            return False
        
        public_key_pem = actor_key.get('publicKeyPem', '')
        if not public_key_pem:
            return False
        
        ## Verify signature
        try:
            result = await actor.crypt_verify(str2sign, signature['signature'], public_key_pem)
            return result is None
        except BaseException:
            return False
    
    @staticmethod
    def isSqlLostException(exception):
        '''Checks if exception is for 'lost connection'.
        exception: exception object.'''
        
        if hasattr(exception, 'args') and len(exception.args) and exception.args[0] == 2013:
            return True
        return False
    
    @staticmethod
    def isFederatingNeeded(activity_dict):
        '''Check if should retry to federate
        activity_dict: dict'''
        if (
            '_requestAttempt' not in activity_dict
            or (
                ## There are failed requests and not attempt done
                activity_dict['_requestAttempt'] < 3
                and len(activity_dict.get('_failedRequests', {}))
            )
        ):
            return True
        
        return False
