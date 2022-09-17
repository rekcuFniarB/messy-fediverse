import re

class Tag:
    _tagname = ''
    _relink = re.compile(f'(<{_tagname} ([^>]+?)>(.+?)</{_tagname}>)')
    _reattr = re.compile(r'[a-zA-Z0-9_-]+?="[^"]*?"')
    
    def __init__(self, *args, **kwargs):
        '''
        data: tuple: ('<a href="https://google.com" class="linkclass">', 'href="https://google.com" class="linkclass"', 'linkname')
        You probably get it from regex parser.
        '''
        self._match = args
        self.attr_href = ''
        self.attr_class = []
        self.attr_rel = []
        self.attr_id = ''
        self.name = ''
        
        if len(args) > 2:
            ## Parsing attributes
            attrs = self._reattr.findall(args[1])
            for attr in attrs:
                if not attr:
                    continue
                attr = attr.split('=')
                if len(attr) < 2:
                    attr.append(attr[0])
                
                ## If attribute of type list (like classes)
                _attr = getattr(self, f'attr_{attr[0]}', None)
                if type(_attr) is list:
                    for val in '='.join(attr[1:]).strip(' "').split(' '):
                        if val and val not in _attr:
                            _attr.append(val)
                    setattr(self, f'attr_{attr[0]}', _attr)
                else:
                    setattr(self, f'attr_{attr[0]}', '='.join(attr[1:]).strip(' "'))
            
            self.name = args[2]
        else:
            for key in kwargs:
                existing_attr = getattr(self, key, None)
                if type(existing_attr) is list:
                    if type(kwargs[key]) is list:
                        setattr(self, key, kwargs[key])
                    else:
                        existing_attr.append(kwargs[key])
                else:
                    setattr(self, key, kwargs[key])
    
    def __str__(self):
        attrs = []
        for name in self.__dict__:
            if name.startswith('attr_'):
                attrname = name[5:]
                attrval = getattr(self, name, '')
                if attrval:
                    if type(attrval) is list:
                        attrval = ' '.join(attrval)
                    attrval = attrval.replace('"', '&quot;')
                    attrs.append(f'{attrname}="{attrval}"')
        return f'<{self._tagname} ' + ' '.join(attrs) + f'>{self.name}</{self._tagname}>'
    
    @classmethod
    def findall(cls, string):
        '''
        Finds all links in `string`
        '''
        result = []
        for match in cls._relink.findall(string):
            result.append(cls(*match))
        
        return result

class TagA(Tag):
    _tagname = 'a'
    _relink = re.compile(f'(<{_tagname} ([^>]+?)>(.+?)</{_tagname}>)')
