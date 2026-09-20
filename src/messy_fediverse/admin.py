from django.contrib import admin
from .models import FederatedEndpoint, Activity, Follower, Tag, TaggedObject
from .controller import fediverse_factory, save_activity, send_accept_follow, add_task
from .middleware import stderrlog
from asgiref.sync import sync_to_async, async_to_sync
import aiohttp
# import asyncio

class FollowerAdmin(admin.ModelAdmin):
    raw_id_fields = ['activity']
    
    def save_model(self, request, obj, form, change):
        '''
        Executed before model's save()
        '''
        result = None
        
        ## obj._loaded_values provided by model's custom from_db()
        if not form.cleaned_data['disabled'] and form.cleaned_data['accepted']:
            if hasattr(obj, '_loaded_values'):
                if not obj._loaded_values['accepted']:
                    try:
                        ## Manually accepting follow request from admin interface
                        ## This should send accept activity request
                        acceptActvityCoro = send_accept_follow(request, obj)
                        ## Will run after current request finished
                        add_task(request, acceptActvityCoro)
                    except BaseException as e:
                        # if e.args[0] == 'Event loop is closed':
                        #     loop = asyncio.new_event_loop()
                        #     stderrlog('NEW LOOP:', loop)
                        #     result = async_to_sync(self.send_accept_follow)(fediverse, obj)
                        #     # result = asyncio.run(self.send_accept_follow(request, obj))
                        # else:
                        #     raise
                        raise
                    
                    ## FIXME check response and discard "accepted" if request failed.
            else:
                ## There is no _loaded_values when creating new
                form.cleaned_data['accepted'] = False
        
        return super().save_model(request, obj, form, change)
    
class TaggedObjectInline(admin.TabularInline):
    model = TaggedObject
    extra = 0
    raw_id_fields = ['user']

class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'user', 'created_at')
    list_filter = ('user',)
    search_fields = ('name', 'title')
    raw_id_fields = ['user']
    inlines = [TaggedObjectInline]

class TaggedObjectAdmin(admin.ModelAdmin):
    list_display = ('object_uri', 'tag', 'user', 'object_type', 'created_at')
    list_filter = ('tag', 'user', 'object_type')
    search_fields = ('object_uri',)
    raw_id_fields = ['tag', 'user']

admin.site.register(FederatedEndpoint)
admin.site.register(Activity)
admin.site.register(Follower, FollowerAdmin)
admin.site.register(Tag, TagAdmin)
admin.site.register(TaggedObject, TaggedObjectAdmin)
