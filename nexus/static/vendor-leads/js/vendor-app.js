/**
 * Vendor Leads Dashboard — Alpine.js application
 * Real-time vendor lead monitoring with SSE integration.
 */

const API_BASE = '/api/fb';

// ── Helpers ──────────────────────────────────────

function scoreLabel(score) {
  if (score >= 80) return 'HIGH FIT';
  if (score >= 50) return 'MEDIUM';
  if (score >= 30) return 'LOW';
  return 'POOR FIT';
}

function scoreClass(score) {
  if (score >= 80) return 'high';
  if (score >= 50) return 'medium';
  if (score >= 30) return 'low';
  return 'poor';
}

function activityStars(level) {
  const filled = Math.min(Math.max(level || 0, 0), 5);
  return '<span class="stars">' +
    '<span>'.repeat(filled).split('<span>').join('<span>\u2605</span>').slice(0, -7) +
    '\u2605'.repeat(filled) +
    '</span>';
}

function starsHtml(level) {
  const n = Math.min(Math.max(level || 0, 0), 5);
  let s = '';
  for (let i = 0; i < 5; i++) {
    s += i < n ? '\u2605' : '<span class="empty">\u2606</span>';
  }
  return '<span class="stars">' + s + '</span>';
}

function formatCategory(cat) {
  if (!cat) return '\u2014';
  return cat.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatStatus(status) {
  if (!status) return 'new';
  return status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}


// ── Alpine Store ─────────────────────────────────

document.addEventListener('alpine:init', () => {

  Alpine.store('app', {
    // Data
    prospects: [],
    liveFeed: [],
    stats: {},
    loading: true,

    // Filters
    statusFilter: '',
    categoryFilter: '',
    minScore: '',
    searchQuery: '',
    sortBy: 'referral_score',
    sortDir: 'desc',

    // Detail panel
    selectedVendor: null,
    detailOpen: false,

    // SSE
    connected: false,
    _eventSource: null,
    _reconnectTimer: null,

    // ── Init ──────────────────────────────────
    async init() {
      await this.loadData();
      this.connectSSE();
    },

    async loadData() {
      this.loading = true;
      try {
        const [prospectsRes, statsRes] = await Promise.all([
          fetch(`${API_BASE}/prospects/scored?sort=${this.sortBy}&sort_dir=${this.sortDir}${this.statusFilter ? '&status=' + this.statusFilter : ''}${this.categoryFilter ? '&category=' + this.categoryFilter : ''}${this.minScore ? '&min_score=' + this.minScore : ''}${this.searchQuery ? '&search=' + encodeURIComponent(this.searchQuery) : ''}`),
          fetch(`${API_BASE}/stats/scored`),
        ]);
        this.prospects = await prospectsRes.json();
        this.stats = await statsRes.json();
      } catch (e) {
        console.error('Failed to load data:', e);
      }
      this.loading = false;
    },

    // ── SSE Connection ────────────────────────
    connectSSE() {
      if (this._eventSource) {
        this._eventSource.close();
      }
      const es = new EventSource('/api/events');

      es.onopen = () => {
        this.connected = true;
        console.log('[SSE] Connected');
      };

      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          if (event.type === 'vendor_new') {
            this.handleNewVendor(event.vendor);
          } else if (event.type === 'vendor_updated') {
            this.handleUpdatedVendor(event.vendor);
          }
        } catch (err) {
          // Non-vendor event, ignore
        }
      };

      es.onerror = () => {
        this.connected = false;
        es.close();
        console.log('[SSE] Disconnected, reconnecting in 3s...');
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => this.connectSSE(), 3000);
      };

      this._eventSource = es;
    },

    handleNewVendor(vendor) {
      // Add to live feed (max 50 items)
      this.liveFeed.unshift({
        ...vendor,
        _feedTime: new Date().toISOString(),
        _isNew: true,
      });
      if (this.liveFeed.length > 50) this.liveFeed.pop();

      // Add to table if passes current filters
      if (this.passesFilters(vendor)) {
        this.prospects.unshift(vendor);
      }

      // Update stats
      this.stats.total = (this.stats.total || 0) + 1;
      this.stats.today = (this.stats.today || 0) + 1;
      const score = vendor.referral_score || 0;
      if (score >= 70) this.stats.high_fit = (this.stats.high_fit || 0) + 1;
      else if (score >= 40) this.stats.medium_fit = (this.stats.medium_fit || 0) + 1;
      else this.stats.low_fit = (this.stats.low_fit || 0) + 1;
    },

    handleUpdatedVendor(vendor) {
      const idx = this.prospects.findIndex(p => p.id === vendor.id);
      if (idx !== -1) {
        this.prospects[idx] = vendor;
      }
      // Update detail panel if this vendor is selected
      if (this.selectedVendor && this.selectedVendor.id === vendor.id) {
        this.selectedVendor = vendor;
      }
    },

    passesFilters(vendor) {
      if (this.statusFilter && vendor.status !== this.statusFilter) return false;
      if (this.categoryFilter && vendor.category !== this.categoryFilter) return false;
      if (this.minScore && (vendor.referral_score || 0) < parseInt(this.minScore)) return false;
      return true;
    },

    // ── Sorting ───────────────────────────────
    toggleSort(col) {
      if (this.sortBy === col) {
        this.sortDir = this.sortDir === 'desc' ? 'asc' : 'desc';
      } else {
        this.sortBy = col;
        this.sortDir = 'desc';
      }
      this.loadData();
    },

    sortArrow(col) {
      if (this.sortBy !== col) return '';
      return this.sortDir === 'desc' ? '\u25BC' : '\u25B2';
    },

    // ── Filter actions ────────────────────────
    applyFilters() {
      this.loadData();
    },

    clearFilters() {
      this.statusFilter = '';
      this.categoryFilter = '';
      this.minScore = '';
      this.searchQuery = '';
      this.loadData();
    },

    // ── Detail panel ──────────────────────────
    openDetail(vendor) {
      this.selectedVendor = { ...vendor };
      this.detailOpen = true;
    },

    closeDetail() {
      this.detailOpen = false;
      this.selectedVendor = null;
    },

    async updateStatus(id, newStatus) {
      try {
        await fetch(`${API_BASE}/prospects/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: newStatus }),
        });
        // Update local state
        const idx = this.prospects.findIndex(p => p.id === id);
        if (idx !== -1) this.prospects[idx].status = newStatus;
        if (this.selectedVendor && this.selectedVendor.id === id) {
          this.selectedVendor.status = newStatus;
        }
      } catch (e) {
        console.error('Failed to update status:', e);
      }
    },

    async saveNotes(id, notes) {
      try {
        await fetch(`${API_BASE}/prospects/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ notes }),
        });
        const idx = this.prospects.findIndex(p => p.id === id);
        if (idx !== -1) this.prospects[idx].notes = notes;
      } catch (e) {
        console.error('Failed to save notes:', e);
      }
    },

    // ── Feed click ────────────────────────────
    feedClick(vendor) {
      this.openDetail(vendor);
    },

    // ── Helper getters for templates ──────────
    scoreLabel, scoreClass, starsHtml, formatCategory, formatStatus, timeAgo,
  });

});
