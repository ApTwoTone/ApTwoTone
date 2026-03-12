/* ── Zoar Bathroom Rentals — Main JS ─────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {

    // ── Sticky nav on scroll ────────────────────────────────────
    var nav = document.getElementById('nav');
    window.addEventListener('scroll', function() {
        if (window.scrollY > 60) {
            nav.classList.add('scrolled');
        } else {
            nav.classList.remove('scrolled');
        }
    });

    // ── Mobile menu toggle ──────────────────────────────────────
    var toggle = document.getElementById('navToggle');
    var links = document.getElementById('navLinks');
    toggle.addEventListener('click', function() {
        links.classList.toggle('open');
    });
    // Close menu on link click
    links.querySelectorAll('a').forEach(function(a) {
        a.addEventListener('click', function() {
            links.classList.remove('open');
        });
    });

    // ── Smooth scroll for anchor links ──────────────────────────
    document.querySelectorAll('a[href^="#"]').forEach(function(link) {
        link.addEventListener('click', function(e) {
            var target = document.querySelector(this.getAttribute('href'));
            if (target) {
                e.preventDefault();
                var promoBar = document.getElementById('promoBar');
                var promoH = promoBar && promoBar.offsetHeight ? promoBar.offsetHeight : 0;
                var offset = 80 + promoH; // nav height + promo bar
                var y = target.getBoundingClientRect().top + window.scrollY - offset;
                window.scrollTo({ top: y, behavior: 'smooth' });
            }
        });
    });

    // ── Fade-in on scroll (Intersection Observer) ───────────────
    var fadeEls = document.querySelectorAll(
        '.service-card, .fleet-card, .why-item, .testimonial-card, .testimonial-video-card, .area-banner, .contact-form-wrap, .contact-info'
    );
    fadeEls.forEach(function(el) { el.classList.add('fade-up'); });

    if ('IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });

        fadeEls.forEach(function(el) { observer.observe(el); });
    } else {
        // Fallback: show all immediately
        fadeEls.forEach(function(el) { el.classList.add('visible'); });
    }

    // ── UTM Parameter Extraction ────────────────────────────────
    // Captures utm_source, utm_medium, utm_campaign, utm_content, utm_term
    // from the URL query string (e.g. ?utm_source=facebook&utm_medium=paid)
    var utmParams = {};
    var referralSource = '';
    (function() {
        var params = new URLSearchParams(window.location.search);
        ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'].forEach(function(key) {
            var val = params.get(key);
            if (val) utmParams[key] = val;
        });
        // Capture vendor referral source from ?ref=VENDOR_ID
        referralSource = params.get('ref') || '';
    })();

    // ── Nexus Event Tracking ─────────────────────────────────────
    // Sends events to the Nexus feedback loop for analysis
    var nexusBase = 'https://crm.zoarbathroomrental.com';
    var loc = window.location;
    if (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') {
        nexusBase = loc.protocol + '//' + loc.host;
    }

    function trackEvent(eventType, data) {
        var payload = Object.assign({
            event_type: eventType,
            page_url: window.location.href,
            source: utmParams.utm_source || 'direct',
            utm_source: utmParams.utm_source || '',
            utm_medium: utmParams.utm_medium || '',
            utm_campaign: utmParams.utm_campaign || '',
            utm_content: utmParams.utm_content || '',
            timestamp: new Date().toISOString(),
        }, data || {});
        // Fire and forget — don't block UI
        fetch(nexusBase + '/api/webhook/website-event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }).catch(function() {});
    }

    // Track page view
    trackEvent('page_view', { referrer: document.referrer });

    // Track scroll depth (25%, 50%, 75%, 100%)
    var scrollTracked = {};
    window.addEventListener('scroll', function() {
        var scrollPct = Math.round((window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100);
        [25, 50, 75, 100].forEach(function(threshold) {
            if (scrollPct >= threshold && !scrollTracked[threshold]) {
                scrollTracked[threshold] = true;
                trackEvent('scroll_depth', { depth: threshold });
            }
        });
    });

    // Track CTA button clicks
    document.querySelectorAll('.btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            trackEvent('button_click', {
                button_text: btn.textContent.trim().substring(0, 50),
                button_href: btn.getAttribute('href') || '',
            });
        });
    });

    // ── Quote form submission ───────────────────────────────────
    var form = document.getElementById('quoteForm');
    var submitBtn = document.getElementById('submitBtn');
    var success = document.getElementById('formSuccess');

    form.addEventListener('submit', function(e) {
        e.preventDefault();

        // Generate shared event_id for Pixel ↔ CAPI deduplication
        var eventId = typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : 'eid_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);

        // Capture fbp/fbc cookies for CAPI matching
        var cookies = document.cookie.split('; ').reduce(function(a, c) {
            var p = c.split('='); a[p[0]] = p[1]; return a;
        }, {});

        // Collect form data
        var data = {
            first_name: form.querySelector('#fname').value.trim(),
            last_name: form.querySelector('#lname').value.trim(),
            phone: form.querySelector('#phone').value.trim(),
            email: form.querySelector('#email').value.trim(),
            event_type: form.querySelector('#event_type').value,
            event_date: form.querySelector('#event_date').value,
            guests: form.querySelector('#guests').value,
            city: form.querySelector('#city').value.trim(),
            message: form.querySelector('#message').value.trim(),
        };

        // Validate
        if (!data.first_name || !data.phone || !data.event_type || !data.city) {
            alert('Please fill in all required fields.');
            return;
        }

        // Show loading state
        submitBtn.disabled = true;
        submitBtn.querySelector('span').textContent = 'Sending...';

        // CRM webhook — public URL via Cloudflare Tunnel
        var webhookUrl = window.NEXUS_WEBHOOK || 'https://crm.zoarbathroomrental.com/api/crm/webhook/website';

        // Local dev override
        var loc = window.location;
        if (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') {
            webhookUrl = loc.protocol + '//' + loc.host + '/api/crm/webhook/website';
        }

        // Build payload with UTM tracking + CAPI dedup fields
        var payload = {
            source: 'website',
            first_name: data.first_name,
            last_name: data.last_name,
            phone: data.phone,
            email: data.email,
            event_type: data.event_type,
            event_date: data.event_date,
            guests: data.guests,
            event_city: data.city,
            notes: data.message,
            event_id: eventId,
            fbc: cookies._fbc || '',
            fbp: cookies._fbp || '',
        };
        // Attach UTM params if present (from ad clicks)
        if (utmParams.utm_source) payload.utm_source = utmParams.utm_source;
        if (utmParams.utm_medium) payload.utm_medium = utmParams.utm_medium;
        if (utmParams.utm_campaign) payload.utm_campaign = utmParams.utm_campaign;
        if (utmParams.utm_content) payload.utm_content = utmParams.utm_content;
        if (utmParams.utm_term) payload.utm_term = utmParams.utm_term;
        if (referralSource) payload.referral_source = referralSource;

        fetch(webhookUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(function() { showSuccess(); })
        .catch(function() {
            // Fallback: mailto if CRM webhook unreachable
            var subject = encodeURIComponent('Quote Request from ' + data.first_name);
            var body = encodeURIComponent(
                'Name: ' + data.first_name + ' ' + data.last_name + '\n' +
                'Phone: ' + data.phone + '\n' +
                'Email: ' + data.email + '\n' +
                'Event: ' + data.event_type + '\n' +
                'Date: ' + data.event_date + '\n' +
                'Guests: ' + data.guests + '\n' +
                'City: ' + data.city + '\n' +
                'Message: ' + data.message
            );
            window.location.href = 'mailto:zoarbathrooms@gmail.com?subject=' + subject + '&body=' + body;
            showSuccess();
        });

        function showSuccess() {
            form.querySelectorAll('.form-group, .form-row, .form-title, .btn-full, .form-note').forEach(function(el) {
                el.style.display = 'none';
            });
            success.style.display = 'block';
            submitBtn.disabled = false;

            // Fire Facebook Pixel Lead event with matching eventID for CAPI dedup
            if (typeof fbq !== 'undefined') {
                fbq('track', 'Lead', {
                    content_name: data.event_type,
                    content_category: 'quote_request',
                    city: data.city
                }, { eventID: eventId });
            }

            // Send to Nexus feedback loop
            trackEvent('form_submit', {
                event_type_selected: data.event_type,
                city: data.city,
                has_email: !!data.email,
                has_phone: !!data.phone,
                guests: data.guests,
            });

            // Also send to the form-submit webhook for lead creation
            fetch(nexusBase + '/api/webhook/form-submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: data.first_name + ' ' + data.last_name,
                    phone: data.phone,
                    email: data.email,
                    event_type: data.event_type,
                    event_date: data.event_date,
                    message: data.message,
                    page_url: window.location.href,
                    utm_source: utmParams.utm_source || '',
                    utm_medium: utmParams.utm_medium || '',
                    utm_campaign: utmParams.utm_campaign || '',
                    utm_content: utmParams.utm_content || '',
                }),
            }).catch(function() {});
        }
    });

    // ── Phone number formatting ─────────────────────────────────
    var phoneInput = document.getElementById('phone');
    phoneInput.addEventListener('input', function(e) {
        var x = e.target.value.replace(/\D/g, '').substring(0, 10);
        if (x.length >= 6) {
            e.target.value = '(' + x.substring(0,3) + ') ' + x.substring(3,6) + '-' + x.substring(6);
        } else if (x.length >= 3) {
            e.target.value = '(' + x.substring(0,3) + ') ' + x.substring(3);
        }
    });

});
