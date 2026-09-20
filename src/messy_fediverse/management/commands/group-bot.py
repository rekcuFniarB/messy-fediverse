from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import RequestFactory
from messy_fediverse.controller import fediverse_factory
from messy_fediverse.models import Group, GroupBoost
import asyncio
import aiohttp
from asgiref.sync import sync_to_async
from urllib.parse import urlencode

## Neutral User-Agent used by default for anonymous fetches.
NEUTRAL_USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

class Command(BaseCommand):
    help = ('Group bot: watches persons in the group tag, finds roots of threads '
            'they comment in and boosts those roots via the external Mastodon account.')
    
    def add_arguments(self, parser):
        parser.add_argument(
            'name',
            type=str,
            help='Group name (matches Tag name)'
        )
        
        parser.add_argument(
            '--depth',
            type=int,
            default=10,
            help='How deep into comments history to go (default 10)'
        )
        
        parser.add_argument(
            '--signed',
            action='store_true',
            help='Fetch with our actor HTTP signature instead of anonymously (default is anonymous)'
        )
        
        parser.add_argument(
            '--user-agent',
            type=str,
            help='User-Agent for anonymous fetches (neutral default is used if omitted)'
        )
        
        parser.add_argument(
            '--domain',
            type=str,
            help='Actor domain (required in --signed mode)'
        )
        
        parser.add_argument(
            '--sleep',
            type=int,
            help='Wait seconds between members'
        )
        
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (more verbose messages)'
        )
    
    def handle(self, *args, **options):
        if options['depth'] < 1:
            raise CommandError('--depth value should be at least 1')
        
        if options['debug']:
            settings.DEBUG = True
        
        self._signed = options['signed']
        self._user_agent = options['user_agent']
        self._actor = None
        
        if self._signed:
            site = None
            if options['domain']:
                if hasattr(settings, 'HOSTS_URLCONF'):
                    urlconf = settings.HOSTS_URLCONF.get(options['domain'], None)
                    if urlconf:
                        settings.ROOT_URLCONF = urlconf
                
                site = Site.objects.get(domain=options['domain'])
            
            request_factory = RequestFactory()
            request = request_factory.get('/social/interact/', secure=True)
            request.site = site
            self._actor = fediverse_factory(request)
        
        return asyncio.run(self.ahandle(**options))
    
    async def ahandle(self, *args, **options):
        name = options['name']
        depth = options['depth']
        
        group = await (
            Group.objects.filter(name=name).select_related('tag').afirst()
        )
        if not group:
            raise CommandError(f'Group "{name}" not found')
        if not group.enabled:
            raise CommandError(f'Group "{name}" is disabled')
        
        member_qs = group.tag.items.filter(object_type='Person')
        total_checked = 0
        
        async for member in member_qs:
            if options['sleep']:
                await asyncio.sleep(options['sleep'])
            
            try:
                checked = await self.process_member(group, member, depth)
            except BaseException as e:
                self.stderr.write(
                    self.style.ERROR(f'Member {member.object_uri} failed: {e}')
                )
                continue
            
            total_checked += checked
            self.stdout.write(
                self.style.SUCCESS(
                    f'Group "{group.name}": member {member.object_uri}: {checked} comments checked'
                )
            )
        
        self.stdout.write(
            self.style.SUCCESS(f'Group "{group.name}": done, {total_checked} comments processed')
        )
    
    async def process_member(self, group, member, depth):
        '''
        Walk member's outbox from newest to oldest, boost roots of checked comments.
        Stops when a comment already checked in previous run is reached.
        '''
        last_checked = member.meta.get('last_checked_uri', '')
        
        person = await self.fetch_ap(member.object_uri)
        if type(person) is not dict or not person.get('outbox'):
            self.stderr.write(
                self.style.WARNING(f'No outbox for {member.object_uri}, skipping')
            )
            return 0
        
        checked = 0
        page = await self.get_outbox_page(person['outbox'])
        
        while page is not None and checked < depth:
            items = page.get('orderedItems') or []
            
            for item in items:
                if checked >= depth:
                    break
                
                ap_object = item
                if type(item) is dict:
                    obj = item.get('object')
                    if type(obj) is dict:
                        ap_object = obj
                
                if type(ap_object) is not dict:
                    continue
                
                comment_id = ap_object.get('id')
                if not comment_id or not ap_object.get('inReplyTo'):
                    ## Not a comment, skipping
                    continue
                
                if comment_id == last_checked:
                    ## Reached a comment already checked in previous run
                    return checked
                
                await self.handle_comment(group, member, comment_id)
                
                member.meta['last_checked_uri'] = comment_id
                await sync_to_async(member.save)(update_fields=['meta'])
                checked += 1
            
            if checked >= depth:
                break
            
            nxt = page.get('next')
            if not nxt:
                break
            page = await self.get_outbox_page(nxt)
        
        return checked
    
    async def fetch_ap(self, url):
        '''
        Fetch ActivityPub data. In --signed mode uses our actor (HTTP signature),
        otherwise an anonymous request with a neutral User-Agent.
        Returns dict, str or None.
        '''
        if self._signed:
            return await self._actor.aget(url)
        
        headers = {
            'Accept': 'application/activity+json',
            'User-Agent': self._user_agent or NEUTRAL_USER_AGENT
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status >= 400:
                        self.stderr.write(
                            self.style.WARNING(f'Fetch failed {response.status} for {url}')
                        )
                        return None
                    
                    content_type = response.headers.get('content-type', '')
                    if 'json' in content_type:
                        return await response.json()
                    return await response.text()
        except BaseException as e:
            self.stderr.write(
                self.style.WARNING(f'Fetch failed for {url}: {e}')
            )
            return None
    
    async def get_outbox_page(self, url):
        '''
        Fetch outbox collection or page. Returns dict with 'orderedItems'
        or None. Accepts a URL string or an already fetched dict.
        '''
        data = url
        if type(url) is str:
            data = await self.fetch_ap(url)
        
        if type(data) is not dict:
            return None
        
        if 'orderedItems' in data:
            return data
        
        first = data.get('first')
        if type(first) is dict:
            return first
        
        if type(first) is str:
            data = await self.fetch_ap(first)
            if type(data) is dict:
                return data
        
        return None
    
    async def find_root(self, comment_id):
        '''
        Walk the inReplyTo chain up to the root message.
        Returns the root ActivityPub object dict or None.
        '''
        current = comment_id
        root = None
        seen = set()
        
        while current and current not in seen:
            seen.add(current)
            obj = await self.fetch_ap(current)
            if type(obj) is dict:
                if 'inReplyTo' not in obj and type(obj.get('object')) is dict:
                    ## Activity wrapper, unwrapping to its object
                    obj = obj['object']
            if type(obj) is not dict:
                break
            root = obj
            current = obj.get('inReplyTo')
        
        return root
    
    async def handle_comment(self, group, member, comment_id):
        '''
        Find root of the thread and boost it if needed.
        '''
        root_object = await self.find_root(comment_id)
        if type(root_object) is not dict or not root_object.get('id'):
            self.stderr.write(
                self.style.WARNING(f'Could not find root for {comment_id}')
            )
            return
        
        root_uri = root_object['id']
        root_author = self.get_attributed_to(root_object)
        
        if root_author == member.object_uri:
            ## Root message is by the comment owner itself
            self.stdout.write(
                self.style.WARNING(f'Root author is the member, skipping {root_uri}')
            )
            return
        
        already = await (
            GroupBoost.objects.filter(group=group, root_uri=root_uri).aexists()
        )
        if already:
            self.stdout.write(
                self.style.WARNING(f'Root already boosted: {root_uri}')
            )
            return
        
        reblog_id = await self.reblog(group, root_uri)
        if not reblog_id:
            return
        
        await GroupBoost.objects.acreate(
            group=group,
            root_uri=root_uri,
            comment_uri=comment_id,
            reblog_id=reblog_id
        )
        self.stdout.write(
            self.style.SUCCESS(f'Boosted {root_uri} (reblog {reblog_id})')
        )
    
    async def reblog(self, group, root_uri):
        '''
        Boost (reblog) a status via the external Mastodon account REST API.
        Returns the reblog status id or None.
        '''
        base_url = group.mastodon_base_url.rstrip('/')
        headers = {
            'Authorization': f'Bearer {group.mastodon_access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        status_id = await self.resolve_status_id(base_url, root_uri, headers)
        if not status_id:
            self.stderr.write(
                self.style.ERROR(f'No status id for {root_uri}, cannot reblog')
            )
            return None
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{base_url}/api/v1/statuses/{status_id}/reblog',
                headers=headers
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    self.stderr.write(
                        self.style.ERROR(
                            f'Reblog failed {response.status}: {text[:400]}'
                        )
                    )
                    return None
                data = await response.json()
        
        if type(data) is dict:
            return data.get('id')
        
        return None
    
    async def resolve_status_id(self, base_url, root_uri, headers):
        '''
        Resolve the numeric id of a (possibly remote) status via the
        Mastodon REST API using a resolve=true search - this actively
        fetches unknown remote statuses and reports them as statuses[0].
        '''
        data = await self.api_get(base_url, root_uri, headers,
            '/api/v2/search', {
                'q': root_uri, 'resolve': 'true', 'type': 'statuses', 'limit': '1'
            })
        if type(data) is dict:
            statuses = data.get('statuses') or []
            if statuses and type(statuses[0]) is dict and statuses[0].get('id'):
                return statuses[0]['id']
        
        return None
    
    async def api_get(self, base_url, root_uri, headers, path, params):
        '''
        Perform a GET request against the Mastodon API and return parsed JSON.
        Logs failures (status code + short body) for later diagnosis.
        '''
        url = f'{base_url}{path}?{urlencode(params)}'
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status >= 400:
                        text = await response.text()
                        self.stderr.write(
                            self.style.ERROR(
                                f'Status lookup failed {response.status} '
                                f'for {root_uri}: {text[:200]}'
                            )
                        )
                        return None
                    data = await response.json()
        except BaseException as e:
            self.stderr.write(
                self.style.ERROR(f'Status lookup failed for {root_uri}: {e}')
            )
            return None
        
        return data
    
    @staticmethod
    def get_attributed_to(ap_object):
        '''
        Normalize the 'attributedTo' value of an ActivityPub object
        to a single actor uri string.
        '''
        attributed = ap_object.get('attributedTo', '')
        
        if type(attributed) is str:
            return attributed
        
        if type(attributed) is list:
            for item in attributed:
                if type(item) is str:
                    return item
                if type(item) is dict and item.get('type') == 'Person':
                    return item.get('id', '')
        
        if type(attributed) is dict:
            return attributed.get('id', '')
        
        return ''

