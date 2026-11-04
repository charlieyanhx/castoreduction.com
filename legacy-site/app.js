/* ============================================
   Castor Advisory — Application Logic
   ============================================ */

(function () {
    'use strict';

    // ── Config ──────────────────────────────────────────
    var EMAIL = 'contact@castoradvisory.com';
    var FORM_ENDPOINT = null; // Set to Cloudflare Worker URL when ready, e.g. 'https://forms.castoradvisory.com/submit'

    var FORM_CONFIG = {
        participants: {
            title: 'Apply as a Participant',
            subtitle: 'Tell us about yourself and what you\'re looking for.',
            fields: [
                { name: 'name', label: 'Full Name', type: 'text', required: true, placeholder: 'Your name' },
                { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@example.com' },
                { name: 'university', label: 'University / Institution', type: 'text', placeholder: 'Where you studied or are studying' },
                { name: 'field', label: 'Field of Study', type: 'text', placeholder: 'e.g. Computer Science, Business' },
                { name: 'resume', label: 'Resume / CV', type: 'file', accept: '.pdf,.doc,.docx', required: true },
                { name: 'interest', label: 'What interests you about Castor?', type: 'textarea', placeholder: 'Tell us briefly...' }
            ],
            submit: 'Submit Application'
        },
        clients: {
            title: 'Get in Touch',
            subtitle: 'Tell us about your business and what you need.',
            fields: [
                { name: 'name', label: 'Your Name', type: 'text', required: true, placeholder: 'Your name' },
                { name: 'email', label: 'Work Email', type: 'email', required: true, placeholder: 'you@company.com' },
                { name: 'company', label: 'Company', type: 'text', required: true, placeholder: 'Company name' },
                { name: 'size', label: 'Company Size', type: 'select', options: ['1-10', '11-50', '51-200', '201-500', '500+'] },
                { name: 'needs', label: 'What are you looking for?', type: 'textarea', placeholder: 'Describe your project or needs...' }
            ],
            submit: 'Submit Inquiry'
        },
        recruiters: {
            title: 'Partner With Us',
            subtitle: 'Learn how Castor talent profiles can improve your placements.',
            fields: [
                { name: 'name', label: 'Your Name', type: 'text', required: true, placeholder: 'Your name' },
                { name: 'email', label: 'Work Email', type: 'email', required: true, placeholder: 'you@agency.com' },
                { name: 'agency', label: 'Agency / Firm', type: 'text', placeholder: 'Your organization' },
                { name: 'focus', label: 'Recruiting Focus', type: 'text', placeholder: 'e.g. Tech, Finance, Consulting' },
                { name: 'message', label: 'Anything else?', type: 'textarea', placeholder: 'Optional details...' }
            ],
            submit: 'Start Conversation'
        },
        investors: {
            title: 'Request Materials',
            subtitle: 'We\'ll share our operating model and financial framework.',
            fields: [
                { name: 'name', label: 'Your Name', type: 'text', required: true, placeholder: 'Your name' },
                { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@fund.com' },
                { name: 'firm', label: 'Firm / Fund', type: 'text', placeholder: 'Your organization' },
                { name: 'role', label: 'Role', type: 'text', placeholder: 'e.g. Partner, Analyst' },
                { name: 'interest', label: 'What materials are you interested in?', type: 'select', options: ['Full operating model + financials', 'Investor deck / summary', 'Talent intelligence overview', 'Partnership discussion', 'Other'] },
                { name: 'reason', label: 'What prompted your interest?', type: 'textarea', placeholder: 'Optional. Help us understand your perspective...' }
            ],
            submit: 'Request the Deck'
        },
        general: {
            title: 'Get in Touch',
            subtitle: 'We\'d love to hear from you.',
            fields: [
                { name: 'name', label: 'Your Name', type: 'text', required: true, placeholder: 'Your name' },
                { name: 'email', label: 'Email', type: 'email', required: true, placeholder: 'you@example.com' },
                { name: 'message', label: 'Message', type: 'textarea', required: true, placeholder: 'How can we help?' }
            ],
            submit: 'Send Message'
        }
    };

    // ── Navigation ──────────────────────────────────────
    var NAV_ITEMS = [
        { id: 'home', label: 'Home' },
        { id: 'participants', label: 'For Participants' },
        { id: 'clients', label: 'For Clients' },
        { id: 'recruiters', label: 'For Recruiters' },
        { id: 'investors', label: 'For Investors' },
        { id: 'about', label: 'The Model' }
    ];
    var currentPage = 'home';
    var mobileMenuOpen = false;

    function initNavigation() {
        var desktopNav = document.getElementById('desktopNavLinks');
        NAV_ITEMS.forEach(function (item) {
            var btn = document.createElement('button');
            btn.className = 'nav-link' + (currentPage === item.id ? ' active' : '');
            btn.textContent = item.label;
            btn.onclick = function () { navigateTo(item.id); };
            desktopNav.appendChild(btn);
        });
        var mobileNav = document.getElementById('mobileMenu');
        NAV_ITEMS.forEach(function (item) {
            var btn = document.createElement('button');
            btn.className = 'mobile-menu-link' + (currentPage === item.id ? ' active' : '');
            btn.textContent = item.label;
            btn.onclick = function () { navigateTo(item.id); toggleMobileMenu(); };
            mobileNav.appendChild(btn);
        });
    }

    window.navigateTo = function (pageId, pushState) {
        if (pushState === undefined) pushState = true;
        document.querySelectorAll('.page').forEach(function (p) { p.classList.remove('active'); });
        var page = document.getElementById(pageId);
        if (!page) return;
        page.classList.add('active');
        currentPage = pageId;
        if (pushState) {
            var hash = pageId === 'home' ? '' : '#' + pageId;
            history.pushState({ page: pageId }, '', hash || window.location.pathname);
        }
        updateNavigation();
        if (mobileMenuOpen) toggleMobileMenu();
        window.scrollTo({ top: 0, behavior: 'instant' });
        // Refresh AOS for newly visible page
        if (typeof AOS !== 'undefined') {
            setTimeout(function () { AOS.refresh(); }, 50);
        }
    };

    function updateNavigation() {
        document.querySelectorAll('.nav-link').forEach(function (btn) {
            btn.classList.remove('active');
            var item = NAV_ITEMS.find(function (i) { return i.id === currentPage; });
            if (item && btn.textContent === item.label) btn.classList.add('active');
        });
        document.querySelectorAll('.mobile-menu-link').forEach(function (btn) {
            btn.classList.remove('active');
            var item = NAV_ITEMS.find(function (i) { return i.id === currentPage; });
            if (item && btn.textContent === item.label) btn.classList.add('active');
        });
    }

    window.toggleMobileMenu = function () {
        var menu = document.getElementById('mobileMenu');
        mobileMenuOpen = !mobileMenuOpen;
        menu.classList.toggle('active');
        var icon = document.getElementById('hamburgerIcon');
        icon.innerHTML = mobileMenuOpen
            ? '<line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line>'
            : '<line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="18" x2="21" y2="18"></line>';
    };

    function getPageFromHash() {
        var hash = window.location.hash.replace('#', '');
        var valid = NAV_ITEMS.map(function (n) { return n.id; });
        return valid.includes(hash) ? hash : 'home';
    }

    window.addEventListener('popstate', function (e) {
        navigateTo(e.state ? e.state.page : getPageFromHash(), false);
    });

    // ── Navbar scroll effect ────────────────────────────
    window.addEventListener('scroll', function () {
        var navbar = document.getElementById('navbar');
        if (window.scrollY > 20) navbar.classList.add('scrolled');
        else navbar.classList.remove('scrolled');
    });

    // ── Forms ───────────────────────────────────────────
    window.openForm = function (key) {
        var config = FORM_CONFIG[key] || FORM_CONFIG.general;
        var container = document.getElementById('modalFormContent');
        var fieldsHTML = '';
        config.fields.forEach(function (f) {
            var req = f.required ? ' required' : '';
            fieldsHTML += '<div class="form-group">';
            fieldsHTML += '<label for="form-' + f.name + '">' + f.label + (f.required ? ' *' : '') + '</label>';
            if (f.type === 'file') {
                var accept = f.accept || '.pdf,.doc,.docx';
                fieldsHTML += '<div class="file-dropzone" id="dropzone-' + f.name + '" onclick="document.getElementById(\'form-' + f.name + '\').click()">';
                fieldsHTML += '<input type="file" id="form-' + f.name + '" name="' + f.name + '" accept="' + accept + '"' + req + ' onchange="handleFileSelect(this, \'' + f.name + '\')">';
                fieldsHTML += '<div class="file-dropzone-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="12" y1="18" x2="12" y2="12"></line><line x1="9" y1="15" x2="15" y2="15"></line></svg></div>';
                fieldsHTML += '<div class="file-dropzone-text" id="dropzone-text-' + f.name + '"><strong>Click to upload</strong> or drag and drop<br><span class="file-dropzone-hint">PDF, DOC, or DOCX (max 10 MB)</span></div></div>';
            } else if (f.type === 'textarea') {
                fieldsHTML += '<textarea id="form-' + f.name + '" name="' + f.name + '" placeholder="' + (f.placeholder || '') + '"' + req + '></textarea>';
            } else if (f.type === 'select') {
                fieldsHTML += '<select id="form-' + f.name + '" name="' + f.name + '"' + req + '><option value="">Select...</option>';
                f.options.forEach(function (o) { fieldsHTML += '<option value="' + o + '">' + o + '</option>'; });
                fieldsHTML += '</select>';
            } else {
                fieldsHTML += '<input type="' + f.type + '" id="form-' + f.name + '" name="' + f.name + '" placeholder="' + (f.placeholder || '') + '"' + req + '>';
            }
            fieldsHTML += '</div>';
        });
        container.innerHTML = '<h2>' + config.title + '</h2><p>' + config.subtitle + '</p><form id="contactForm" onsubmit="handleFormSubmit(event, \'' + key + '\')">' + fieldsHTML + '<button type="submit" class="form-submit">' + config.submit + '</button></form>';
        document.getElementById('modalOverlay').classList.add('active');
        document.body.style.overflow = 'hidden';
        container.querySelectorAll('.file-dropzone').forEach(initDropzone);
        setTimeout(function () {
            var fi = container.querySelector('input:not([type="file"]), textarea, select');
            if (fi) fi.focus();
        }, 300);
    };

    function initDropzone(dropzone) {
        ['dragenter', 'dragover'].forEach(function (evt) {
            dropzone.addEventListener(evt, function (e) { e.preventDefault(); e.stopPropagation(); dropzone.classList.add('dragover'); });
        });
        ['dragleave', 'drop'].forEach(function (evt) {
            dropzone.addEventListener(evt, function (e) { e.preventDefault(); e.stopPropagation(); dropzone.classList.remove('dragover'); });
        });
        dropzone.addEventListener('drop', function (e) {
            var files = e.dataTransfer.files;
            if (files.length > 0) {
                var input = dropzone.querySelector('input[type="file"]');
                var dt = new DataTransfer();
                dt.items.add(files[0]);
                input.files = dt.files;
                handleFileSelect(input, input.name);
            }
        });
    }

    window.handleFileSelect = function (input, fieldName) {
        var dropzone = document.getElementById('dropzone-' + fieldName);
        var textEl = document.getElementById('dropzone-text-' + fieldName);
        var file = input.files[0];
        if (file) {
            if (file.size > 10 * 1024 * 1024) {
                alert('File is too large. Please upload a file under 10 MB.');
                input.value = '';
                return;
            }
            var sizeStr = file.size >= 1024 * 1024 ? (file.size / (1024 * 1024)).toFixed(1) + ' MB' : (file.size / 1024).toFixed(0) + ' KB';
            dropzone.classList.add('has-file');
            textEl.innerHTML = '<strong>' + file.name + '</strong><br><span class="file-dropzone-hint">' + sizeStr + '</span><button type="button" class="file-dropzone-remove" onclick="removeFile(event, \'' + fieldName + '\')"><svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg> Remove</button>';
        }
    };

    window.removeFile = function (e, fieldName) {
        e.stopPropagation();
        document.getElementById('form-' + fieldName).value = '';
        var dropzone = document.getElementById('dropzone-' + fieldName);
        dropzone.classList.remove('has-file');
        document.getElementById('dropzone-text-' + fieldName).innerHTML = '<strong>Click to upload</strong> or drag and drop<br><span class="file-dropzone-hint">PDF, DOC, or DOCX (max 10 MB)</span>';
    };

    window.closeModal = function () {
        document.getElementById('modalOverlay').classList.remove('active');
        document.body.style.overflow = '';
    };

    window.closeModalOnBackdrop = function (e) {
        if (e.target === e.currentTarget) closeModal();
    };

    window.handleFormSubmit = function (e, formKey) {
        e.preventDefault();
        var form = e.target;
        var btn = form.querySelector('.form-submit');
        btn.disabled = true;
        btn.textContent = 'Sending...';

        var data = new FormData(form);
        var hasFileInput = form.querySelector('input[type="file"]');
        var resumeFile = hasFileInput ? hasFileInput.files[0] : null;
        var subject = FORM_CONFIG[formKey] ? FORM_CONFIG[formKey].title : 'Inquiry';

        // If we have a backend endpoint, POST to it
        if (FORM_ENDPOINT) {
            data.append('_form', formKey);
            data.append('_subject', subject);

            fetch(FORM_ENDPOINT, { method: 'POST', body: data })
                .then(function (res) {
                    if (!res.ok) throw new Error('Submission failed');
                    return res.json();
                })
                .then(function () {
                    showFormSuccess('Your submission has been received. We\'ll be in touch soon.');
                })
                .catch(function () {
                    showFormSuccess('Something went wrong. Please email us directly at ' + EMAIL + '.');
                });
            return;
        }

        // Fallback: mailto
        var bodyLines = [];
        for (var pair of data.entries()) {
            if (pair[1] instanceof File) {
                if (pair[1].name) bodyLines.push('Resume: ' + pair[1].name + ' (please attach)');
            } else {
                bodyLines.push(pair[0] + ': ' + pair[1]);
            }
        }
        var body = bodyLines.join('\n');
        var hasResume = resumeFile && resumeFile.name;
        var successMsg = hasResume
            ? 'Your email client will open with your details. <strong>Please attach your resume</strong> (' + resumeFile.name + ') before sending.'
            : 'Your email client will open with your details pre-filled. Just hit send and we\'ll be in touch soon.';

        setTimeout(function () {
            showFormSuccess(successMsg);
            window.location.href = 'mailto:' + EMAIL + '?subject=' + encodeURIComponent(subject + ' - Castor Advisory') + '&body=' + encodeURIComponent(body);
            setTimeout(closeModal, hasResume ? 5000 : 3000);
        }, 600);
    };

    function showFormSuccess(message) {
        document.getElementById('modalFormContent').innerHTML =
            '<div class="form-success">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>' +
            '<h3>Thank you!</h3><p>' + message + '</p></div>';
    }

    window.openEmail = function (subject) {
        window.location.href = 'mailto:' + EMAIL + '?subject=' + encodeURIComponent(subject || 'Inquiry');
    };

    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });

    // ── Init ────────────────────────────────────────────
    initNavigation();
    var initialPage = getPageFromHash();
    if (initialPage !== 'home') {
        navigateTo(initialPage, false);
    } else {
        history.replaceState({ page: 'home' }, '', window.location.pathname + window.location.hash);
    }

    // Init AOS after page is ready
    if (typeof AOS !== 'undefined') {
        AOS.init({
            duration: 700,
            easing: 'ease-out-cubic',
            once: true,
            offset: 60,
            disable: function () {
                return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            }
        });
    }
})();
