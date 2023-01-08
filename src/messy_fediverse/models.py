from django.db import models
from django.conf import settings
from os import path
import json
from datetime import datetime

def get_upload_path(self, filename):
    '''
    self: model instance
    filename: string
    '''
    datadir = settings.MESSY_FEDIVERSE.get('DATADIR', settings.MEDIA_ROOT)
    datadir = path.relpath(datadir, settings.MEDIA_ROOT)
    now = datetime.now()
    uniqid = hex(int(str(now.timestamp()).replace('.', '')))[2:]
    return path.join(datadir, 'activity', now.strftime('%Y/%m/%d'), f'{uniqid}.{filename}')

class FederatedEndpoint(models.Model):
    uri = models.URLField('URL', unique=True, null=False, blank=False)
    disabled = models.BooleanField('Disabled', default=False, null=False)
    def __str__(self):
        name = ''
        if self.disabled:
            name = '[X] '
        return name + self.uri

class Activity(models.Model):
    TYPES = (
        ('',    ''),
        ('FOL', 'Follow'),
        ('UNF', 'Unfollow'),
        ('UND', 'Undo'),
        ('ACC', 'Accept'),
        ('CRE', 'Create'),
        ('NTE', 'Note'),
        ('DEL', 'Delete'),
        ('TOM', 'Tombstone'),
        ('LKE', 'Like'),
        ('ANN', 'Announce')
    )
    ts = models.DateTimeField('Timestamp', auto_now_add=True)
    uri = models.URLField('Activity URI', unique=True, null=False)
    activity_type = models.CharField('Type', choices=TYPES, max_length=3, null=False, default='', blank=True)
    actor_uri = models.URLField('Actor URI', null=False, default='', blank=True)
    object_uri = models.URLField('Object URI', null=False, default='', blank=True)
    self_json = models.FileField('Raw JSON', upload_to=get_upload_path, null=True)
    incoming = models.BooleanField('Is incoming', default=False, null=False)
    
    def get_dict(self):
        data = None
        
        if self.self_json.name:
            try:
                self.self_json.seek(0)
                data = json.loads(self.self_json.read().decode('utf-8'))
                data['_static'] = True
            except:
                pass
        
        if not data:
            data = {
                "@context": "https://www.w3.org/ns/activitystreams",
                "id": self.uri,
                "type": self.activity_type,
                "actor": self.actor_uri,
                "object": self.object_uri,
                "_static": False
            }

        return data
    
    def __str__(self):
        action = dict(Activity.TYPES).get(self.activity_type, '')
        ts = self.ts.strftime('%Y-%m-%d %H:%M:%S')
        return f'{self.uri} {action} {ts}'
    
class Follower(models.Model):
    uri = models.URLField('Actor URI', unique=True, null=False)
    ## Whom they follow
    object_uri = models.URLField('Followed object URI', null=False, default='', blank=True)
    activity = models.OneToOneField(Activity, null=True, blank=True, on_delete=models.SET_NULL)
    endpoint = models.ForeignKey(FederatedEndpoint, null=True, blank=True, on_delete=models.SET_NULL)
    disabled = models.BooleanField('Disabled', default=False, null=False)
    accepted = models.BooleanField('Accepted', default=False, null=False)
    
    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        
        ## Copy of loaded values to use for comparison in save()
        ## https://docs.djangoproject.com/en/4.1/ref/models/instances/#customizing-model-loading
        instance._loaded_values = dict(zip(field_names, values))
        
        return instance
    
    def save(self, *args, **kwargs):
        ## If modifying
        if not self._state.adding:
            if not self.disabled:
                if self.accepted and self.accepted != self._loaded_values['accepted']:
                    ## "accepted" value changed
                    pass
        
        return super().save(*args, **kwargs)
    
    def __str__(self):
        name = ''
        if self.disabled:
            name = '[X] '
        return name + self.uri