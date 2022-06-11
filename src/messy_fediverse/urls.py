from django.urls import path
#from django.urls import include

from . import  controller

app_name = 'messy-fediverse'

urlpatterns = [
    path('/', controller.main, name='root'),
    path('.json', controller.root_json, name='root-json'),
    path('/webfinger/', controller.webfinger, name='webfinger'),
    path('/outbox/', controller.outbox, name='outbox'),
    path('/inbox/', controller.Inbox.as_view(), name='inbox'),
    path('/featured/', controller.featured, name='featured'),
    path('/api/auth/', controller.auth, name='auth'),
    path('/api/auth/token/', controller.auth_token, name='auth-token'),
    path('/followers/', controller.dumb, name='followers'),
    path('/following/', controller.dumb, name='following'),
    path('/interact/', controller.Interact.as_view(), name='interact'),
    path('/status/<path:rpath>/', controller.status, name='status'),
    path('/replies/<path:rpath>/', controller.replies, name='replies'),
    path('/<path:rpath>/', controller.dumb, name='dumb')
]
