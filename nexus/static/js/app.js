/**
 * Nexus CRM — Alpine.js Application
 */
document.addEventListener('alpine:init', () => {

    Alpine.store('crm', {
        // Navigation
        view: 'overview',  // overview | contacts | pipeline | detail | activity | settings
        loading: false,
        toast: null,

        // Data
        leads: [],
        total: 0,
        stages: [],
        stats: {},
        activity: [],
        pipelineData: {},

        // Detail
        selectedLead: null,
        timeline: [],
        messages: [],

        // Conversations (detail page)
        convFilter: 'all',  // all | sms | email
        importing: false,

        // Conversations (inbox view)
        conversations: [],
        convoTotal: 0,
        convoQuery: '',
        convoOffset: 0,
        convoLimit: 50,
        convoLeadId: null,
        convoMessages: [],
        convoReply: '',
        bulkImporting: false,

        // Agents
        agentCards: [],
        agentFeed: [],
        agentFeedPolling: false,
        agentFeedTimer: null,
        agentDetail: null,
        agentLog: [],
        agentLogTimer: null,
        brainInsights: [],
        brainRecommendations: [],
        agentTotalCost: 0,
        agentQuickStats: {},
        agentGenerating: false,
        agentAnalyzing: false,

        // Metrics & Analytics
        metrics: {},
        campaignMetrics: [],
        metricsLoading: false,
        dropoutSummary: {},
        dropoutDiagnoses: [],
        dropoutLoading: false,

        // Brain Knowledge & Discussions
        brainKnowledge: [],
        brainDiscussions: [],
        brainOpenProblems: [],
        brainTab: 'insights',

        // Source Stats
        sourceStats: null,

        // Settings
        settings: {},
        fbStats: null,

        // Automation (follow-ups + templates)
        followUps: [],
        templates: [],
        templateFilter: '',
        selectedTemplate: null,
        templateLeadId: '',
        templateChannel: 'sms',
        templateSending: false,

        // Event Command Center
        eventsStats: {},
        eventsBoard: {},
        eventsTimeline: [],
        eventsLastUpdated: null,
        eventsRefreshTimer: null,
        eccFollowUpSending: false,

        // System
        healthData: null,
        backups: [],

        // Live Intel
        intelStats: {},
        intelVendors: [],
        intelCategories: [],
        intelZones: [],
        intelOutreach: [],
        intelNewCount: 0,
        intelLastUpdated: null,
        intelPrevCount: 0,
        intelPolling: false,
        intelTimer: null,

        // Contacts filters
        query: '',
        stageFilter: '',
        sort: 'updated_at',
        limit: 25,
        offset: 0,

        // Modals
        modal: '',  // '' | 'newLead' | 'addNote' | 'changeStage' | 'sendSMS' | 'sendEmail' | 'editLead'

        // Forms
        newLead: { first_name: '', last_name: '', phone: '', email: '', source: 'manual', event_type: '', event_date: '', event_city: '', guest_count: '', notes: '' },
        noteText: '',
        targetStage: '',
        smsText: '',
        emailSubject: '',
        emailBody: '',
        editFields: {},

        // Computed
        get page() { return Math.floor(this.offset / this.limit) + 1; },
        get totalPages() { return Math.ceil(this.total / this.limit) || 1; },
        get filteredMessages() {
            if (this.convFilter === 'all') return this.messages;
            return this.messages.filter(m => m.channel === this.convFilter);
        },

        // ── Init ──────────────────────────────────────────────────
        async init() {
            this.loading = true;
            try {
                await Promise.all([this.loadStages(), this.loadStats(), this.loadActivity(), this.loadSourceStats()]);
            } catch (e) { console.error('Init error:', e); }
            this.loading = false;
        },

        // ── Data Loading ──────────────────────────────────────────
        async loadLeads() {
            var params = { limit: this.limit, offset: this.offset, sort: this.sort };
            if (this.query) params.q = this.query;
            if (this.stageFilter) params.status = this.stageFilter;
            var data = await API.getLeads(params);
            this.leads = data.leads;
            this.total = data.total;
        },

        async loadStages() {
            var data = await API.getStages();
            this.stages = data.stages;
        },

        async loadStats() {
            this.stats = await API.getStats();
        },

        async loadActivity() {
            var data = await API.getActivity(30);
            this.activity = data.activity;
        },

        async loadPipeline() {
            var data = await API.getPipeline();
            this.pipelineData = data.pipeline;
        },

        // ── Navigation ────────────────────────────────────────────
        async goTo(page) {
            this.modal = '';
            this.loading = true;
            this.view = page;
            try {
                this.stopAgentPolling();
                this.stopIntelPolling();
                if (page === 'overview') {
                    await Promise.all([this.loadStages(), this.loadStats(), this.loadActivity(), this.loadSourceStats()]);
                } else if (page === 'contacts') {
                    this.offset = 0;
                    await Promise.all([this.loadLeads(), this.loadStages()]);
                } else if (page === 'conversations') {
                    this.convoOffset = 0;
                    this.convoLeadId = null;
                    this.convoMessages = [];
                    await this.loadConversations();
                } else if (page === 'pipeline') {
                    await Promise.all([this.loadPipeline(), this.loadStages()]);
                } else if (page === 'activity') {
                    await this.loadActivity();
                } else if (page === 'metrics') {
                    await this.loadMetrics();
                } else if (page === 'agents') {
                    await this.loadAgents();
                    this.startAgentPolling();
                } else if (page === 'agent-detail') {
                    // loaded via showAgentDetail
                } else if (page === 'events') {
                    await this.loadEvents();
                    this.startEventsRefresh();
                } else if (page === 'automation') {
                    await this.loadAutomation();
                } else if (page === 'live-intel') {
                    await this.loadLiveIntel();
                    this.startIntelPolling();
                } else if (page === 'settings') {
                    await this.loadSettings();
                }
            } catch (e) { this.showToast(e.message, 'error'); }
            this.loading = false;
        },

        async showDetail(leadId) {
            this.loading = true;
            this.modal = '';
            this.convFilter = 'all';
            try {
                var data = await API.getLead(leadId);
                this.selectedLead = data.lead;
                this.timeline = data.timeline || [];
                this.messages = data.messages || [];
                this.view = 'detail';
            } catch (e) { this.showToast(e.message, 'error'); }
            this.loading = false;
        },

        async refreshDetail() {
            if (!this.selectedLead) return;
            var data = await API.getLead(this.selectedLead.id);
            this.selectedLead = data.lead;
            this.timeline = data.timeline || [];
            this.messages = data.messages || [];
        },

        // ── Contacts Filtering ────────────────────────────────────
        async filterByStage(stage) {
            this.stageFilter = this.stageFilter === stage ? '' : stage;
            this.offset = 0;
            await this.loadLeads();
        },

        async search() {
            this.offset = 0;
            await this.loadLeads();
        },

        async nextPage() {
            if (this.offset + this.limit < this.total) {
                this.offset += this.limit;
                await this.loadLeads();
            }
        },

        async prevPage() {
            if (this.offset > 0) {
                this.offset = Math.max(0, this.offset - this.limit);
                await this.loadLeads();
            }
        },

        // ── Actions ───────────────────────────────────────────────
        async createLead() {
            try {
                var data = await API.createLead(this.newLead);
                this.modal = '';
                this.newLead = { first_name: '', last_name: '', phone: '', email: '', source: 'manual', event_type: '', event_date: '', event_city: '', guest_count: '', notes: '' };
                this.showToast(data.deduplicated ? 'Matched existing contact' : 'Lead created successfully');
                if (this.view === 'contacts') await this.loadLeads();
                await this.loadStages();
                await this.loadStats();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async changeStage() {
            if (!this.selectedLead || !this.targetStage) return;
            try {
                await API.transitionStage(this.selectedLead.id, this.targetStage);
                this.modal = '';
                this.targetStage = '';
                this.showToast('Stage updated');
                await this.refreshDetail();
                await this.loadStages();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async submitNote() {
            if (!this.selectedLead || !this.noteText.trim()) return;
            try {
                await API.addNote(this.selectedLead.id, this.noteText);
                this.noteText = '';
                this.modal = '';
                this.showToast('Note added');
                await this.refreshDetail();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async submitSMS() {
            if (!this.selectedLead || !this.smsText.trim()) return;
            try {
                var r = await API.sendSMS(this.selectedLead.id, this.smsText);
                this.smsText = '';
                this.modal = '';
                if (r.ok) {
                    this.showToast('SMS sent via ' + (r.method || 'unknown'));
                } else {
                    this.showToast('SMS failed: ' + (r.error || 'unknown'), 'error');
                }
                await this.refreshDetail();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async submitEmail() {
            if (!this.selectedLead || !this.emailBody.trim()) return;
            try {
                var r = await API.sendEmail(this.selectedLead.id, this.emailSubject || 'Message from Zoar Bathroom Rentals', this.emailBody);
                this.emailSubject = '';
                this.emailBody = '';
                this.modal = '';
                if (r.ok) {
                    this.showToast('Email sent');
                } else {
                    this.showToast('Email failed: ' + (r.error || 'unknown'), 'error');
                }
                await this.refreshDetail();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        openEditLead() {
            var l = this.selectedLead;
            this.editFields = {
                first_name: l.first_name || '',
                last_name: l.last_name || '',
                phone: l.phone || '',
                email: l.email || '',
                event_type: l.event_type || '',
                event_date: l.event_date || '',
                event_city: l.event_city || '',
                event_address: l.event_address || '',
                guest_count: l.guest_count || '',
                event_start_time: l.event_start_time || '',
                event_end_time: l.event_end_time || '',
                terrain_notes: l.terrain_notes || '',
                power_water_notes: l.power_water_notes || '',
                total_quote_amount: l.total_quote_amount || '',
                deposit_amount: l.deposit_amount || '',
                deposit_status: l.deposit_status || 'none',
            };
            this.modal = 'editLead';
        },

        async saveEdit() {
            if (!this.selectedLead) return;
            try {
                await API.updateLead(this.selectedLead.id, this.editFields);
                this.modal = '';
                this.showToast('Lead updated');
                await this.refreshDetail();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        // ── Automation (Follow-Ups + Templates) ─────────────────────
        async loadAutomation() {
            try {
                var results = await Promise.all([API.getFollowUps(), API.getTemplates()]);
                this.followUps = results[0].sequences || results[0].followups || [];
                this.templates = results[1].templates || [];
            } catch (e) { console.error('Automation load:', e); }
        },

        async stopFollowUp(leadId) {
            try {
                var r = await API.stopFollowUp(leadId);
                if (r.ok) {
                    this.showToast('Follow-up stopped');
                    await this.loadAutomation();
                } else {
                    this.showToast('Failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async enrollFollowUp(leadId, seqType) {
            try {
                var r = await API.enrollFollowUp(leadId, seqType);
                if (r.ok) {
                    this.showToast('Enrolled in ' + (seqType || 'default') + ' sequence');
                    if (this.view === 'automation') await this.loadAutomation();
                } else {
                    this.showToast('Failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        get filteredTemplates() {
            if (!this.templateFilter) return this.templates;
            var f = this.templateFilter;
            return this.templates.filter(function(t) { return t.category === f; });
        },

        get templateCategories() {
            var cats = {};
            (this.templates || []).forEach(function(t) { if (t.category) cats[t.category] = true; });
            return Object.keys(cats);
        },

        selectTemplate(tpl) {
            this.selectedTemplate = tpl;
        },

        async sendTemplateToLead() {
            if (!this.selectedTemplate || !this.templateLeadId) return;
            this.templateSending = true;
            try {
                var r = await API.sendTemplate(this.templateLeadId, this.selectedTemplate.id, this.templateChannel);
                if (r.ok) {
                    this.showToast('Template sent via ' + this.templateChannel);
                    this.selectedTemplate = null;
                    this.templateLeadId = '';
                } else {
                    this.showToast('Failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
            this.templateSending = false;
        },

        // ── System (Health + Backups) ────────────────────────────────
        async loadSystemHealth() {
            try {
                var results = await Promise.all([API.getHealth(), API.getBackups()]);
                this.healthData = results[0];
                this.backups = results[1].backups || [];
            } catch (e) { console.error('System load:', e); }
        },

        async createBackup() {
            try {
                var r = await API.createBackup();
                if (r.ok) {
                    this.showToast('Backup created: ' + (r.file || ''));
                    await this.loadSystemHealth();
                } else {
                    this.showToast('Backup failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        // ── Settings ──────────────────────────────────────────────
        async loadSettings() {
            this.settings = await API.getSettings();
            this.fbStats = await API.getFacebookStats();
        },

        async toggleSetting(key, value) {
            try {
                var update = {};
                update[key] = value;
                await API.updateSettings(update);
                this.settings[key] = value;
                this.showToast('Setting updated');
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        copyWebhookUrl() {
            var url = (this.settings.fb_webhook_domain || window.location.origin) + '/api/crm/webhook/facebook';
            navigator.clipboard.writeText(url).then(() => {
                this.showToast('Webhook URL copied');
            });
        },

        async testFacebookWebhook() {
            try {
                var r = await API.testFacebookWebhook();
                if (r.ok) {
                    this.showToast('Test lead created (ID: ' + r.lead.id + ')');
                    this.fbStats = await API.getFacebookStats();
                } else {
                    this.showToast('Test failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        // ── Conversations Inbox ──────────────────────────────────
        async loadConversations() {
            var params = { limit: this.convoLimit, offset: this.convoOffset };
            if (this.convoQuery) params.q = this.convoQuery;
            try {
                var data = await API.getConversations(params);
                this.conversations = data.conversations || [];
                this.convoTotal = data.total || 0;
            } catch (e) { console.error('Conversations error:', e); }
        },

        async searchConversations() {
            this.convoOffset = 0;
            await this.loadConversations();
        },

        async openConversation(leadId) {
            this.convoLeadId = leadId;
            try {
                var data = await API.getConversationMessages(leadId);
                this.convoMessages = data.messages || [];
            } catch (e) { console.error('Messages error:', e); }
        },

        async sendConvoSMS() {
            if (!this.convoLeadId || !this.convoReply.trim()) return;
            try {
                var r = await API.sendSMS(this.convoLeadId, this.convoReply);
                this.convoReply = '';
                if (r.ok) { this.showToast('SMS sent'); } else { this.showToast('SMS failed: ' + (r.error || 'unknown'), 'error'); }
                await this.openConversation(this.convoLeadId);
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async sendConvoEmail() {
            if (!this.convoLeadId || !this.convoReply.trim()) return;
            try {
                var r = await API.sendEmail(this.convoLeadId, 'Message from Zoar Bathroom Rentals', this.convoReply);
                this.convoReply = '';
                if (r.ok) { this.showToast('Email sent'); } else { this.showToast('Email failed: ' + (r.error || 'unknown'), 'error'); }
                await this.openConversation(this.convoLeadId);
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async bulkImport() {
            this.bulkImporting = true;
            try {
                await API.importAll();
                this.showToast('Import started');
            } catch (e) { this.showToast(e.message, 'error'); }
            this.bulkImporting = false;
        },

        async refreshNames() {
            try {
                var r = await API.refreshNames();
                this.showToast('Refreshed ' + (r.updated || 0) + ' names');
                if (this.view === 'contacts') await this.loadLeads();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async loadSourceStats() {
            try { this.sourceStats = await API.getSourceStats(); } catch(e) { console.warn('Source stats:', e); }
        },

        sourceLabel(s) {
            var m = { google_voice: 'Google Voice', facebook: 'Facebook', facebook_ad: 'Facebook Ad', manual: 'Manual', website: 'Website' };
            return m[s] || s || 'Unknown';
        },

        // ── Metrics & Analytics ─────────────────────────────────
        async loadMetrics() {
            this.metricsLoading = true;
            try {
                var data = await API.getMetrics();
                this.metrics = data.overall || data.metrics || {};
                var campaigns = data.by_campaign || data.campaigns || [];
                this.campaignMetrics = campaigns.filter(function(c) { return c.campaign && c.campaign !== 'None'; });
                try { this.dropoutSummary = await API.getDropoutSummary(); } catch (e) { console.warn('Dropout summary:', e); }
            } catch (e) {
                console.error('Metrics error:', e);
                this.showToast('Failed to load metrics: ' + e.message, 'error');
            }
            this.metricsLoading = false;
        },

        async runDropoutAnalysis() {
            this.dropoutLoading = true;
            try {
                var data = await API.analyzeLeadDropouts();
                this.dropoutDiagnoses = data.diagnoses || [];
                this.dropoutSummary = data.summary || {};
                this.showToast('Analysis complete: ' + this.dropoutDiagnoses.length + ' leads diagnosed');
            } catch (e) { this.showToast('Analysis failed: ' + e.message, 'error'); }
            this.dropoutLoading = false;
        },

        // ── Agent System ────────────────────────────────────────
        async loadAgents() {
            try {
                var statusData = await API.getAgentStatus();
                var agents = statusData.agents || {};
                var runningData = {};
                try { runningData = await API.getAgentRunning(); } catch(e) {}
                var runningIds = (runningData.running || []).map(function(a) { return a.agent_id; });

                var agentDefs = [
                    { id: 'content', icon: '📝', label: 'Content Agent' },
                    { id: 'lead_finder', icon: '🔍', label: 'Research Agent' },
                    { id: 'analyst', icon: '🧠', label: 'Analyst Agent' },
                    { id: 'facebook', icon: '📘', label: 'Facebook Agent' },
                    { id: 'instagram', icon: '📸', label: 'Instagram Agent' },
                    { id: 'higgsfield', icon: '🎨', label: 'HiggsField Agent' },
                ];

                this.agentCards = agentDefs.map(function(def) {
                    var data = agents[def.id] || {};
                    return {
                        id: def.id,
                        icon: def.icon,
                        label: def.label,
                        running: runningIds.indexOf(def.id) >= 0,
                        last_active: data.last_active || '',
                        stat_line: data.leads_found ? data.leads_found + ' leads' :
                                   data.posts_made ? data.posts_made + ' posts' :
                                   data.insights_produced ? data.insights_produced + ' insights' : '',
                        data: data,
                    };
                });

                this.agentTotalCost = statusData.total_cost || 0;

                // Load brain and feed
                try {
                    var brainData = await API.getAgentBrain();
                    this.brainInsights = brainData.recent_insights || brainData.insights || [];
                    this.brainRecommendations = brainData.recommendations || [];
                    this.agentQuickStats = {
                        total_insights: (brainData.summary || {}).total_insights || this.brainInsights.length,
                        leads_found: (agents.lead_finder || {}).leads_found || 0,
                        content_queued: (agents.content || {}).posts_made || 0,
                        knowledge: (brainData.knowledge || []).length,
                        discussions: (brainData.discussions || []).length,
                    };
                    this.brainOpenProblems = brainData.open_problems || [];
                } catch(e) { console.warn('Brain:', e); }

                try {
                    var feedData = await API.getAgentLiveFeed();
                    this.agentFeed = Array.isArray(feedData) ? feedData : (feedData.feed || feedData.activity || []);
                } catch(e) { console.warn('Feed:', e); }
            } catch (e) { console.error('Agents error:', e); }
        },

        startAgentPolling() {
            if (this.agentFeedPolling) return;
            this.agentFeedPolling = true;
            var self = this;
            this.agentFeedTimer = setInterval(async function() {
                try {
                    var feedData = await API.getAgentLiveFeed();
                    self.agentFeed = Array.isArray(feedData) ? feedData : (feedData.feed || feedData.activity || []);
                } catch(e) {}
            }, 10000);
        },

        stopAgentPolling() {
            this.agentFeedPolling = false;
            if (this.agentFeedTimer) { clearInterval(this.agentFeedTimer); this.agentFeedTimer = null; }
            if (this.agentLogTimer) { clearInterval(this.agentLogTimer); this.agentLogTimer = null; }
            if (this.eventsRefreshTimer) { clearInterval(this.eventsRefreshTimer); this.eventsRefreshTimer = null; }
        },

        async showAgentDetail(agentId) {
            var card = this.agentCards.find(function(a) { return a.id === agentId; });
            if (!card) return;
            this.agentDetail = card;
            this.agentLog = [];
            this.view = 'agent-detail';
            try {
                var logData = await API.getAgentLog(agentId);
                this.agentLog = logData.lines || logData.log || [];
            } catch(e) { console.warn('Log:', e); }
            var self = this;
            if (this.agentLogTimer) clearInterval(this.agentLogTimer);
            this.agentLogTimer = setInterval(async function() {
                try {
                    var ld = await API.getAgentLog(agentId);
                    self.agentLog = ld.lines || ld.log || [];
                } catch(e) {}
            }, 5000);
        },

        async generateAgentContent() {
            this.agentGenerating = true;
            try {
                var r = await API.generateAgentContent('posts', 7);
                this.showToast('Generated ' + (r.generated || 0) + ' content items');
                await this.loadAgents();
            } catch (e) { this.showToast('Generation failed: ' + e.message, 'error'); }
            this.agentGenerating = false;
        },

        async runAnalysis() {
            this.agentAnalyzing = true;
            try {
                var r = await API.runBrainAnalysis();
                this.showToast('Analysis complete');
                await this.loadAgents();
            } catch (e) { this.showToast('Analysis failed: ' + e.message, 'error'); }
            this.agentAnalyzing = false;
        },

        async startAgent(agentId) {
            try {
                var r = await API.startAgent(agentId);
                this.showToast(r.ok ? 'Agent started' : ('Failed: ' + (r.error || 'unknown')), r.ok ? 'success' : 'error');
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async stopAgent(agentId) {
            try {
                var r = await API.stopAgent(agentId);
                this.showToast(r.ok ? 'Agent stopped' : ('Failed: ' + (r.error || 'unknown')), r.ok ? 'success' : 'error');
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async loadBrainKnowledge() {
            try {
                var data = await API.getBrainKnowledge();
                this.brainKnowledge = data.knowledge || [];
            } catch(e) { console.warn('Knowledge:', e); }
        },

        async loadBrainDiscussions() {
            try {
                var data = await API.getBrainDiscussions();
                this.brainDiscussions = data.discussions || [];
                this.brainOpenProblems = data.open_problems || [];
            } catch(e) { console.warn('Discussions:', e); }
        },

        // ── Event Command Center ─────────────────────────────────
        async loadEvents() {
            try {
                var results = await Promise.all([
                    API.getPipeline(),
                    API.getStats(),
                    API.getLeads({ status: 'booked', sort: 'event_date', limit: 20 }),
                ]);
                var pipeline = results[0].pipeline || {};
                var stats = results[1] || {};
                var bookedLeads = (results[2].leads || []);

                // Build board: map pipeline statuses to our 5 columns
                this.eventsBoard = {
                    new_lead: [].concat(pipeline.new_lead || [], pipeline.auto_contacted || []),
                    quote_sent: [].concat(pipeline.quote_sent || [], pipeline.deposit_pending || []),
                    qualifying: pipeline.qualifying || [],
                    booked: pipeline.booked || [],
                    lost: pipeline.lost || [],
                };

                // Compute stats
                var now = new Date();
                var weekAgo = new Date(now.getTime() - 7 * 86400000);
                var todayStr = now.toISOString().split('T')[0];

                // Count leads this week from all columns
                var allLeads = [];
                var self = this;
                Object.keys(this.eventsBoard).forEach(function(k) {
                    allLeads = allLeads.concat(self.eventsBoard[k] || []);
                });
                var leadsThisWeek = allLeads.filter(function(l) {
                    if (!l.discovered_at && !l.created_at) return false;
                    var d = new Date(l.discovered_at || l.created_at);
                    return d >= weekAgo;
                }).length;

                // Quotes sent today
                var quotesToday = (this.eventsBoard.quote_sent || []).filter(function(l) {
                    if (!l.updated_at) return false;
                    return l.updated_at.indexOf(todayStr) === 0;
                }).length;

                // Revenue pipeline
                var revPipeline = 0;
                ['new_lead', 'quote_sent', 'qualifying'].forEach(function(k) {
                    (self.eventsBoard[k] || []).forEach(function(l) {
                        revPipeline += Number(l.total_quote_amount) || 0;
                    });
                });

                // Next event date
                var nextEvent = null;
                var bookedSorted = (this.eventsBoard.booked || []).filter(function(l) {
                    return l.event_date && new Date(l.event_date) >= now;
                }).sort(function(a, b) {
                    return new Date(a.event_date) - new Date(b.event_date);
                });
                if (bookedSorted.length > 0) nextEvent = bookedSorted[0].event_date;

                // Days since last booking
                var daysSince = null;
                var allBooked = (this.eventsBoard.booked || []).sort(function(a, b) {
                    return new Date(b.updated_at || b.discovered_at || 0) - new Date(a.updated_at || a.discovered_at || 0);
                });
                if (allBooked.length > 0) {
                    var lastBookDate = new Date(allBooked[0].updated_at || allBooked[0].discovered_at);
                    daysSince = Math.floor((now - lastBookDate) / 86400000);
                }

                // Conversion rate
                var totalAll = allLeads.length;
                var totalBooked = (this.eventsBoard.booked || []).length;
                var convRate = totalAll > 0 ? (totalBooked / totalAll) * 100 : 0;

                // Stale leads (48h+ without contact in active columns)
                var twoDaysAgo = new Date(now.getTime() - 48 * 3600000);
                var staleCount = 0;
                ['new_lead', 'quote_sent', 'qualifying'].forEach(function(k) {
                    (self.eventsBoard[k] || []).forEach(function(l) {
                        var lastContact = new Date(l.updated_at || l.discovered_at || 0);
                        if (lastContact < twoDaysAgo) staleCount++;
                    });
                });

                this.eventsStats = {
                    leads_this_week: leadsThisWeek,
                    quotes_today: quotesToday,
                    revenue_pipeline: revPipeline,
                    next_event_date: nextEvent,
                    days_since_booking: daysSince,
                    conversion_rate: convRate,
                    stale_leads: staleCount,
                };

                // Timeline: booked events with future dates, sorted chronologically
                this.eventsTimeline = bookedSorted;

                this.eventsLastUpdated = new Date().toISOString();
            } catch (e) {
                console.error('Events load error:', e);
                this.showToast('Failed to load events: ' + e.message, 'error');
            }
        },

        startEventsRefresh() {
            if (this.eventsRefreshTimer) clearInterval(this.eventsRefreshTimer);
            var self = this;
            this.eventsRefreshTimer = setInterval(async function() {
                if (self.view === 'events') {
                    try { await self.loadEvents(); } catch(e) {}
                }
            }, 30000);
        },

        async eccMoveToStage(leadId, newStage) {
            try {
                await API.transitionStage(leadId, newStage);
                this.showToast('Moved to ' + this.stageLabel(newStage));
                await this.loadEvents();
            } catch (e) {
                this.showToast('Move failed: ' + e.message, 'error');
            }
        },

        async eccSendFollowUps() {
            this.eccFollowUpSending = true;
            try {
                var stale = [];
                var now = new Date();
                var twoDaysAgo = new Date(now.getTime() - 48 * 3600000);
                var self = this;
                ['new_lead', 'quote_sent', 'qualifying'].forEach(function(k) {
                    (self.eventsBoard[k] || []).forEach(function(l) {
                        var lastContact = new Date(l.updated_at || l.discovered_at || 0);
                        if (lastContact < twoDaysAgo) stale.push(l);
                    });
                });

                var enrolled = 0;
                for (var i = 0; i < stale.length; i++) {
                    try {
                        await API.enrollFollowUp(stale[i].id, 'default');
                        enrolled++;
                    } catch(e) { /* already enrolled or error, skip */ }
                }
                this.showToast('Enrolled ' + enrolled + ' leads in follow-up sequences');
                await this.loadEvents();
            } catch (e) {
                this.showToast('Follow-up failed: ' + e.message, 'error');
            }
            this.eccFollowUpSending = false;
        },

        eventEmoji(type) {
            var m = {
                wedding: '\uD83D\uDC8D',
                corporate: '\uD83C\uDFE2',
                construction: '\uD83D\uDEA7',
                festival: '\uD83C\uDFA4',
                private_party: '\uD83C\uDF89',
                quinceanera: '\uD83C\uDF38',
                film: '\uD83C\uDFAC',
                other: '\uD83D\uDCC5',
            };
            return m[type] || '\uD83D\uDCC5';
        },

        eccCardBorder(lead) {
            if (!lead) return '';
            var now = new Date();
            var lastContact = new Date(lead.updated_at || lead.discovered_at || 0);
            var diffHours = (now - lastContact) / 3600000;
            if (diffHours < 48) return 'ecc-card-recent';
            if (diffHours < 168) return 'ecc-card-stale';
            return 'ecc-card-cold';
        },

        eccAge(lead) {
            if (!lead) return '';
            var now = new Date();
            var lastContact = new Date(lead.updated_at || lead.discovered_at || 0);
            var diffHours = (now - lastContact) / 3600000;
            if (diffHours < 1) return 'just now';
            if (diffHours < 24) return Math.floor(diffHours) + 'h ago';
            var days = Math.floor(diffHours / 24);
            return days + 'd ago';
        },

        eccAgeClass(lead) {
            if (!lead) return '';
            var now = new Date();
            var lastContact = new Date(lead.updated_at || lead.discovered_at || 0);
            var diffHours = (now - lastContact) / 3600000;
            if (diffHours < 48) return 'ecc-card-age-recent';
            if (diffHours < 168) return 'ecc-card-age-stale';
            return 'ecc-card-age-cold';
        },

        // ── Live Intel ─────────────────────────────────────────────
        async loadLiveIntel() {
            try {
                var results = await Promise.all([
                    API.getVendors({ limit: 50, offset: 0 }),
                    API.getVendorStats(),
                    API.getOutreachQueue(null, 50),
                ]);

                var vendorData = results[0];
                var statsData = results[1];
                var outreachData = results[2];

                // Detect new vendors for highlight animation
                var newTotal = vendorData.total || 0;
                if (this.intelPrevCount > 0 && newTotal > this.intelPrevCount) {
                    this.intelNewCount = newTotal - this.intelPrevCount;
                } else {
                    this.intelNewCount = 0;
                }
                this.intelPrevCount = newTotal;

                this.intelVendors = vendorData.vendors || [];
                this.intelOutreach = outreachData.queue || outreachData.outreach || [];
                this.intelLastUpdated = new Date().toISOString();

                // Build stats from statsData
                var byStatus = statsData.by_status || {};
                var byCategory = statsData.by_category || {};
                var byCity = statsData.by_city || {};
                this.intelStats = {
                    total: statsData.total || newTotal,
                    status_new: byStatus.new || 0,
                    status_contacted: byStatus.contacted || 0,
                    status_partner: byStatus.partner || 0,
                    outreach_drafts: this.intelOutreach.length,
                    added_today: statsData.added_today || 0,
                };

                // Build category chart data
                var catArr = [];
                for (var cat in byCategory) {
                    if (byCategory.hasOwnProperty(cat)) {
                        catArr.push({ name: cat, count: byCategory[cat] });
                    }
                }
                catArr.sort(function(a, b) { return b.count - a.count; });
                this.intelCategories = catArr;

                // Build geographic zones from city data
                var zoneMap = {
                    'SFV': ['san fernando', 'panorama city', 'north hollywood', 'van nuys', 'reseda', 'northridge', 'canoga park', 'chatsworth', 'sylmar', 'pacoima', 'sun valley', 'tarzana', 'encino', 'woodland hills', 'sherman oaks', 'studio city', 'winnetka', 'granada hills', 'mission hills', 'arleta', 'lake balboa', 'west hills', 'porter ranch', 'north hills', 'valley village'],
                    'Santa Clarita': ['santa clarita', 'valencia', 'newhall', 'saugus', 'canyon country', 'castaic', 'stevenson ranch', 'acton', 'agua dulce'],
                    'Conejo Valley': ['thousand oaks', 'westlake village', 'agoura hills', 'oak park', 'newbury park', 'camarillo', 'moorpark', 'simi valley'],
                    'San Gabriel Valley': ['pasadena', 'arcadia', 'monrovia', 'glendora', 'covina', 'azusa', 'duarte', 'irwindale', 'baldwin park', 'el monte', 'alhambra', 'san gabriel', 'temple city', 'rosemead', 'la verne', 'claremont', 'pomona', 'west covina', 'diamond bar', 'walnut', 'rowland heights'],
                    'Greater LA': ['los angeles', 'burbank', 'glendale', 'pasadena', 'santa monica', 'culver city', 'inglewood', 'torrance', 'downey', 'whittier', 'long beach', 'west hollywood', 'beverly hills', 'malibu', 'calabasas', 'la canada', 'montrose', 'la crescenta', 'eagle rock', 'highland park', 'silver lake', 'echo park', 'hollywood', 'koreatown', 'downtown', 'south la'],
                };

                var zoneCounts = {};
                var zoneNames = ['SFV', 'Santa Clarita', 'Conejo Valley', 'San Gabriel Valley', 'Greater LA'];
                zoneNames.forEach(function(z) { zoneCounts[z] = 0; });

                for (var cityName in byCity) {
                    if (!byCity.hasOwnProperty(cityName)) continue;
                    var lower = (cityName || '').toLowerCase().trim();
                    var matched = false;
                    for (var zi = 0; zi < zoneNames.length; zi++) {
                        var zn = zoneNames[zi];
                        var cities = zoneMap[zn];
                        for (var ci = 0; ci < cities.length; ci++) {
                            if (lower.indexOf(cities[ci]) >= 0) {
                                zoneCounts[zn] += byCity[cityName];
                                matched = true;
                                break;
                            }
                        }
                        if (matched) break;
                    }
                    if (!matched) {
                        zoneCounts['Greater LA'] += byCity[cityName];
                    }
                }

                this.intelZones = zoneNames.map(function(z) { return { name: z, count: zoneCounts[z] }; });

            } catch (e) {
                console.error('Live Intel error:', e);
            }
        },

        startIntelPolling() {
            if (this.intelPolling) return;
            this.intelPolling = true;
            var self = this;
            this.intelTimer = setInterval(async function() {
                if (self.view !== 'live-intel') return;
                try { await self.loadLiveIntel(); } catch(e) {}
            }, 10000);
        },

        stopIntelPolling() {
            this.intelPolling = false;
            if (this.intelTimer) { clearInterval(this.intelTimer); this.intelTimer = null; }
        },

        async approveOutreach(vendorId, outreachId) {
            try {
                var r = await API.approveOutreach(vendorId, outreachId);
                if (r.ok) {
                    this.showToast('Outreach approved');
                    await this.loadLiveIntel();
                } else {
                    this.showToast('Failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        async skipOutreach(vendorId, outreachId) {
            try {
                await API.updateVendorStatus(vendorId, { outreach_status: 'cancelled' });
                this.showToast('Outreach skipped');
                await this.loadLiveIntel();
            } catch (e) { this.showToast(e.message, 'error'); }
        },

        get intelMaxCat() {
            var max = 0;
            (this.intelCategories || []).forEach(function(c) { if (c.count > max) max = c.count; });
            return max;
        },

        intelCatIcon(cat) {
            var icons = {
                wedding_venue: '\uD83D\uDC92',
                wedding_planner: '\uD83D\uDC8D',
                event_planner: '\uD83D\uDCCB',
                quinceanera_venue: '\uD83C\uDF89',
                quinceanera_planner: '\uD83C\uDF8A',
                catering: '\uD83C\uDF7D\uFE0F',
                party_rental: '\uD83C\uDFAA',
                dj_entertainment: '\uD83C\uDFB5',
                photography: '\uD83D\uDCF7',
                florist: '\uD83C\uDF3A',
                bartending_mobile_bar: '\uD83C\uDF78',
                construction: '\uD83C\uDFD7\uFE0F',
                film_production: '\uD83C\uDFAC',
                festival_organizer: '\uD83C\uDFAA',
                other: '\uD83D\uDCBC',
            };
            return icons[cat] || '\uD83D\uDCBC';
        },

        intelCatLabel(cat) {
            if (!cat) return 'Other';
            return cat.replace(/_/g, ' ').replace(/\b\w/g, function(l) { return l.toUpperCase(); });
        },

        intelCatColor(idx) {
            var colors = [
                'var(--cyan)', 'var(--purple)', 'var(--green)', 'var(--orange)',
                'var(--accent)', 'var(--yellow)', 'var(--emerald)', 'var(--red)',
                '#818cf8', '#f472b6', '#a78bfa', '#34d399',
                '#fbbf24', '#60a5fa', '#f87171',
            ];
            return colors[idx % colors.length];
        },

        intelZoneColor(count) {
            if (count >= 20) return 'var(--cyan)';
            if (count >= 10) return 'var(--green)';
            if (count >= 5) return 'var(--yellow)';
            if (count > 0) return 'var(--border-light)';
            return 'var(--border)';
        },

        intelZoneBg(count) {
            if (count >= 20) return 'rgba(6,182,212,0.1)';
            if (count >= 10) return 'rgba(34,197,94,0.08)';
            if (count >= 5) return 'rgba(234,179,8,0.06)';
            if (count > 0) return 'rgba(255,255,255,0.02)';
            return 'var(--bg-card-alt)';
        },

        // ── Conversations Import (detail page) ──────────────────
        async importConversations() {
            if (!this.selectedLead) return;
            this.importing = true;
            try {
                var r = await API.importConversations(this.selectedLead.id);
                if (r.ok) {
                    this.showToast('Imported ' + r.imported + ' messages (' + r.sms + ' SMS, ' + r.email + ' email)');
                    await this.refreshDetail();
                } else {
                    this.showToast('Import failed: ' + (r.error || 'unknown'), 'error');
                }
            } catch (e) { this.showToast(e.message, 'error'); }
            this.importing = false;
        },

        // ── Helpers ───────────────────────────────────────────────
        showToast(msg, type) {
            this.toast = { msg: msg, type: type || 'success' };
            setTimeout(() => { this.toast = null; }, 3500);
        },

        badgeClass(color) { return 'badge badge-' + (color || 'blue'); },

        stageLabel(key) {
            var s = this.stages.find(function(s) { return s.key === key; });
            return s ? s.label : (key || '').replace(/_/g, ' ');
        },

        stageColor(key) {
            var s = this.stages.find(function(s) { return s.key === key; });
            return s ? s.color : 'blue';
        },

        getAllowedTransitions(st) {
            var map = {
                new_lead: ['auto_contacted', 'qualifying', 'lost'],
                auto_contacted: ['qualifying', 'quote_sent', 'lost'],
                qualifying: ['quote_sent', 'lost'],
                quote_sent: ['deposit_pending', 'qualifying', 'lost'],
                deposit_pending: ['booked', 'quote_sent', 'lost'],
                booked: ['completed', 'lost'],
                completed: [],
                lost: ['new_lead'],
            };
            return map[st] || [];
        },

        tlDotClass(t) {
            if (!t) return 'tl-dot';
            if (t.indexOf('sms') >= 0) return 'tl-dot dot-sms';
            if (t.indexOf('email') >= 0) return 'tl-dot dot-email';
            if (t.indexOf('stage') >= 0) return 'tl-dot dot-stage';
            if (t.indexOf('note') >= 0) return 'tl-dot dot-note';
            if (t.indexOf('reply') >= 0) return 'tl-dot dot-reply';
            if (t.indexOf('created') >= 0) return 'tl-dot dot-created';
            return 'tl-dot';
        },

        leadName(l) {
            if (!l) return '-';
            return l.full_name || ((l.first_name || '') + ' ' + (l.last_name || '')).trim() || '-';
        },

        formatDate(d) {
            if (!d) return '-';
            try {
                var s = d.indexOf('T') >= 0 ? d : d.replace(' ', 'T');
                var dt = new Date(s);
                if (isNaN(dt.getTime())) return d;
                return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            } catch(e) { return d; }
        },

        formatTime(d) {
            if (!d) return '';
            try {
                var dt = new Date(d);
                return dt.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
            } catch(e) { return d; }
        },

        formatTimeShort(d) {
            if (!d) return '';
            try {
                var dt = new Date(d);
                var now = new Date();
                var diff = now - dt;
                if (diff < 60000) return 'just now';
                if (diff < 3600000) return Math.floor(diff/60000) + 'm ago';
                if (diff < 86400000) return Math.floor(diff/3600000) + 'h ago';
                if (diff < 604800000) return Math.floor(diff/86400000) + 'd ago';
                return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            } catch(e) { return d; }
        },

        parseNotes(s) {
            try { return JSON.parse(s || '[]'); } catch(e) { return []; }
        },

        money(n) {
            var v = Number(n) || 0;
            if (v === 0) return '-';
            return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        },

        eventTypeLabel(t) {
            var m = { wedding: 'Wedding', corporate: 'Corporate', construction: 'Construction', festival: 'Festival', private_party: 'Private Party', other: 'Other' };
            return m[t] || t || '-';
        },

        sourceIcon(s) {
            if (!s) return '';
            if (s === 'facebook') return 'fb';
            if (s === 'manual') return 'manual';
            if (s === 'ghl') return 'ghl';
            return s.substring(0, 3);
        },
    });
});
