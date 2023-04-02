from django import forms
from django.conf import settings

def get_languages():
    return (('', ''), *settings.LANGUAGES)

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
