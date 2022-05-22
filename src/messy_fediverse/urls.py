from django.urls import path
#from django.urls import include

from . import  controller

app_name = 'messy-fediverse'

urlpatterns = [
    path('/', controller.main, name='root'),
    path('.json', controller.root_json, name='root-json'),
    path('/outbox/', controller.outbox, name='outbox'),
    path('/inbox/', controller.inbox, name='inbox'),
    path('/featured/', controller.featured, name='featured'),
    path('/api/auth/', controller.auth, name='auth'),
    path('/api/auth/token/', controller.auth_token, name='auth-token'),
    path('/interact/', controller.Interact.as_view(), name='interact'),
    path('/status/<path:rpath>/', controller.status, name='status'),
    path('/<path:rpath>/', controller.dumb, name='dumb')
]
