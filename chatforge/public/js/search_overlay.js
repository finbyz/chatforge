/**
 * Kersten UK — AI-Powered Website Search Overlay
 * Premium consultant-style UI inspired by Claude/ChatGPT
 * Hooks into the Frappe navbar search — website pages only
 */

class ChatforgeSearch {
    constructor() {
        this.isOpen = false;
        this.fingerprint = null;
        this.sessionId = 'sess_' + Math.random().toString(36).substr(2, 9);
        this.selectedIndex = -1;
        this._statusTimer = null;
        this._statusIdx = 0;
        this._statusMsgs = [
            'Searching Kersten UK...',
            'Consulting our product range...',
            'Gathering the best matches...',
            'Almost done...',
            'One last tweak...',
        ];
        this.init();
    }

    async init() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.setup());
        } else {
            this.setup();
        }
    }

    async setup() {
        this.config = await this.getSearchConfig();
        if (!this.config || !this.config.enabled) return;
        this.buildOverlay();
        this.bindEvents();
        this.hijackFrappeSearch();
        await this.generateFingerprint();
    }

    async getSearchConfig() {
        try {
            const res = await fetch('/api/method/chatforge.search_api.get_search_config', {
                headers: { 'Accept': 'application/json' },
            });
            const data = await res.json();
            return data.message || { enabled: false };
        } catch (err) {
            console.warn('[ChatforgeSearch] Search config unavailable:', err);
            return { enabled: false };
        }
    }

    // ──────────────────────────────────────────
    // FINGERPRINT
    // ──────────────────────────────────────────
    async generateFingerprint() {
        const cached = sessionStorage.getItem('cf_fp');
        if (cached) { this.fingerprint = cached; return; }
        try {
            const res = await fetch('/api/method/chatforge.search_api.get_client_ip');
            const data = await res.json();
            const ip = (data.message && data.message.success) ? data.message.ip : 'unknown';
            const raw = navigator.userAgent + ip;
            const buf = new TextEncoder().encode(raw);
            const hash = await crypto.subtle.digest('SHA-256', buf);
            const hex = Array.from(new Uint8Array(hash))
                .map(b => b.toString(16).padStart(2, '0')).join('');
            this.fingerprint = hex.substring(0, 16);
            sessionStorage.setItem('cf_fp', this.fingerprint);
        } catch (_) {
            this.fingerprint = 'fb_' + Math.random().toString(36).substr(2, 9);
        }
    }

    // ──────────────────────────────────────────
    // BUILD DOM
    // ──────────────────────────────────────────
    buildOverlay() {
        // Inject CSS (cache-busted for dev)
        if (!document.getElementById('cf-search-overlay-style')) {
            const link = document.createElement('link');
            link.id = 'cf-search-overlay-style';
            link.rel = 'stylesheet';
            link.href = '/assets/chatforge/css/search_overlay.css';
            document.head.appendChild(link);
        }

        if (document.getElementById('chatforge-search-overlay')) return;

        document.body.insertAdjacentHTML('beforeend', `
            <div id="chatforge-search-overlay" class="cf-search-overlay" role="dialog" aria-modal="true" aria-label="AI Search">

                <div class="cf-search-container">

                    <!-- ── Search Bar ── -->
                    <div class="cf-search-bar-wrap">
                        <svg class="cf-search-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                        </svg>
                        <input
                            type="text"
                            id="cf-search-input"
                            class="cf-search-input"
                            placeholder="Ask our AI — e.g. best sweeper for winter maintenance..."
                            autocomplete="off"
                            spellcheck="false"
                            aria-label="AI search input"
                        >
                        <button id="cf-clear-btn" class="cf-clear-btn" aria-label="Clear search">&times;</button>
                        <div id="cf-enter-hint" class="cf-enter-hint">Enter &crarr;</div>
                        <div id="cf-close-btn" class="cf-esc-badge" role="button" tabindex="0" aria-label="Close search">ESC</div>
                    </div>

                    <!-- ── Results Panel ── -->
                    <div id="cf-results-wrap" class="cf-results-wrap">

                        <!-- Recent searches -->
                        <div id="cf-recent-wrap" class="cf-recent-wrap">
                            <div class="cf-recent-label">Recent Searches</div>
                            <div id="cf-recent-chips" class="cf-recent-chips"></div>
                        </div>

                        <!-- Loading skeleton + status -->
                        <div id="cf-status-wrap" class="cf-status-wrap">
                            <div class="cf-skeleton-rows">
                                <div class="cf-skel-row">
                                    <div class="cf-skel-box"></div>
                                    <div class="cf-skel-lines">
                                        <div class="cf-skel-line short"></div>
                                        <div class="cf-skel-line long"></div>
                                        <div class="cf-skel-line med"></div>
                                    </div>
                                </div>
                                <div class="cf-skel-row">
                                    <div class="cf-skel-box"></div>
                                    <div class="cf-skel-lines">
                                        <div class="cf-skel-line short"></div>
                                        <div class="cf-skel-line long"></div>
                                    </div>
                                </div>
                                <div class="cf-skel-row">
                                    <div class="cf-skel-box"></div>
                                    <div class="cf-skel-lines">
                                        <div class="cf-skel-line short"></div>
                                        <div class="cf-skel-line med"></div>
                                        <div class="cf-skel-line long"></div>
                                    </div>
                                </div>
                            </div>
                            <div class="cf-status-bar">
                                <div class="cf-status-dot"></div>
                                <span id="cf-status-text" class="cf-status-text">Searching Kersten UK...</span>
                            </div>
                        </div>

                        <!-- AI Consultant Answer -->
                        <div id="cf-consultant-box" class="cf-consultant-box">
                            <div class="cf-consultant-header">
                                <span id="cf-consultant-avatar" class="cf-consultant-avatar"></span>
                                <span class="cf-consultant-kicker">
                                    <span class="cf-consultant-dot"></span>
                                    Kersten Expert
                                </span>
                            </div>
                            <div id="cf-consultant-answer" class="cf-consultant-answer"></div>
                        </div>

                        <!-- Products header -->
                        <div id="cf-results-header" class="cf-results-header">
                            <svg viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 3h14M5 3a2 2 0 00-2 2v16l7-3 7 3V5a2 2 0 00-2-2H5z"/></svg>
                            Recommended Products &amp; Pages
                        </div>

                        <!-- Result list -->
                        <div id="cf-results-list" class="cf-results-list" role="list"></div>

                        <!-- Count -->
                        <div id="cf-results-count" class="cf-results-count"></div>

                        <!-- Consultation CTA -->
                        <div id="cf-contact-cta" class="cf-contact-cta">
                            <span>Need help choosing?</span>
                            <a href="/contact" class="cf-contact-link">Book a free consultation</a>
                        </div>

                        <!-- No results -->
                        <div id="cf-no-results" class="cf-no-results" role="status">
                            <svg viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                            <div class="cf-no-results-title" id="cf-no-results-text">No results found</div>
                            <a href="/search-by-category" class="cf-no-results-sub">Browse all products &rarr;</a>
                        </div>

                    </div><!-- /cf-results-wrap -->
                </div><!-- /cf-search-container -->
            </div><!-- /chatforge-search-overlay -->
        `);

        // Cache DOM refs
        this.overlay = document.getElementById('chatforge-search-overlay');
        this.input = document.getElementById('cf-search-input');
        this.clearBtn = document.getElementById('cf-clear-btn');
        this.enterHint = document.getElementById('cf-enter-hint');
        this.closeBtn = document.getElementById('cf-close-btn');
        this.resultsWrap = document.getElementById('cf-results-wrap');
        this.recentWrap = document.getElementById('cf-recent-wrap');
        this.recentChips = document.getElementById('cf-recent-chips');
        this.statusWrap = document.getElementById('cf-status-wrap');
        this.statusText = document.getElementById('cf-status-text');
        this.consultantBox = document.getElementById('cf-consultant-box');
        this.consultantAvatar = document.getElementById('cf-consultant-avatar');
        this.consultantAnswer = document.getElementById('cf-consultant-answer');
        this.resultsHeader = document.getElementById('cf-results-header');
        this.resultsList = document.getElementById('cf-results-list');
        this.resultsCount = document.getElementById('cf-results-count');
        this.contactCta = document.getElementById('cf-contact-cta');
        this.noResults = document.getElementById('cf-no-results');
        this.noResultsText = document.getElementById('cf-no-results-text');
    }

    // ──────────────────────────────────────────
    // EVENTS
    // ──────────────────────────────────────────
    bindEvents() {
        // Global keyboard
        document.addEventListener('keydown', (e) => {
            if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
                e.preventDefault();
                this.open();
            }
            if (e.key === 'Escape' && this.isOpen) this.close();
        });

        // Click backdrop to close
        this.overlay.addEventListener('click', (e) => {
            if (e.target === this.overlay) this.close();
        });

        // ESC button
        this.closeBtn.addEventListener('click', () => this.close());
        this.closeBtn.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.close(); });

        // Clear
        this.clearBtn.addEventListener('click', () => {
            this.input.value = '';
            this.handleInputState();
            this.showHistory();
        });

        // Input typed
        this.input.addEventListener('input', () => this.handleInputState());

        // Keyboard navigation
        this.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const items = this.resultsList.querySelectorAll('.cf-search-item');
                if (this.selectedIndex >= 0 && items[this.selectedIndex]) {
                    window.location.href = items[this.selectedIndex].getAttribute('href');
                    return;
                }
                const q = this.input.value.trim();
                if (q.length >= 2) this.performSearch(q);
            } else if (e.key === 'ArrowDown') {
                e.preventDefault(); this.navigateList(1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault(); this.navigateList(-1);
            }
        });
    }

    handleInputState() {
        const val = this.input.value.trim();
        this.clearBtn.style.display = val ? 'block' : 'none';
        this.enterHint.style.display = val ? 'block' : 'none';
        if (val) this.recentWrap.style.display = 'none';
    }

    navigateList(dir) {
        const items = this.resultsList.querySelectorAll('.cf-search-item');
        if (!items.length) return;
        if (this.selectedIndex >= 0) items[this.selectedIndex].classList.remove('focused');
        this.selectedIndex = (this.selectedIndex + dir + items.length) % items.length;
        items[this.selectedIndex].classList.add('focused');
        items[this.selectedIndex].scrollIntoView({ block: 'nearest' });
    }

    // ──────────────────────────────────────────
    // FRAPPE NAVBAR HIJACK
    // Makes the existing Frappe navbar search open our overlay
    // ──────────────────────────────────────────
    hijackFrappeSearch() {
        const doHijack = () => {
            // Target Frappe website navbar search input
            const targets = document.querySelectorAll(
                '.navbar-modal-search, input[name="query"], .search-bar input'
            );

            targets.forEach((el) => {
                if (el.dataset.cfHijacked) return;  // already done
                el.dataset.cfHijacked = 'true';

                // Style it as a non-editable trigger
                el.setAttribute('readonly', 'true');
                el.setAttribute('placeholder', 'AI Search');
                el.setAttribute('title', 'AI Search - press /');
                el.style.cursor = 'pointer';
                el.classList.add('cf-navbar-search-trigger');

                const parent = el.parentElement;
                if (parent && !parent.querySelector('.cf-navbar-shortcut')) {
                    parent.classList.add('cf-navbar-search-wrap');
                    const shortcut = document.createElement('button');
                    shortcut.type = 'button';
                    shortcut.className = 'cf-navbar-shortcut';
                    shortcut.setAttribute('aria-label', 'Open AI search');
                    shortcut.setAttribute('title', 'Press / to search');
                    shortcut.textContent = '/';
                    shortcut.addEventListener('mousedown', (e) => e.preventDefault(), true);
                    shortcut.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        this.open();
                    }, true);
                    parent.appendChild(shortcut);
                }

                // Prevent Frappe's own modal from opening
                el.addEventListener('mousedown', (e) => e.preventDefault(), true);
                el.addEventListener('focus', (e) => {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    el.blur();
                    this.open();
                }, true);
                el.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    this.open();
                }, true);

                // Block any form submit
                const form = el.closest('form');
                if (form) form.addEventListener('submit', (e) => e.preventDefault(), true);
            });

            // Disable Frappe's internal search binding
            if (window.frappe) {
                frappe.bind_navbar_search = () => { };
                if (frappe.search) frappe.search.show_awesomepopup = () => { };
            }
        };

        // Run now + after Frappe's async navbar render
        doHijack();
        setTimeout(doHijack, 400);
        setTimeout(doHijack, 1200);
        setTimeout(doHijack, 3000);
    }

    // ──────────────────────────────────────────
    // OPEN / CLOSE
    // ──────────────────────────────────────────
    open() {
        if (this.isOpen) return;
        this.isOpen = true;
        this.overlay.classList.remove('closing');
        this.overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
        setTimeout(() => this.input.focus(), 80);
        this.input.value = '';
        this.handleInputState();
        this._resetPanel();
        this.showHistory();
    }

    close() {
        if (!this.isOpen) return;
        this.isOpen = false;
        this._stopStatus();
        this.overlay.classList.add('closing');
        setTimeout(() => {
            this.overlay.classList.remove('active', 'closing');
            document.body.style.overflow = '';
        }, 160);
    }

    // ──────────────────────────────────────────
    // RECENT HISTORY
    // ──────────────────────────────────────────
    async showHistory() {
        try {
            const params = new URLSearchParams();
            if (this.fingerprint) params.append('fingerprint', this.fingerprint);
            const res = await fetch('/api/method/chatforge.search_api.get_recent_history?' + params);
            const data = await res.json();
            const hist = data.message && data.message.success && data.message.history;

            if (hist && hist.length > 0) {
                this.resultsWrap.style.display = 'flex';
                this.recentWrap.style.display = 'block';
                this.recentChips.innerHTML = hist.map(q =>
                    `<div class="cf-recent-chip" data-q="${this.escHtml(q)}">${this.escHtml(q)}</div>`
                ).join('');
                this.recentChips.querySelectorAll('.cf-recent-chip').forEach(chip => {
                    chip.addEventListener('click', () => {
                        const q = chip.getAttribute('data-q');
                        this.input.value = q;
                        this.handleInputState();
                        this.performSearch(q, { useCache: true });
                    });
                });
            }
        } catch (_) { }
    }

    // ──────────────────────────────────────────
    // STATUS ANIMATION
    // ──────────────────────────────────────────
    _startStatus() {
        this._statusIdx = 0;
        this.statusText.textContent = this._statusMsgs[0];
        this._statusTimer = setInterval(() => {
            this._statusIdx = (this._statusIdx + 1) % this._statusMsgs.length;
            // Force re-trigger CSS animation by cloning
            const old = this.statusText;
            const clone = old.cloneNode(false);
            clone.textContent = this._statusMsgs[this._statusIdx];
            old.parentNode.replaceChild(clone, old);
            this.statusText = clone;
        }, 2200);
    }

    _stopStatus() {
        if (this._statusTimer) { clearInterval(this._statusTimer); this._statusTimer = null; }
    }

    // ──────────────────────────────────────────
    // RESET PANEL
    // ──────────────────────────────────────────
    _resetPanel() {
        this.statusWrap.style.display = 'none';
        this.consultantBox.style.display = 'none';
        this.resultsHeader.style.display = 'none';
        this.resultsList.innerHTML = '';
        this.resultsCount.style.display = 'none';
        this.contactCta.style.display = 'none';
        this.noResults.style.display = 'none';
        this.recentWrap.style.display = 'none';
        this.resultsWrap.style.display = 'none';
    }

    // ──────────────────────────────────────────
    // SEARCH
    // ──────────────────────────────────────────
    async performSearch(query, options = {}) {
        if (!query || query.length < 2) return;

        this.activeQuery = query;
        this.selectedIndex = -1;
        this._resetPanel();
        this.resultsWrap.style.display = 'flex';
        this.statusWrap.style.display = 'flex';
        this._startStatus();

        const headers = { 'Accept': 'application/json', 'Content-Type': 'application/json' };
        if (window.frappe && frappe.csrf_token && frappe.csrf_token !== 'None') {
            headers['X-Frappe-CSRF-Token'] = frappe.csrf_token;
        }

        const body = JSON.stringify({
            query,
            fingerprint: this.fingerprint,
            session_id: this.sessionId,
            use_cache: !!options.useCache,
        });

        try {
            const response = await fetch('/api/method/chatforge.search_api.search', {
                method: 'POST', headers, body,
            });
            const data = await response.json();

            this._stopStatus();
            this.statusWrap.style.display = 'none';

            if (data.message && data.message.success) {
                const msg = data.message;

                // 1. Consultant answer (lead with this)
                if (msg.ai_summary) {
                    this.renderConsultantAvatar();
                    this.consultantAnswer.innerHTML = this.formatAnswer(msg.ai_summary, query, msg.results || []);
                    this.consultantBox.style.display = 'block';
                }

                // 2. Product / page links below
                if (msg.results && msg.results.length > 0) {
                    this.resultsHeader.style.display = 'flex';
                    this.renderList(msg.results, query);
                    const n = msg.results.length;
                    this.resultsCount.textContent = `${n} result${n !== 1 ? 's' : ''} found`;
                    this.resultsCount.style.display = 'block';
                }

                // Nothing at all
                if (!msg.ai_summary && (!msg.results || !msg.results.length)) {
                    this.showEmpty(query);
                } else {
                    this.contactCta.style.display = 'flex';
                }
            } else {
                this.showEmpty(query);
            }
        } catch (err) {
            console.error('[ChatforgeSearch] Error:', err);
            this._stopStatus();
            this.statusWrap.style.display = 'none';
            this.showEmpty(query);
        }
    }

    // ──────────────────────────────────────────
    // RENDER RESULTS (max 5 cards)
    // ──────────────────────────────────────────
    renderList(results, query = '') {
        const limit = Math.min(Math.max(Number(this.config && this.config.result_limit) || 9, 1), 12);
        this.resultsList.innerHTML = results
            .slice(0, limit)
            .map(item => this.cardHtml(item, query))
            .join('');
    }

    showEmpty(query) {
        this.noResultsText.textContent = `No results for "${this.escHtml(query)}"`;
        this.noResults.style.display = 'flex';
        this.contactCta.style.display = 'flex';
    }

    renderConsultantAvatar() {
        const avatar = this.config && this.config.bot_avatar;
        if (avatar) {
            this.consultantAvatar.innerHTML = `<img src="${this.escHtml(avatar)}" alt="">`;
        } else {
            this.consultantAvatar.textContent = 'K';
        }
    }

    // ──────────────────────────────────────────
    // HTML HELPERS
    // ──────────────────────────────────────────
    formatAnswer(text, query = '', results = []) {
        if (!text) return '';
        let s = this.escHtml(text);
        // Markdown bold -> linked product/page when a returned result title matches.
        s = s.replace(/\*\*(.*?)\*\*/g, (_, phrase) => {
            const url = this.findResultUrlForPhrase(phrase, results);
            if (!url) return `<strong>${phrase}</strong>`;
            return `<a href="${this.escAttr(url)}" class="cf-summary-link"><strong>${phrase}</strong></a>`;
        });
        s = this.highlightHtml(s, query);
        // Paragraph breaks
        s = s.replace(/\n\n/g, '</p><p>');
        s = s.replace(/\n/g, '<br>');
        return `<p>${s}</p>`;
    }

    cardHtml(item, query = '') {
        const thumb = item.image
            ? `<div class="cf-item-thumb"><img src="${this.escAttr(item.image)}" alt="" loading="lazy"></div>`
            : `<div class="cf-item-thumb">
                   <svg viewBox="0 0 24 24">
                       <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                             d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                   </svg>
               </div>`;

        return `
            <a href="${this.escAttr(item.url || '#')}" class="cf-search-item" role="listitem">
                ${thumb}
                    <div class="cf-item-content">
                        <div class="cf-item-badge">${this.escHtml(item.doctype || 'Page')}</div>
                    <div class="cf-item-title">${this.highlightText(item.title, query)}</div>
                    <div class="cf-item-desc">${this.highlightText(item.description || '', query)}</div>
                </div>
                <div class="cf-item-arrow">&rsaquo;</div>
            </a>`;
    }

    escHtml(s) {
        if (!s) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    escAttr(s) {
        return this.escHtml(s).replace(/`/g, '&#096;');
    }

    normalizeText(s) {
        return String(s || '')
            .toLowerCase()
            .replace(/&amp;/g, '&')
            .replace(/&#039;/g, "'")
            .replace(/&quot;/g, '"')
            .replace(/[^a-z0-9]+/g, ' ')
            .trim();
    }

    findResultUrlForPhrase(phrase, results = []) {
        const needle = this.normalizeText(phrase);
        if (!needle) return '';

        const match = (results || []).find(item => {
            const title = this.normalizeText(item && item.title);
            return title && (title === needle || title.includes(needle) || needle.includes(title));
        });

        return match && match.url ? match.url : '';
    }

    getHighlightTerms(query) {
        const stopWords = new Set(['the', 'and', 'for', 'our', 'your', 'with', 'best', 'what', 'which', 'from', 'this', 'that']);
        return Array.from(new Set(String(query || '')
            .toLowerCase()
            .split(/[^a-z0-9]+/i)
            .filter(term => term.length > 2 && !stopWords.has(term))));
    }

    highlightHtml(html, query) {
        const terms = this.getHighlightTerms(query);
        if (!terms.length || !html) return html;

        return String(html).split(/(<[^>]+>)/g).map(part => {
            if (part.startsWith('<')) return part;
            let value = part;
            terms.forEach(term => {
                const escapedTerm = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const regex = new RegExp(`(${escapedTerm})`, 'gi');
                value = value.replace(regex, '<mark class="cf-hit">$1</mark>');
            });
            return value;
        }).join('');
    }

    highlightText(text, query) {
        let value = this.escHtml(text);
        return this.highlightHtml(value, query);
    }
}

// Boot
window.ChatforgeSearchInstance = new ChatforgeSearch();
