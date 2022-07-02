from django import forms

class InteractForm(forms.Form):
    subject = forms.CharField(max_length=255, required=False)
    content = forms.CharField(widget=forms.Textarea, max_length=65535, required=True)
    link = forms.CharField(widget=forms.HiddenInput, required=False)
    custom_url = forms.CharField(max_length=255, required=False)

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
