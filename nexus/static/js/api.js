/**
 * Nexus CRM — API Client
 */
const API = {
    base: '/api/crm',

    async _fetch(path, opts = {}) {
        const url = this.base + path;
        const res = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...opts.headers },
            ...opts,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: res.statusText }));
            throw new Error(err.error || 'HTTP ' + res.status);
        }
        return res.json();
    },

    // Leads
    getLeads(params = {}) {
        const qs = new URLSearchParams(params).toString();
        return this._fetch('/leads?' + qs);
    },
    getLead(id) {
        return this._fetch('/leads/' + id);
    },
    createLead(data) {
        return this._fetch('/leads', { method: 'POST', body: JSON.stringify(data) });
    },
    updateLead(id, data) {
        return this._fetch('/leads/' + id, { method: 'PUT', body: JSON.stringify(data) });
    },
    transitionStage(id, stage, reason) {
        return this._fetch('/leads/' + id + '/stage', {
            method: 'POST',
            body: JSON.stringify({ stage: stage, reason: reason || '', actor: 'user' }),
        });
    },
    addNote(id, note, author) {
        return this._fetch('/leads/' + id + '/note', {
            method: 'POST',
            body: JSON.stringify({ note: note, author: author || 'Kai' }),
        });
    },
    getTimeline(id, limit) {
        return this._fetch('/leads/' + id + '/timeline?limit=' + (limit || 50));
    },
    sendSMS(id, message) {
        return this._fetch('/leads/' + id + '/sms', {
            method: 'POST',
            body: JSON.stringify({ message: message }),
        });
    },
    sendEmail(id, subject, message) {
        return this._fetch('/leads/' + id + '/email', {
            method: 'POST',
            body: JSON.stringify({ subject: subject, message: message }),
        });
    },

    // Pipeline
    getStages() {
        return this._fetch('/stages');
    },
    getStats() {
        return this._fetch('/stats');
    },
    getActivity(limit) {
        return this._fetch('/activity?limit=' + (limit || 30));
    },
    getPipeline() {
        return this._fetch('/leads/pipeline');
    },

    // Settings
    getSettings() {
        return this._fetch('/settings');
    },
    updateSettings(data) {
        return this._fetch('/settings', { method: 'PUT', body: JSON.stringify(data) });
    },

    // Conversations (inbox)
    getConversations(params = {}) {
        const qs = new URLSearchParams(params).toString();
        return this._fetch('/conversations?' + qs);
    },
    getConversationMessages(leadId, limit) {
        return this._fetch('/conversations/' + leadId + '/messages?limit=' + (limit || 200));
    },
    importConversations(leadId) {
        return this._fetch('/leads/' + leadId + '/import-conversations', { method: 'POST' });
    },

    // Bulk import
    importAll() {
        return this._fetch('/import/all', { method: 'POST' });
    },
    getImportStatus() {
        return this._fetch('/import/status');
    },
    refreshNames() {
        return this._fetch('/import/refresh-names', { method: 'POST' });
    },

    // Facebook
    testFacebookWebhook() {
        return this._fetch('/webhook/facebook/test', { method: 'POST' });
    },
    getFacebookStats() {
        return this._fetch('/facebook/stats');
    },

    // Source Attribution
    getSourceStats() {
        return this._fetch('/stats/sources');
    },

    // ── Agent System ─────────────────────────────────────────────
    getAgentStatus() {
        return fetch('/api/agents/status').then(r => r.json());
    },
    getAgentLiveFeed() {
        return fetch('/api/agents/live-feed').then(r => r.json());
    },
    getAgentBrain() {
        return fetch('/api/agents/brain').then(r => r.json());
    },
    getAgentLog(agentId) {
        return fetch('/api/agents/log/' + agentId).then(r => r.json());
    },
    getAgentContentQueue() {
        return fetch('/api/agents/content-queue').then(r => r.json());
    },
    getAgentDiscoveredLeads() {
        return fetch('/api/agents/discovered-leads').then(r => r.json());
    },
    getAgentRunning() {
        return fetch('/api/agents/running').then(r => r.json());
    },
    generateAgentContent(type, count) {
        return fetch('/api/agents/generate-content', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type || 'posts', count: count || 7 }),
        }).then(r => r.json());
    },
    startAgent(agentId) {
        return fetch('/api/agents/start/' + agentId, { method: 'POST' }).then(r => r.json());
    },
    stopAgent(agentId) {
        return fetch('/api/agents/stop/' + agentId, { method: 'POST' }).then(r => r.json());
    },

    // ── Metrics ───────────────────────────────────────────────────
    getMetrics() {
        return fetch('/api/metrics').then(r => r.json());
    },
    getMetricHistory(metricName, days) {
        return fetch('/api/metrics/history/' + metricName + '?days=' + (days || 7)).then(r => r.json());
    },
    getLatestMetrics() {
        return fetch('/api/metrics/latest').then(r => r.json());
    },

    // ── Lead Analysis ─────────────────────────────────────────────
    analyzeLeadDropouts() {
        return fetch('/api/analysis/lead-dropouts', { method: 'POST' }).then(r => r.json());
    },
    getDropoutSummary() {
        return fetch('/api/analysis/dropout-summary').then(r => r.json());
    },

    // ── Brain Discussion & Knowledge ──────────────────────────────
    getBrainDiscussions() {
        return fetch('/api/brain/discussions').then(r => r.json());
    },
    getBrainKnowledge() {
        return fetch('/api/brain/knowledge').then(r => r.json());
    },
    runBrainAnalysis() {
        return fetch('/api/brain/run-analysis', { method: 'POST' }).then(r => r.json());
    },

    // ── Division Three / Autonomous Entrepreneur ──────────────────
    getD3EntrepreneurDashboard() {
        return fetch('/api/d3/entrepreneur/dashboard').then(r => r.json());
    },
    getD3EntrepreneurRuntime() {
        return fetch('/api/d3/entrepreneur/runtime').then(r => r.json());
    },
    getD3EntrepreneurJourney(params = {}) {
        const qs = new URLSearchParams(params).toString();
        return fetch('/api/d3/entrepreneur/journey' + (qs ? ('?' + qs) : '')).then(r => r.json());
    },
    runD3EntrepreneurCycle(parallelism, limit) {
        return fetch('/api/d3/entrepreneur/cycle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parallelism: parallelism || 4, limit: limit || 20 }),
        }).then(r => r.json());
    },
    startD3EntrepreneurLoop(intervalSec, parallelism) {
        return fetch('/api/d3/entrepreneur/loop/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval_sec: intervalSec || 1800, parallelism: parallelism || 4 }),
        }).then(r => r.json());
    },
    stopD3EntrepreneurLoop() {
        return fetch('/api/d3/entrepreneur/loop/stop', { method: 'POST' }).then(r => r.json());
    },

    // ── Ad Performance Import ─────────────────────────────────────
    importAdPerformance(data) {
        return fetch('/api/webhook/ad-performance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        }).then(r => r.json());
    },

    // ── Follow-Up Sequences ─────────────────────────────────────────
    getFollowUps() {
        return this._fetch('/followups');
    },
    getFollowUp(leadId) {
        return this._fetch('/followups/' + leadId);
    },
    stopFollowUp(leadId) {
        return this._fetch('/followups/' + leadId + '/stop', { method: 'POST' });
    },
    enrollFollowUp(leadId, seqType) {
        return this._fetch('/followups/' + leadId + '/enroll', {
            method: 'POST',
            body: JSON.stringify({ sequence_type: seqType || 'default' }),
        });
    },

    // ── SMS/Email Templates ─────────────────────────────────────────
    getTemplates() {
        return this._fetch('/templates');
    },
    createTemplate(data) {
        return this._fetch('/templates', {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },
    sendTemplate(leadId, templateId, channel) {
        return this._fetch('/leads/' + leadId + '/send-template', {
            method: 'POST',
            body: JSON.stringify({ template_id: templateId, channel: channel || 'sms' }),
        });
    },

    // ── System / Health / Backups ───────────────────────────────────
    getHealth() {
        return fetch('/api/health').then(r => r.json());
    },
    getBackups() {
        return fetch('/api/system/backups').then(r => r.json());
    },
    createBackup() {
        return fetch('/api/system/backups', { method: 'POST' }).then(r => r.json());
    },

    // ── Vendor / Live Intel ──────────────────────────────────────────
    getVendors(params) {
        var qs = new URLSearchParams(params || {}).toString();
        return fetch('/api/vendors?' + qs).then(function(r) { return r.json(); });
    },
    getVendorStats() {
        return fetch('/api/vendors/stats').then(function(r) { return r.json(); });
    },
    getVendorCategories() {
        return fetch('/api/vendors/categories').then(function(r) { return r.json(); });
    },
    getOutreachQueue(status, limit) {
        var qs = new URLSearchParams({ limit: limit || 50 });
        if (status) qs.set('status', status);
        return fetch('/api/vendors/outreach-queue?' + qs.toString()).then(function(r) { return r.json(); });
    },
    approveOutreach(vendorId, outreachId) {
        return fetch('/api/vendors/' + vendorId + '/approve-outreach', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ outreach_id: outreachId }),
        }).then(function(r) { return r.json(); });
    },
    updateVendorStatus(vendorId, data) {
        return fetch('/api/vendors/' + vendorId + '/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        }).then(function(r) { return r.json(); });
    },

    // ── Email Marketing (Tomorrow Review Queue) ────────────────────────────
    getEmailMarketingPreview(params) {
        var qs = new URLSearchParams(params || {}).toString();
        return fetch('/api/email-marketing/preview-batch' + (qs ? ('?' + qs) : '')).then(function(r) { return r.json(); });
    },
    approveEmailMarketingBatch(payload) {
        return fetch('/api/email-marketing/approve-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        }).then(function(r) { return r.json(); });
    },
    editEmailMarketingQueueItem(queueId, payload) {
        return fetch('/api/email-marketing/queue/' + queueId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        }).then(function(r) { return r.json(); });
    },
    skipEmailMarketing(payload) {
        return fetch('/api/email-marketing/skip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        }).then(function(r) { return r.json(); });
    },
    getEmailMarketingStats() {
        return fetch('/api/email-marketing/stats').then(function(r) { return r.json(); });
    },
    getEmailMarketingHealth() {
        return fetch('/api/email-marketing/campaign-health').then(function(r) { return r.json(); });
    },
};
