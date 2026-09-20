from django import forms
from django.conf import settings
from .models import Tag

def get_languages():
    return (('', ''), *settings.LANGUAGES)

class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ('name', 'title')

class TaggedObjectForm(forms.Form):
    object_uri = forms.URLField(max_length=255, required=True, label='Object URI')
    object_type = forms.CharField(max_length=64, required=False, label='Object type')

class InteractForm(forms.Form):
    summary = forms.CharField(max_length=255, required=False)
    content = forms.CharField(widget=forms.Textarea, max_length=65535, required=True)
    language = forms.ChoiceField(required=False, choices=get_languages)
    link = forms.CharField(widget=forms.HiddenInput, required=False)
    tags = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), max_length=65535, required=False)
    url = forms.CharField(max_length=255, required=False)
    reply_direct = forms.CharField(widget=forms.HiddenInput, required=False)
    context = forms.CharField(widget=forms.HiddenInput, required=False)

class InteractSearchForm(forms.Form):
    acct = forms.CharField(max_length=255, required=False, label='Search')

class ReplyForm(forms.Form):
    account = forms.EmailField(
        max_length=255,
        required=True
    )
    uri = forms.URLField(
        widget=forms.HiddenInput,
        required=False
    )
    
    account.widget.attrs.update({'placeholder': 'Example: username@mastodon.online'})
