from django import forms

class InteractForm(forms.Form):
    subject = forms.CharField(max_length=255, required=False)
    content = forms.CharField(widget=forms.Textarea, max_length=65535, required=True)
    link = forms.CharField(widget=forms.HiddenInput, required=False)
    tags = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), max_length=65535, required=False)
    custom_url = forms.CharField(max_length=255, required=False)
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
