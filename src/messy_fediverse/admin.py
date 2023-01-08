from django.contrib import admin
from .models import FederatedEndpoint, Activity, Follower
from .controller import fediverse_factory, stderrlog
from asgiref.sync import sync_to_async, async_to_sync

class FollowerAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        '''
        Executed before model's save()
        '''
        result = None
        
        ## obj._loaded_values provided by model's custom from_db()
        if not form.cleaned_data['disabled'] and form.cleaned_data['accepted']:
            if not obj._loaded_values['accepted']:
                result = async_to_sync(self.send_accept_follow, force_new_loop=True)(request, obj)
        
        return super().save_model(request, obj, form, change)
    
    async def send_accept_follow(self, request, follower):
        fediverse = fediverse_factory(request)
        session = await fediverse.http_session()
        
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
            fediverse.post(actorInfo['endpoints']['sharedInbox'], session, json=acceptActivity)
        )
        stderrlog('RESPONSE:', response, '\nACTIVITY:', acceptActivity)
        return response
    
admin.site.register(FederatedEndpoint)
admin.site.register(Activity)
admin.site.register(Follower, FollowerAdmin)
