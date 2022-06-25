from django.urls import path
#from django.urls import include

from . import  controller

app_name = 'messy-fediverse'

urlpatterns = [
    path('', controller.main, name='root'),
    #path('.json', controller.root_json, name='root-json'),
    path('webfinger/', controller.webfinger, name='webfinger'),
    path('outbox/', controller.outbox, name='outbox'),
    path('inbox/', controller.Inbox.as_view(), name='inbox'),
    path('featured/', controller.featured, name='featured'),
    path('api/auth/', controller.auth, name='auth'),
    path('api/auth/token/', controller.auth_token, name='auth-token'),
    path('followers/', controller.dumb, name='followers'),
    path('following/', controller.Following.as_view(), name='following'),
    path('interact/', controller.Interact.as_view(), name='interact'),
    path('status/<path:rpath>/', controller.status, name='status'),
    path('replies/<path:rpath>/', controller.Replies.as_view(), name='replies'),
    path('replies<path:rpath>', controller.Replies.as_view(), name='replies_slashed_url'),
    path('<path:rpath>/', controller.dumb, name='dumb')
]
