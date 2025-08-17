from django.core.management.base import BaseCommand, CommandError
from messy_fediverse.controller import Replies, save_activity, fediverse_factory
from messy_fediverse.models import Activity
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import RequestFactory
import asyncio
from time import sleep
from datetime import datetime

class Command(BaseCommand):
    help = 'Worker process'
    done = False
    
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
            help='Process specific URI'
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
        request = request_factory.get('/social/interact/', secure=True)
        request.site = site
        actor = fediverse_factory(request)
        result = None
        qs = None
        
        ## We want to process one specified activity
        if options['uri']:
            qs = Activity.objects.filter(uri=options['uri'])
        
        while not self.done:
            if options['uri']:
                ## No infinite loop in this case
                self.done = True
                result = asyncio.run(Replies.fetch_parents(request, options['uri']))
                self.stderr.write(
                    self.style.SUCCESS(f"DEBUG: Fetched root: {result}")
                )
                qs = Activity.objects.filter(object_uri=options['uri'])
            else:
                ## Marking these items for processing
                Activity.objects.filter(processing_status=0).update(processing_status=10)
                qs = Activity.objects.filter(processing_status=10).order_by('-pk')[:100]
            
            # for activity in qs.iterator(chunk_size=100):
            for activity in qs:
                self.stderr.write(
                    self.style.SUCCESS(f"DEBUG: Processing: #{activity.id}.{activity.incoming} {activity}")
                )
                
                try:
                    result = asyncio.run(Replies.fetch_parents(request, activity.object_uri))
                    self.stderr.write(
                        self.style.SUCCESS(f"DEBUG: Fetched root: {result}")
                    )
                except BaseException as e:
                    self.stderr.write(
                        self.style.ERROR(f"ERROR: Processing failed: #{activity.id}.{activity.incoming} {activity}: {e}")
                    )
                
                ## If is outgoing
                if activity.incoming == 0:
                    try:
                        result = asyncio.run(self.federate(request, actor, activity))
                        self.stderr.write(
                            self.style.SUCCESS(f"DEBUG: Federated activity: {result}")
                        )
                    except BaseException as e:
                        self.stderr.write(
                            self.style.ERROR(f"Processing failed: #{activity.id}.{activity.incoming} {activity}: {e}")
                        )
                
                activity.processing_status = 20
                activity.save()
                
                self.stderr.write(
                    self.style.SUCCESS(f"Done activity: {activity.id} {activity}")
                )
                
                self.stderr.flush()
                
                if options['sleep']:
                    sleep(options['sleep'])
            
            sleep(options['sleep'] or 1)
    
    async def federate(self, request, actor, activity):
        now = datetime.now()
        activity_dict = await activity.get_dict()
        activity_ts = datetime.fromisoformat(
            activity_dict.get('published', '1970-01-01').replace('Z', '')
        )
        if (
            'failedRequests' in activity_dict
            or '_failedRequests' in activity_dict
            or 'endpointsResults' in activity_dict
            or '_endpointsResults' in activity_dict
            or '_response' in activity_dict
            or now.timestamp() - activity_ts.timestamp() > 60 * 60 * 24 * 90
        ):
            ## Already federated or too old
            self.stderr.write(
                self.style.SUCCESS(f"DEBUG: NOT federating: {activity}")
            )
            return activity_dict
        
        activity_dict = await actor.prepare_activity(activity_dict)
        activity_dict = await actor.federate(activity_dict)
        return await save_activity(request, activity_dict)
