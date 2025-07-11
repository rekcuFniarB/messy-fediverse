(function() {
    function initMessyFediverse() {
        var messyFediverse = document.getElementById('messy-fediverse');
        if (!!messyFediverse && typeof messyFediverse.onFormSubmit !== 'function') {
            /**
             * Form submit handler. Adds AJAX support for forms.
             * @param object Event object.
             * @return promise object.
             */
            messyFediverse.onFormSubmit = function(event) {
                event.preventDefault();
                event.stopPropagation();
                
                var requestUrl = new URL(event.target.action);
                requestParams = {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                }
                
                requestParams.method = event.target.method.toUpperCase();
                if (event.target.dataset.method) {
                    requestParams.method = event.target.dataset.method.toUpperCase();
                }
                for (let field of event.target.elements) {
                    // Button can override request method if has value like data-method="PATCH"
                    if (field.type == 'submit' && field.dataset.method) {
                        requestParams.method = field.dataset.method.toUpperCase();
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
                    
                    if (!!input.name && !!input.value && input.type != 'submit') {
                        formParams.set(input.name, input.value);
                        if (input.name.toLowerCase().indexOf('csrf') > -1) {
                            // Sending also as header for methods like DELETE
                            requestParams.headers['X-CSRFToken'] = input.value;
                        }
                    }
                }
                
                if (event.submitter && event.submitter.name && event.submitter.value) {
                    // Which button caused submit
                    formParams.set(event.submitter.name, event.submitter.value);
                }
                
                this.loading();
                
                result = fetch(requestUrl.href, requestParams).then(response => {
                    if (!response.ok) {
                        throw new Error('Request failed.');
                    }
                    
                    var contentType = response.headers.get('content-type');
                    if (contentType.indexOf('text/html') === 0) {
                        if (response.url) {
                            // In case of redirects
                            window.history.replaceState(window.history.state || {}, '', response.url);
                        }
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
                }).finally(() => {
                    this.loading(false);
                });
                
                if (!!event.target.elements.uri) {
                    // If reply form
                    event.target.elements.uri.value = '';
                }
                
                return result;
            }.bind(messyFediverse); // onFormSubmit
            
            messyFediverse.clickEventHandler = function(event) {
                if (event.target.closest('button.reply-js')) {
                    return this.replyToComment(event);
                }
                else if (event.target.closest('button.delete-js')) {
                    return this.deleteReply(event);
                }
                else if (event.target.closest('[data-ajax-target]')) {
                    return this.ajaxLoader(event);
                }
                else {
                    let eTarget = event.target.closest('[data-action]');
                    if (eTarget && typeof this[eTarget.dataset.action] === 'function') {
                        return this[eTarget.dataset.action](event);
                    }
                }
            }.bind(messyFediverse); // clickEventHandler
            
            messyFediverse.changeEventHandler = function(event) {
                let input = event.target.closest('input');
                if (!input) return false;
                if (input.type == 'checkbox') {
                    if (input.dataset.toggle) {
                        return this.toggleDisplay(input.dataset.toggle, input.checked);
                    }
                }
            }.bind(messyFediverse);
            
            messyFediverse.anyEventHandler = function(event) {
                if (event.type && typeof this[`${event.type}EventHandler`] === 'function') {
                    return this[`${event.type}EventHandler`](event);
                }
            }.bind(messyFediverse);
            
            messyFediverse.toggleDisplay = function(selector, display) {
                for (let element of document.querySelectorAll(selector)) {
                    if (display) {
                        element.classList.remove('d-none');
                    } else {
                        element.classList.add('d-none');
                    }
                }
            }.bind(messyFediverse);
            
            messyFediverse.togglePreview = function(event) {
                const form = event.target.closest('form');
                const previewBlock = document.getElementById('messy-fediverse-preview');
                const previewContainer = previewBlock.querySelector('.preview');
                if (form) {
                    // Preview button was clicked
                    previewContainer.innerHTML = form.elements.content.value;
                    form.classList.add('d-none');
                    previewBlock.classList.remove('d-none');
                    previewBlock.callbackForm = form;
                } else {
                    // Edit button was clicked
                    previewBlock.classList.add('d-none');
                    previewBlock.callbackForm.classList.remove('d-none');
                }
            }.bind(messyFediverse);
            
            messyFediverse.replyToComment = function(event) {
                var parentComment = event.target.closest('[data-uri]');
                if (!!parentComment) {
                    event.preventDefault();
                    var form = this.querySelector('form');
                    form.elements.uri.value = parentComment.dataset.uri;
                    form.dispatchEvent(new Event('submit', {cancelable: true, bubbles: true}));
                }
            }.bind(messyFediverse);
            
            messyFediverse.deleteReply = function(event) {
                var comment = event.target.closest('[data-local-id]');
                if (!!comment) {
                    event.preventDefault();
                    let form;
                    for (let f of this.querySelectorAll('form')) {
                        if (f.action.indexOf('/replies/') > -1) {
                            form = f;
                            break;
                        }
                    }
                    if (!form) {
                        return alert('Form not found');
                    }
                    var url = new URL(form.action);
                    url.searchParams.set('id', comment.dataset.localId || '');
                    url.searchParams.set('uri', comment.dataset.uri || '');
                    // First hidden input
                    var csrf = [...form.elements].filter(item => {
                        return item.type == 'hidden' && item.name.toLowerCase().indexOf('csrf') > -1;
                    })[0] || {};
                    this.loading();
                    fetch(
                        url,
                        {
                            method: 'DELETE',
                            headers: {
                                'X-CSRFToken': csrf.value || '',
                                'X-Requested-With': 'XMLHttpRequest'
                            }
                        }
                    )
                    .then(response => {
                        if (!response.ok) {
                            throw new Error('Request failed.');
                        }
                        return response.json();
                    })
                    .then(response => {
                        if (!!response.success) {
                            comment.style.display = 'none';
                        } else {
                            let error = 'Request failed.';
                            if (!!response.error) {
                                error = response.error;
                            }
                            throw new Error(error);
                        }
                    })
                    .catch(error => {
                        console.error('ERROR:', error);
                        alert(error);
                    }).finally(() => {
                        this.loading(false);
                    });
                }
            }.bind(messyFediverse);
            
            messyFediverse.ajaxLoader = function(event) {
                var eventTarget = event.target.closest('[data-ajax-target]');
                if (!!eventTarget.dataset.ajaxTarget) {
                    var ajaxTarget = document.getElementById(eventTarget.dataset.ajaxTarget);
                    if (!!ajaxTarget) {
                        event.preventDefault();
                        event.stopPropagation();
                        this.loading();
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
                            window.dispatchEvent(new Event('load'));
                        }).finally(() => {
                            this.loading(false);
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
                    for (let section of newContent.querySelectorAll('[id]')) {
                        section = section.closest('[id]');
                        if (section.id) {
                            domSection = document.getElementById(section.id);
                            if (domSection) {
                                domSection.innerHTML = section.innerHTML;
                                for (attr of section.attributes) {
                                    if (attr.name) {
                                        domSection.setAttribute(attr.name, attr.value);
                                    }
                                }
                                domSection.dispatchEvent(new Event('updated', {bubbles: true, cancelable: true}));
                            }
                        }
                    }
                }
                
                var rawJson = this.querySelector('script[type="application/json"]');
                if (!!rawJson && rawJson.innerHTML.trim()) {
                    try {
                        this.activityData = JSON.parse(rawJson.innerHTML.trim());
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
                
                let awaitLink = this.querySelector('.fediverse-awaiting-post a');
                if (awaitLink) {
                    let url = new URL(awaitLink);
                    let delay = parseInt(url.searchParams.get('delay') || 81);
                    delay *= 3
                    url.searchParams.set('delay', delay);
                    awaitLink.href = url.toString();
                    setTimeout(awaitLink.click.bind(awaitLink), delay);
                }
            }.bind(messyFediverse);
            
            messyFediverse.loading = function(on) {
                if (typeof on === 'undefined') {
                    on = true;
                }
                
                if (on) {
                    document.body.classList.add('loading');
                    document.body.style.pointerEvents = 'none';
                } else {
                    document.body.classList.remove('loading');
                    document.body.style.pointerEvents = '';
                }
                
                return on;
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
            messyFediverse.addEventListener('click', messyFediverse.anyEventHandler);
            for (let event of ['input', 'change', 'paste']) {
                messyFediverse.addEventListener(event, messyFediverse.cacheFormInput);
                messyFediverse.addEventListener(event, messyFediverse.anyEventHandler);
            }
            messyFediverse.updateContent();
        }
    }
    initMessyFediverse();
    window.addEventListener('load', initMessyFediverse);
})();
