from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import RequestFactory
from django.utils.html import strip_tags
from messy_fediverse.controller import fediverse_factory
from messy_fediverse.models import Group, GroupBoost
import asyncio
import aiohttp
import html
from asgiref.sync import sync_to_async
from datetime import datetime
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
        self._status_limit = None
        
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
            
            self.stdout.write(
                self.style.HTTP_INFO(
                    f'[{datetime.now():%Y-%m-%d %H:%M:%S}] '
                    f'Checking user {member.object_uri}'
                )
            )
            
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
        self.stdout.write(
            self.style.SUCCESS(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] Finished')
        )
    
    async def process_member(self, group, member, depth):
        '''
        Walk member's outbox from newest to oldest, boost roots of new comments.
        The stored last_checked_uri is the newest (most recent) comment processed
        in the last run; this run stops as soon as that frontier comment is met
        again, so already checked comments are never re-walked.
        '''
        frontier = member.meta.get('last_checked_uri', '')
        
        person = await self.fetch_ap(member.object_uri)
        if type(person) is not dict or not person.get('outbox'):
            self.stderr.write(
                self.style.WARNING(f'No outbox for {member.object_uri}, skipping')
            )
            return 0
        
        checked = 0
        first_new = ''
        seen = set()
        page = await self.get_outbox_page(person['outbox'])
        
        async def save_marker():
            if first_new:
                member.meta['last_checked_uri'] = first_new
                await sync_to_async(member.save)(update_fields=['meta'])
        
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
                
                if comment_id == frontier:
                    ## Reached the newest checked comment of the previous run,
                    ## so everything newer was handled above and everything
                    ## below is already checked - stop going deeper.
                    await save_marker()
                    return checked
                
                if comment_id in seen:
                    ## Same comment listed more than once in the collection
                    continue
                
                seen.add(comment_id)
                await self.handle_comment(group, member, comment_id)
                if not first_new:
                    first_new = comment_id
                checked += 1
            
            if checked >= depth:
                break
            
            nxt = page.get('next')
            if not nxt:
                break
            page = await self.get_outbox_page(nxt)
        
        await save_marker()
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
        Walk the inReplyTo chain up to the root message. Returns the root
        ActivityPub object dict, or None if the chain cannot be walked to a
        verifiable root (a dict with a non-empty 'id' and no parent) - in the
        latter case the boost is skipped, never boosting a mid-thread reply.
        '''
        current = comment_id
        seen = set()
        
        while current and current not in seen:
            seen.add(current)
            obj = await self.fetch_ap(current)
            if type(obj) is dict:
                if 'inReplyTo' not in obj and type(obj.get('object')) is dict:
                    ## Activity wrapper, unwrapping to its object
                    obj = obj['object']
            if type(obj) is not dict or not obj.get('id'):
                return None
            
            parent = obj.get('inReplyTo')
            if not parent:
                return obj
            
            if type(parent) is str:
                current = parent
            elif type(parent) is dict:
                ## Some implementations embed the parent object inline
                if type(parent.get('id')) is str:
                    current = parent['id']
                else:
                    return None
            else:
                return None
        
        return None
    
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
        
        reblog_id, quoted = await self.reblog(group, root_uri, root_object)
        if not reblog_id:
            return
        
        await GroupBoost.objects.acreate(
            group=group,
            root_uri=root_uri,
            comment_uri=comment_id,
            reblog_id=reblog_id
        )
        if quoted:
            self.stdout.write(
                self.style.SUCCESS(f'Quoted {root_uri} (status {reblog_id})')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Boosted {root_uri} (reblog {reblog_id})')
            )
    
    async def reblog(self, group, root_uri, root_object):
        '''
        Boost a status via the external Mastodon account REST API. When
        reblogging is not possible (author block, unknown status, ...), posts
        a quote status with a blockquote and a link to the origin instead.
        Returns (status id, quoted) or (None, False).
        '''
        base_url = group.mastodon_base_url.rstrip('/')
        headers = {
            'Authorization': f'Bearer {group.mastodon_access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        status_id, error = await self.resolve_status_id(base_url, root_uri, headers)
        if status_id:
            data, error = await self.mastodon_request('POST',
                f'{base_url}/api/v1/statuses/{status_id}/reblog', headers)
            if error is None:
                if type(data) is dict and data.get('id'):
                    return data['id'], False
                error = 'permanent'
        
        if error == 'transient-exhausted':
            ## The API is unreachable, a quote request would fail as well
            self.stderr.write(
                self.style.ERROR(f'Reblog failed for {root_uri}, giving up')
            )
            return None, False
        
        quote_id = await self.post_quote(base_url, root_uri, root_object, headers)
        if quote_id:
            return quote_id, True
        
        return None, False
    
    async def post_quote(self, base_url, root_uri, root_object, headers):
        '''
        Post a status quoting the root message: a blockquote with its plain
        text and a link to the origin. Returns the new status id or None.
        '''
        limit = await self.get_status_limit(base_url, headers)
        link = root_object.get('url')
        if type(link) is not str or not link:
            link = root_uri
        
        link_markup = f'<a href="{link}">{link}</a>'
        text = self.quote_text(root_object)
        prefix = '<blockquote>'
        suffix = '</blockquote>\n'
        available = limit - len(prefix) - len(suffix) - len(link_markup)
        
        if text and available > 3:
            if len(text) > available:
                text = self.cut_escaped(text, available - 3) + '...'
            status = f'{prefix}{text}{suffix}{link_markup}'
        else:
            ## No text or no room for it - post the origin link alone
            status = link_markup
        
        if len(status) > limit:
            self.stderr.write(
                self.style.ERROR(
                    f'Quote does not fit into {limit} chars for {root_uri}'
                )
            )
            return None
        
        data, error = await self.mastodon_request('POST',
            f'{base_url}/api/v1/statuses', headers,
            {'status': status, 'visibility': 'public'})
        if error or type(data) is not dict:
            return None
        
        return data.get('id')
    
    @staticmethod
    def quote_text(ap_object):
        '''
        Plain text of a status for quoting: strips HTML tags, unescapes
        entities and escapes the result back for HTML embedding.
        '''
        raw = ap_object.get('content')
        if type(raw) is not str or not raw.strip():
            raw = ap_object.get('name') or ap_object.get('summary') or ''
        if not raw:
            return ''
        
        text = html.unescape(strip_tags(raw.replace('<', ' <')))
        text = ' '.join(text.split())
        return html.escape(text, quote=False)
    
    @staticmethod
    def cut_escaped(text, limit):
        '''
        Cut an HTML escaped string without breaking an entity in half.
        '''
        cut = text[:limit]
        amp = cut.rfind('&')
        if amp != -1 and amp > cut.rfind(';'):
            cut = cut[:amp]
        return cut
    
    async def get_status_limit(self, base_url, headers):
        '''
        Maximum note length configured on the Mastodon instance.
        Cached for the command run, defaults to 500.
        '''
        if self._status_limit:
            return self._status_limit
        
        data, error = await self.mastodon_request('GET',
            f'{base_url}/api/v2/instance', headers)
        limit = 500
        if error is None and type(data) is dict:
            try:
                limit = int(data['configuration']['statuses']['max_characters'])
            except (KeyError, TypeError, ValueError):
                limit = 500
        
        self._status_limit = limit
        return limit
    
    async def resolve_status_id(self, base_url, root_uri, headers):
        '''
        Resolve the numeric id of a (possibly remote) status via the
        Mastodon REST API using a resolve=true search - this actively
        fetches unknown remote statuses and reports them as statuses[0].
        Returns (status id, error) where error mirrors mastodon_request
        ('permanent' also for an empty search result).
        '''
        params = {
            'q': root_uri, 'resolve': 'true', 'type': 'statuses', 'limit': '1'
        }
        data, error = await self.mastodon_request('GET',
            f'{base_url}/api/v2/search?{urlencode(params)}',
            headers)
        if error:
            return None, error
        
        statuses = data.get('statuses') if type(data) is dict else None
        if statuses and type(statuses[0]) is dict and statuses[0].get('id'):
            return statuses[0]['id'], None
        
        self.stderr.write(
            self.style.ERROR(f'Status not resolvable for {root_uri}')
        )
        return None, 'permanent'
    
    async def mastodon_request(self, method, url, headers, payload=None, retries=3):
        '''
        Perform a Mastodon API request with retries on transient failures
        (network errors, HTTP 429, HTTP 5xx). Returns (data, error) where
        error is None on success, 'permanent' on a non-retriable HTTP 4xx
        and 'transient-exhausted' when retries are used up.
        '''
        for attempt in range(retries):
            if attempt:
                await asyncio.sleep(2 * attempt)
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method, url, headers=headers, json=payload
                    ) as response:
                        if response.status >= 400:
                            text = await response.text()
                            self.stderr.write(
                                self.style.ERROR(
                                    f'Mastodon API {method} {response.status} '
                                    f'for {url}: {text[:400]}'
                                )
                            )
                            if response.status != 429 and response.status < 500:
                                return None, 'permanent'
                            continue
                        data = await response.json()
            except BaseException as e:
                self.stderr.write(
                    self.style.WARNING(
                        f'Mastodon API {method} failed for {url} '
                        f'(attempt {attempt + 1}/{retries}): {e}'
                    )
                )
                continue
            
            return data, None
        
        return None, 'transient-exhausted'
    
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

