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

class Command(BaseCommand):
    help = 'Worker process'
    _done = False
    _request = None
    _actor = None
    
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
        url = None
        site = None
        
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
        while not self._done:
            result = None
            
            if options['uri']:
                ## No infinite loop in this case
                self._done = True
                await Activity.objects.filter(processing_status=0, object_uri=options['uri']).aupdate(processing_status=10)
                qs = Activity.objects.filter(processing_status=10, object_uri=options['uri'])
            else:
                ## Marking these items for processing
                ## FIXME for multiprocessing we need to
                ## mark which process it was acquired by.
                await Activity.objects.filter(processing_status=0).aupdate(processing_status=10)
                qs = Activity.objects.filter(processing_status=10).order_by('-pk')[:100]
            
            async for activity in qs:
                self.stderr.write(
                    self.style.SUCCESS(f"DEBUG: Processing: #{activity.id} {activity}")
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
                            self.style.ERROR(f"Federating failed: #{activity.id} {activity}: {e}")
                        )
                        if self.isSqlLostException(e):
                            ## Got exception
                            ## 'Lost connection to MySQL server during query'
                            ## Will retry in next iteration
                            await sync_to_async(close_old_connections)()
                            continue
                
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
                except BaseException as e:
                    self.stderr.write(
                        self.style.ERROR(f"ERROR: Fetching root failed: #{activity.id} {activity}: {e}")
                    )
                    if self.isSqlLostException(e):
                        ## Got exception
                        ## 'Lost connection to MySQL server during query'
                        ## Will retry in next iteration
                        await sync_to_async(close_old_connections)()
                        continue
                
                is_done = True
                
                ## If is outgoing
                if not activity.incoming:
                    # activity.refresh_from_db()
                    ## Until django 5.1 it requires sync_to_async,
                    ## so using aget instead.
                    activity = await Activity.objects.aget(pk=activity.pk)
                    activity_dict = await activity.get_dict()
                    is_done = not self.isFederatingNeeded(activity_dict)
                    if not is_done:
                        self.stderr.write(
                            self.style.WARNING(f"Will retry activity: #{activity.id} {activity}")
                        )
                    
                if is_done:
                    ## Processing done
                    await Activity.objects.filter(pk=activity.pk).aupdate(processing_status=20)
                    self.stderr.write(
                        self.style.SUCCESS(f"Done activity: #{activity.id} {activity}")
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
        activity_dict = await activity.get_dict()
        activity_ts = datetime.fromisoformat(
            activity_dict.get('published', '1970-01-01').replace('Z', '')
        )
        
        if not self.isFederatingNeeded(activity_dict):
            ## Already federated or too old
            self.stderr.write(
                self.style.SUCCESS(f"DEBUG: NOT federating: #{activity.id} {activity}")
            )
            return None
        
        activity_dict = await actor.prepare_activity(activity_dict)
        activity_dict = await actor.federate(activity_dict)
        return await save_activity(request, activity_dict)
    
    @staticmethod
    def isSqlLostException(exception):
        '''Checks if exception is for 'lost connection'.
        exception: exception object.'''
        
        if hasattr(e, 'args') and len(e.args) and e.args[0] == 2013:
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
    
