import syslog

class SysLog:
    '''
    Requests logger
    '''
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        
        ## Before view code
        response = self.get_response(request)
        ## After view code
        
        if request.resolver_match and request.resolver_match.app_name == 'messy-fediverse':
            syslog.syslog(syslog.LOG_INFO, f'MESSY SOCIAL {request.method}: {request.path}')
            syslog.syslog(syslog.LOG_INFO, 'META: ' + request.META.__str__())
            syslog.syslog(syslog.LOG_INFO, 'GET: ' + request.GET.__str__())
            if request.method == 'POST':
                syslog.syslog(syslog.LOG_INFO, 'POST: ' + request.POST.__str__())
            
            syslog.syslog(syslog.LOG_INFO, 'Response: ' + response.__str__())
        
        return response
