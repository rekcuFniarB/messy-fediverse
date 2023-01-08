from django.contrib import admin
from .models import FederatedEndpoint, Activity, Follower
from .controller import fediverse_factory, stderrlog
from asgiref.sync import sync_to_async, async_to_sync
import aiohttp
# import asyncio

class FollowerAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        '''
        Executed before model's save()
        '''
        result = None
        
        ## obj._loaded_values provided by model's custom from_db()
        if not form.cleaned_data['disabled'] and form.cleaned_data['accepted']:
            if not obj._loaded_values['accepted']:
                fediverse = fediverse_factory(request)
                try:
                    result = async_to_sync(self.send_accept_follow)(fediverse, obj)
                except BaseException as e:
                    # if e.args[0] == 'Event loop is closed':
                    #     loop = asyncio.new_event_loop()
                    #     stderrlog('NEW LOOP:', loop)
                    #     result = async_to_sync(self.send_accept_follow)(fediverse, obj)
                    #     # result = asyncio.run(self.send_accept_follow(request, obj))
                    # else:
                    #     raise
                    raise
                
                # result = asyncio.run(self.send_accept_follow(fediverse, obj))
                stderrlog('SAVE RESULT:', result)
                ## FIXME check response and discard "accepted" if request failed.
        
        return super().save_model(request, obj, form, change)
    
    async def send_accept_follow(self, fediverse, follower):
        response = None
        async with aiohttp.ClientSession() as session:
            #fediverse = fediverse_factory(request)
            # session = await fediverse.http_session()
            await fediverse.http_session(session)
            
            ## FIXME succeedes only once, second time it returns "loop closed" error
            actorInfo, = await fediverse.gather_http_responses(
                fediverse.get(follower.uri, session=session)
            )
            
            if type(actorInfo) is not dict:
                if isinstance(actorInfo, BaseException):
                    raise actorInfo
                elif type(actorInfo) is str:
                    raise TypeError(actorInfo)
                else:
                    raise TypeError('Bad actor info:', type(actorInfo), str(actorInfo))
            
            ## Getting activityPub object dict
            apobject = follower.activity.get_dict().get('object', None)
            acceptActivity = fediverse.activity(
                type='Accept',
                object=apobject,
                to=[actorInfo['id']]
            )
            response, = await fediverse.gather_http_responses(
                # fediverse.post(actorInfo['endpoints']['sharedInbox'], session, json=acceptActivity)
                fediverse.post(actorInfo['inbox'], session, json=acceptActivity)
            )
        
        return response
    
admin.site.register(FederatedEndpoint)
admin.site.register(Activity)
admin.site.register(Follower, FollowerAdmin)
