var messyFediverse = document.getElementById('messy-fediverse');
if (!!messyFediverse) {
    /**
     * Form submit handler. Adds AJAX support for forms.
     * @param object Event object.
     * @return promise object.
     */
    messyFediverse.onFormSubmit = function(event) {
        event.preventDefault();
        var requestUrl = new URL(event.target.action);
        requestParams = {
            method: event.target.method.toUpperCase(),
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        }
        
        var formParams;
        if (requestParams.method == 'GET') {
            formParams = requestUrl.searchParams;
        } else {
            formParams = new URLSearchParams();
            requestParams.body = formParams;
        }
        
        for (let input of event.target.elements) {
            if (!!input.required && !input.value) {
                if (typeof input.scrollIntoView === 'function') {
                    input.classList.add('error');
                    input.addEventListener('focus', function(event) {
                        event.target.classList.remove('error');
                    }, {once: true});
                    input.scrollIntoView(false);
                }
                return alert('Fill all required fields.');
            }
            
            if (!!input.name && !!input.value) {
                formParams.set(input.name, input.value)
            }
        }
        
        result = fetch(requestUrl.href, requestParams).then(response => {
            if (!response.ok) {
                throw new Error('Request failed.');
            }
            
            var contentType = response.headers.get('content-type');
            if (contentType.indexOf('text/html') === 0) {
                return response.text();
            }
            else if (contentType.indexOf('text/plain') === 0) {
                return response.text().then(text => {
                    return {alert: text};
                })
            }
            else if (contentType.indexOf('application/') === 0 && contentType.indexOf('json') !== -1) {
                return response.json();
            }
        }).then(response => {
            if (typeof response === 'object') {
                if (!!response.alert) {
                    alert(response.alert);
                }
                else if (!!response.popup) {
                    return window.open(response.popup, '_blank', 'popup,width=640,height=640');
                }
            } else {
                this.updateContent(response);
            }
        }).catch(error => {
            console.error('ERROR:', error);
            alert(error);
        });
        
        if (!!event.target.elements.uri) {
            // If reply form
            event.target.elements.uri.value = '';
        }
        
        return result;
    }.bind(messyFediverse); // onFormSubmit
    
    messyFediverse.clicksHandler = function(event) {
        if (event.target.closest('button.reply-js')) {
            return this.replyToComment(event);
        }
        if (event.target.closest('[data-ajax-target]')) {
            return this.ajaxLoader(event);
        }
    }.bind(messyFediverse); // clicksHandler
    
    messyFediverse.replyToComment = function(event) {
        var parentComment = event.target.closest('[data-uri]');
        if (!!parentComment) {
            event.preventDefault();
            var form = this.querySelector('form');
            form.elements.uri.value = parentComment.dataset.uri;
            form.dispatchEvent(new Event('submit', {cancelable: true, bubbles: true}));
        }
    }.bind(messyFediverse);
    
    messyFediverse.ajaxLoader = function(event) {
        var eventTarget = event.target.closest('[data-ajax-target]');
        if (!!eventTarget.dataset.ajaxTarget) {
            var ajaxTarget = document.getElementById(eventTarget.dataset.ajaxTarget);
            if (!!ajaxTarget) {
                event.preventDefault();
                event.stopPropagation();
                fetch(eventTarget.href, {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                })
                .then(response => {
                    return response.text();
                })
                .then(response => {
                    var newContent = document.createElement('div');
                    newContent.innerHTML = response;
                    newContent = newContent.querySelector('#messy-fediverse-block-main,main,body') || newContent;
                    ajaxTarget.innerHTML = newContent.innerHTML;
                });
            }
        }
    }.bind(messyFediverse);
    
    /**
     * Refresh page content
     * @param string HTML of new content
     */
    messyFediverse.updateContent = function(content) {
        if (!!content) {
            var newContent = document.createElement('div');
            newContent.innerHTML = content;
            newContent = newContent.querySelector(`#${this.id}`);
            if (!!newContent) {
                this.innerHTML = newContent.innerHTML;
            }
        }
        
        var rawJson = this.querySelector('script[type="application/json"]');
        if (!!rawJson) {
            try {
                this.activityData = JSON.parse(rawJson.innerHTML);
            } catch (e) {
                console.error('JSON PARSE ERROR:', e);
                this.activityData = {};
            }
        }
        
        for (let form of this.querySelectorAll('form')) {
            for (let input of form.elements) {
                if (!!input.name && !input.value && ['hidden', 'submit'].indexOf(input.type) === -1) {
                    let value = localStorage.getItem(`messyFediverseFormCache_${input.name}`);
                    if (value !== null) {
                        input.value = value;
                    }
                }
            }
        }
    }.bind(messyFediverse);
    
    /**
     * Caching form inputs
     */
    messyFediverse.cacheFormInput = function(event) {
        if (!!event.target.name && typeof event.target.value !== 'undefined') {
            localStorage.setItem(`messyFediverseFormCache_${event.target.name}`, event.target.value);
        }
    }.bind(messyFediverse);
    
    messyFediverse.addEventListener('submit', messyFediverse.onFormSubmit);
    messyFediverse.addEventListener('click', messyFediverse.clicksHandler);
    for (let event of ['input', 'change', 'paste']) {
        messyFediverse.addEventListener(event, messyFediverse.cacheFormInput);
    }
    messyFediverse.updateContent();
} else {
    console.error('WARNING: messy-fediverse element not found.');
}
