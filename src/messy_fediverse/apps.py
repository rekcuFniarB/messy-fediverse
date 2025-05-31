import sys
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _
from django.core.signals import request_finished
from asgiref.sync import async_to_sync, sync_to_async
import asyncio

selfModule = sys.modules[__name__]

class MessyFediverseConfig(AppConfig):
    name = "messy_fediverse"
    verbose_name = _("Messy Fediverse")
    
    def ready(self):
        from .fediverse import FediverseActor
        from .middleware import stderrlog
        from . import controller
        selfModule.FediverseActor = FediverseActor
        selfModule.stderrlog = stderrlog
        selfModule.controller = controller
        controller.stderrlog = stderrlog
        
        request_finished.connect(self.postprocess_tasks, dispatch_uid='messy_fediverse_postprocess_tasks')
    
    def postprocess_tasks(self, sender, **kwargs):
        ## Async signals support already added in development version of django. Current is 4.1
        return async_to_sync(controller.postprocess_tasks)(sender, **kwargs)
