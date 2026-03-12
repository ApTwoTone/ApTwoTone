window.aggressiveOutreachApp = function aggressiveOutreachApp() {
  return {
    summary: {},
    targets: [],
    selectedTarget: null,
    selectedStatus: 'researched',
    statusFilter: '',
    runLimit: 15,
    runConcurrency: 5,
    loading: false,
    running: false,
    error: '',

    async init() {
      await this.refresh();
    },

    async refresh() {
      this.error = '';
      await Promise.all([this.loadSummary(), this.loadTargets()]);
    },

    async loadSummary() {
      const res = await fetch('/api/aggressive-outreach/summary');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to load summary');
      this.summary = data;
    },

    async loadTargets() {
      this.loading = true;
      try {
        const qs = new URLSearchParams({ limit: 50 });
        if (this.statusFilter) qs.set('status', this.statusFilter);
        const res = await fetch('/api/aggressive-outreach/targets?' + qs.toString());
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to load targets');
        this.targets = data.targets || [];
        if (this.selectedTarget) {
          const replacement = this.targets.find((item) => item.id === this.selectedTarget.id);
          this.selectedTarget = replacement || this.targets[0] || null;
          this.selectedStatus = this.selectedTarget ? (this.selectedTarget.status || 'researched') : 'researched';
        } else {
          this.selectedTarget = this.targets[0] || null;
          this.selectedStatus = this.selectedTarget ? (this.selectedTarget.status || 'researched') : 'researched';
        }
      } catch (err) {
        this.error = err.message || String(err);
      }
      this.loading = false;
    },

    async runResearch() {
      this.running = true;
      this.error = '';
      try {
        const res = await fetch('/api/aggressive-outreach/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            limit: this.runLimit || 15,
            concurrency: this.runConcurrency || 5,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Research run failed');
        await this.refresh();
      } catch (err) {
        this.error = err.message || String(err);
      }
      this.running = false;
    },

    async queueEmail(target) {
      this.error = '';
      try {
        const res = await fetch('/api/aggressive-outreach/targets/' + target.id + '/queue-email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Queue email failed');
        await this.refresh();
      } catch (err) {
        this.error = err.message || String(err);
      }
    },

    async saveStatus() {
      if (!this.selectedTarget) return;
      this.error = '';
      try {
        const res = await fetch('/api/aggressive-outreach/targets/' + this.selectedTarget.id + '/status', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: this.selectedStatus }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Status update failed');
        await this.refresh();
      } catch (err) {
        this.error = err.message || String(err);
      }
    },

    selectTarget(target) {
      this.selectedTarget = target;
      this.selectedStatus = target.status || 'researched';
    },

    formatCategory(category) {
      return (category || 'other').replace(/[_-]/g, ' ').replace(/\b\w/g, function(letter) {
        return letter.toUpperCase();
      });
    },

    prettyStatus(status) {
      return (status || 'researched').replace(/_/g, ' ').replace(/\b\w/g, function(letter) {
        return letter.toUpperCase();
      });
    },

    normalizedPhone(phone) {
      return String(phone || '').replace(/[^\d]/g, '');
    },

    shortDate(value) {
      if (!value) return '-';
      const date = new Date(value.replace(' ', 'T'));
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString();
    },
  };
};
