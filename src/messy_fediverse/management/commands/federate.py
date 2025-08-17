from django.core.management.base import BaseCommand, CommandError
from messy_fediverse import controller
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import RequestFactory
import asyncio
import json

class Command(BaseCommand):
    help = 'Federates activity'
    
    def add_arguments(self, parser):
        # Optional string argument
        parser.add_argument(
            '--domain',
            type=str,
            help='Actor domain'
        )
        
        parser.add_argument(
            '--json',
            type=str,
            help='Path to JSON file of activity to federate'
        )
        
        parser.add_argument(
            '--output-json',
            type=str,
            help='Save result to this json file'
        )
    
    def handle(self, *args, **options):
        url = None
        site = None
        
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
        actor = controller.fediverse_factory(request)
        result = None
        
        if options['json']:
            with open(options['json'], 'rb') as f:
                activity_dict = json.load(f)
                activity = actor.activity(activity)
                activity = asyncio.run(actor.prepare_activity(activity))
                result = asyncio.run(actor.federate(activity))
        
        if options['output_json']:
            with open(options['output_json'], 'wb') as f:
                json.dump(result, f)
        
        self.stdout.write(
            self.style.SUCCESS(f"Federated: {result}")
        )
