from django import forms

class InteractForm(forms.Form):
    content = forms.CharField(widget=forms.Textarea, max_length=65535, required=True)
    link = forms.CharField(widget=forms.HiddenInput, required=True)
