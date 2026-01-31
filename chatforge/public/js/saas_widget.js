/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CHATBOT SAAS - PRODUCTION WIDGET JS
 * Vanilla JavaScript, Zero Dependencies
 * ═══════════════════════════════════════════════════════════════════════════
 */

(function () {
    'use strict';

    // Prevent multiple initializations
    if (window.SaasChatbot) return;

    class SaasChatbot {
        constructor() {
            this.config = {
                token: null,
                host: null, // Host URL from data-host attribute
                botName: 'AI Assistant',
                welcomeMessage: 'Hello! How can I help you today? 👋',
                primaryColor: '#4F46E5',
                secondaryColor: '#818CF8',
                position: null,
                botAvatar: null
            };
            this.state = {
                isOpen: false,
                sessionId: null,
                history: [],
                isTyping: false
            };
            this.elements = {};
            this.STORAGE_KEY = 'saas_chatbot_session';
            this.CONFIG_STORAGE_KEY = 'saas_chatbot_config';
            this.HISTORY_TTL_MS = 1000 * 60 * 60 * 24 * 15; // 15 days
            this.API_BASE = '/api/method/chatforge.api'; // Will be updated with host

            this.init();
        }

        async init() {
            // Get token and host from script tag
            const scriptTag = document.currentScript || document.querySelector('script[data-token]');
            if (scriptTag) {
                if (scriptTag.getAttribute('data-token')) {
                    this.config.token = scriptTag.getAttribute('data-token');
                }
                if (scriptTag.getAttribute('data-host')) {
                    this.config.host = scriptTag.getAttribute('data-host').replace(/\/$/, ''); // Remove trailing slash
                    this.API_BASE = this.config.host + '/api/method/chatforge.api';
                }
            }

            // Load session from storage
            this.loadSession();
            // Load previous config from storage (for instant correct positioning)
            this.loadConfig();
            // Preload history from local storage (fast, no network)
            this.loadHistoryFromStorage();

            // Inject CSS
            this.injectStyles();

            // Build Widget DOM
            this.createWidget();

            // Render any cached history immediately
            this.renderHistory();

            // Bind Events
            this.bindEvents();

            // Fetch config from server
            // If no token provided, try to auto-fetch for same-site usage
            if (this.config.token) {
                await this.fetchConfig();
            } else {
                await this.fetchActiveConfig();
            }

            // Load history from server if token exists
            if (this.config.token) {
                await this.syncHistory();
            }

            console.log('✅ SaaS Chatbot Widget Initialized');
        }

        async fetchActiveConfig() {
            try {
                // Add cache-busting parameter to prevent stale config
                const cacheBuster = `_=${Date.now()}`;
                const response = await fetch(`${this.API_BASE}.get_active_config?${cacheBuster}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Cache-Control': 'no-cache'
                    }
                });
                const data = await response.json();

                if (data.message && !data.message.error) {
                    const cfg = data.message;
                    this.config.token = cfg.api_token;
                    this.config.botName = cfg.bot_name || this.config.botName;
                    this.config.welcomeMessage = cfg.welcome_message || this.config.welcomeMessage;
                    this.config.primaryColor = cfg.primary_color || this.config.primaryColor;
                    this.config.secondaryColor = cfg.secondary_color || cfg.primary_color || this.config.secondaryColor;
                    this.config.botAvatar = cfg.bot_avatar;
                    this.config.position = cfg.widget_position;

                    // Apply config
                    this.elements.botName.textContent = this.config.botName;
                    this.applyTheme(this.config.primaryColor, this.config.secondaryColor);
                    this.applyPosition(this.config.position);
                    this.updateAvatars();
                    this.saveConfig();

                    // Unlock visibility and show only after ALL config is applied correctly
                    this.elements.container.style.setProperty('display', 'flex', 'important');
                    this.elements.container.style.setProperty('opacity', '', '');
                    this.elements.container.style.setProperty('visibility', '', '');

                    // Trigger CSS animations
                    setTimeout(() => {
                        this.elements.container.classList.add('initialized');
                    }, 50);

                    // Show teaser after delay
                    setTimeout(() => this.showTeaser(), 3000);
                } else {
                    console.warn('No active chatbot config found');
                }
            } catch (err) {
                console.warn('Failed to fetch active config:', err);
            }
        }

        injectStyles() {
            // Check if CSS already loaded
            if (!document.querySelector('link[href*="saas_widget.css"]')) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                // Use absolute URL if host is specified (cross-origin)
                const cssPath = '/assets/chatforge/css/saas_widget.css';
                // Add cache buster to CSS to prevent seeing old styles
                const cacheBuster = `v=${Date.now()}`;
                link.href = (this.config.host ? this.config.host + cssPath : cssPath) + '?' + cacheBuster;
                document.head.appendChild(link);
            }

            // Load Inter font if not already loaded
            if (!document.querySelector('link[href*="fonts.googleapis.com/css2?family=Inter"]')) {
                const fontLink = document.createElement('link');
                fontLink.rel = 'stylesheet';
                fontLink.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap';
                document.head.appendChild(fontLink);
            }
        }

        createWidget() {
            const container = document.createElement('div');
            container.className = 'saas-chatbot-container';
            container.id = 'saas-chatbot';

            // Start strictly detached/hidden until configuration is loaded
            container.style.cssText = 'display: none !important; opacity: 0 !important; visibility: hidden !important;';

            // Apply cached position immediately to prevent jumping
            if (this.config.position) {
                const isLeft = this.config.position.toLowerCase().includes('left');
                container.setAttribute('data-position', isLeft ? 'left' : 'right');
            }

            container.innerHTML = `
                <!-- Chat Window -->
                <div class="saas-chat-window" id="saas-chat-window">
                    <!-- Header -->
                    <div class="saas-chat-header">
                        <div class="saas-header-info">
                            <div class="saas-bot-avatar" id="saas-bot-avatar">🤖</div>
                            <div class="saas-bot-details">
                                <h4 id="saas-bot-name">${this.config.botName}</h4>
                                <div class="saas-bot-status">Online</div>
                            </div>
                        </div>
                        <div class="saas-header-actions">
                            <button class="saas-header-btn" id="saas-clear-btn" title="Clear Chat">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <polyline points="3 6 5 6 21 6"></polyline>
                                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                </svg>
                            </button>
                            <button class="saas-header-btn" id="saas-close-btn" title="Close">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <line x1="18" y1="6" x2="6" y2="18"></line>
                                    <line x1="6" y1="6" x2="18" y2="18"></line>
                                </svg>
                            </button>
                        </div>
                    </div>

                    <!-- Messages -->
                    <div class="saas-chat-messages" id="saas-messages"></div>

                    <!-- Quick Replies (Optional) -->
                    <div class="saas-quick-replies" id="saas-quick-replies"></div>

                    <!-- Input Area -->
                    <div class="saas-chat-input-area">
                        <input 
                            type="text" 
                            class="saas-chat-input" 
                            id="saas-input" 
                            placeholder="Type your message..."
                            autocomplete="off"
                        />
                        <button class="saas-send-btn" id="saas-send-btn" title="Send">
                            <svg viewBox="0 0 24 24">
                                <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
                            </svg>
                        </button>
                    </div>

                    <!-- Powered By -->
                    <div class="saas-powered-by">
                        Powered by <a href="https://finbyz.tech" target="_blank">FinByz</a>
                    </div>
                </div>

                <!-- FAB Button -->
                <button class="saas-chat-fab" id="saas-fab">
                    <div class="saas-fab-icon" id="saas-fab-icon">
                        <svg viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
                    </div>
                </button>

                <!-- Teaser Bubble -->
                <div class="saas-teaser-bubble" id="saas-teaser">
                    <span>How can I help you today? 👋</span>
                    <button class="saas-teaser-close" id="saas-teaser-close">×</button>
                </div>
            `;

            document.body.appendChild(container);

            // Store element references
            this.elements = {
                container,
                window: container.querySelector('#saas-chat-window'),
                messages: container.querySelector('#saas-messages'),
                input: container.querySelector('#saas-input'),
                sendBtn: container.querySelector('#saas-send-btn'),
                fab: container.querySelector('#saas-fab'),
                closeBtn: container.querySelector('#saas-close-btn'),
                clearBtn: container.querySelector('#saas-clear-btn'),
                botName: container.querySelector('#saas-bot-name'),
                botAvatar: container.querySelector('#saas-bot-avatar'),
                fabIcon: container.querySelector('#saas-fab-icon'),
                teaser: container.querySelector('#saas-teaser'),
                teaserClose: container.querySelector('#saas-teaser-close'),
                quickReplies: container.querySelector('#saas-quick-replies')
            };
        }

        bindEvents() {
            // Toggle chat
            this.elements.fab.addEventListener('click', () => this.toggle());
            this.elements.closeBtn.addEventListener('click', () => this.toggle());

            // Send message
            this.elements.sendBtn.addEventListener('click', () => this.handleSend());
            this.elements.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.handleSend();
                }
            });

            // Auto-resize input (if it becomes textarea in future)
            this.elements.input.addEventListener('input', () => {
                const hasText = this.elements.input.value.trim().length > 0;
                this.elements.sendBtn.disabled = !hasText;

                // Auto-resize if textarea
                if (this.elements.input.tagName === 'TEXTAREA') {
                    this.elements.input.style.height = 'auto';
                    this.elements.input.style.height = Math.min(this.elements.input.scrollHeight, 120) + 'px';
                }
            });

            // Clear chat
            this.elements.clearBtn.addEventListener('click', () => this.clearChat());

            // Disable send while typing
            this.elements.input.addEventListener('input', () => {
                const hasText = this.elements.input.value.trim().length > 0;
                this.elements.sendBtn.disabled = !hasText;
            });

            // Teaser events
            this.elements.teaser.addEventListener('click', (e) => {
                if (e.target !== this.elements.teaserClose) {
                    this.hideTeaser();
                    this.toggle();
                }
            });
            this.elements.teaserClose.addEventListener('click', (e) => {
                e.stopPropagation();
                this.hideTeaser();
            });
        }

        toggle() {
            this.state.isOpen = !this.state.isOpen;
            this.elements.window.classList.toggle('open', this.state.isOpen);
            this.elements.fab.classList.toggle('open', this.state.isOpen);

            if (this.state.isOpen) {
                this.hideTeaser();
                // Show welcome if first open
                if (this.state.history.length === 0) {
                    this.addMessage(this.config.welcomeMessage, 'bot');
                    this.showQuickReplies();
                }
                setTimeout(() => this.elements.input.focus(), 350);
            }
        }

        showTeaser() {
            if (this.state.isOpen || this.state.teaserShown) return;
            this.elements.teaser.classList.add('visible');
            this.state.teaserShown = true;
        }

        hideTeaser() {
            this.elements.teaser.classList.remove('visible');
        }

        async fetchConfig() {
            try {
                // Add cache-busting parameter to prevent stale config
                const cacheBuster = `_=${Date.now()}`;
                const response = await fetch(`${this.API_BASE}.get_config?${cacheBuster}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Cache-Control': 'no-cache'
                    },
                    body: new URLSearchParams({ token: this.config.token })
                });
                const data = await response.json();

                if (data.message) {
                    const cfg = data.message;

                    // Check if token is invalid - hide widget completely
                    if (cfg.valid === false || cfg.error) {
                        console.error('Chatbot SaaS: ' + (cfg.error || 'Invalid configuration'));
                        this.hideWidget();
                        return;
                    }

                    this.config.primaryColor = cfg.primary_color || this.config.primaryColor;
                    this.config.secondaryColor = cfg.secondary_color || cfg.primary_color || this.config.secondaryColor;
                    this.config.botAvatar = cfg.bot_avatar;
                    this.config.position = cfg.widget_position;

                    // Apply config
                    this.elements.botName.textContent = this.config.botName;
                    this.applyTheme(this.config.primaryColor, this.config.secondaryColor);
                    this.applyPosition(this.config.position);
                    this.updateAvatars();
                    this.saveConfig();

                    // Unlock visibility and show only after ALL config is applied correctly
                    this.elements.container.style.setProperty('display', 'flex', 'important');
                    this.elements.container.style.setProperty('opacity', '', '');
                    this.elements.container.style.setProperty('visibility', '', '');

                    // Trigger CSS animations
                    setTimeout(() => {
                        this.elements.container.classList.add('initialized');
                    }, 50);

                    this.state.configLoaded = true;

                    // Show teaser after delay
                    setTimeout(() => this.showTeaser(), 3000);
                }
            } catch (err) {
                console.error('Chatbot SaaS: Failed to fetch config', err);
                this.hideWidget();
            }
        }

        hideWidget() {
            // Remove widget from DOM if token/config is invalid
            if (this.elements.container) {
                this.elements.container.style.display = 'none';
            }
        }

        applyPosition(position) {
            // Default to 'right' if no position is specified in config
            const pos = position || 'right';
            const isLeft = pos.toLowerCase().includes('left');
            const container = this.elements.container;
            if (!container) return;

            // Set data attribute for CSS - this is the source of truth for positioning
            container.setAttribute('data-position', isLeft ? 'left' : 'right');
        }

        applyTheme(primary, secondary) {
            const container = this.elements.container;
            if (!container) return;
            container.style.setProperty('--saas-primary', primary);
            container.style.setProperty('--saas-secondary', secondary);

            // Generate darker variant for primary (approximate)
            const dark = this.adjustColor(primary, -30);
            container.style.setProperty('--saas-primary-dark', dark);

            // Glow
            container.style.setProperty('--saas-primary-glow', primary + '66'); // 40% opacity
        }

        updateAvatars() {
            if (!this.config.botAvatar) return;
            const fullUrl = (this.config.host || '') + this.config.botAvatar;
            const imgHtml = `<img src="${fullUrl}" alt="${this.config.botName}" />`;

            this.elements.botAvatar.innerHTML = imgHtml;
        }

        adjustColor(hex, amount) {
            return '#' + hex.replace(/^#/, '').replace(/../g, char => {
                const val = parseInt(char, 16);
                const newCol = Math.max(0, Math.min(255, val + amount)).toString(16);
                return (newCol.length === 1 ? '0' : '') + newCol;
            });
        }

        loadSession() {
            try {
                const stored = localStorage.getItem(this.STORAGE_KEY);
                if (stored) {
                    const data = JSON.parse(stored);
                    this.state.sessionId = data.sessionId;
                }
            } catch (e) {
                console.warn('Failed to load session:', e);
            }

            if (!this.state.sessionId) {
                this.state.sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
                this.saveSession();
            }
        }

        saveConfig() {
            try {
                const configToSave = {
                    position: this.config.position,
                    primaryColor: this.config.primaryColor,
                    secondaryColor: this.config.secondaryColor,
                    botName: this.config.botName,
                    botAvatar: this.config.botAvatar
                };
                localStorage.setItem(this.CONFIG_STORAGE_KEY, JSON.stringify(configToSave));
            } catch (e) {
                console.warn('Failed to save config:', e);
            }
        }

        loadConfig() {
            try {
                const stored = localStorage.getItem(this.CONFIG_STORAGE_KEY);
                if (stored) {
                    const data = JSON.parse(stored);
                    this.config.position = data.position || this.config.position;
                    this.config.primaryColor = data.primaryColor || this.config.primaryColor;
                    this.config.secondaryColor = data.secondaryColor || this.config.secondaryColor;
                    this.config.botName = data.botName || this.config.botName;
                    this.config.botAvatar = data.botAvatar || this.config.botAvatar;
                }
            } catch (e) {
                console.warn('Failed to load config:', e);
            }
        }

        saveSession() {
            try {
                localStorage.setItem(this.STORAGE_KEY, JSON.stringify({
                    sessionId: this.state.sessionId
                }));
            } catch (e) {
                console.warn('Failed to save session:', e);
            }
        }

        historyStorageKey() {
            const tokenPart = this.config.token || 'default';
            return `${this.STORAGE_KEY}:${tokenPart}:history`;
        }

        loadHistoryFromStorage() {
            try {
                const stored = localStorage.getItem(this.historyStorageKey());
                if (!stored) return;

                const data = JSON.parse(stored);
                const expiresAt = data.expiresAt || 0;
                const now = Date.now();
                if (expiresAt && now > expiresAt) {
                    localStorage.removeItem(this.historyStorageKey());
                    return;
                }

                if (Array.isArray(data.history)) {
                    this.state.history = data.history;
                }
                if (!this.state.sessionId && data.sessionId) {
                    this.state.sessionId = data.sessionId;
                }
            } catch (e) {
                console.warn('Failed to load history:', e);
            }
        }

        saveHistoryToStorage() {
            try {
                const payload = {
                    history: this.state.history,
                    sessionId: this.state.sessionId,
                    expiresAt: Date.now() + this.HISTORY_TTL_MS
                };
                localStorage.setItem(this.historyStorageKey(), JSON.stringify(payload));
            } catch (e) {
                console.warn('Failed to persist history:', e);
            }
        }

        async syncHistory() {
            // If we already have local history, prefer it and skip network
            if (this.state.history && this.state.history.length > 0) return;
            if (!this.config.token) return;

            try {
                const response = await fetch(`${this.API_BASE}.get_history`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({
                        token: this.config.token,
                        session_id: this.state.sessionId
                    })
                });
                const data = await response.json();

                if (data.message && Array.isArray(data.message)) {
                    this.state.history = data.message;
                    this.saveHistoryToStorage();
                    this.renderHistory();
                }
            } catch (err) {
                console.warn('Failed to sync history:', err);
            }
        }

        renderHistory() {
            this.elements.messages.innerHTML = '';
            this.state.history.forEach(msg => {
                this.addMessageToUI(msg.content, msg.sender === 'User' ? 'user' : 'bot', false);
            });
        }

        async handleSend() {
            const text = this.elements.input.value.trim();
            if (!text || this.state.isTyping) return;

            // Clear input
            this.elements.input.value = '';
            this.elements.sendBtn.disabled = true;

            // Hide quick replies
            this.hideQuickReplies();

            // Add user message
            this.addMessage(text, 'user');

            // Show typing
            this.showTyping();

            try {
                const response = await this.sendToAI(text);
                this.hideTyping();

                if (response.success) {
                    this.addMessage(response.response, 'bot');
                } else {
                    this.addMessage(response.response || 'Sorry, I encountered an error. Please try again.', 'bot');
                }
            } catch (err) {
                this.hideTyping();
                this.addMessage('Connection error. Please try again.', 'bot');
                console.error('Send error:', err);
            }
        }

        async sendToAI(query) {
            // Format history for the backend with 'role' key (user/bot)
            // This enables the AI to remember the full conversation context
            const formattedHistory = this.state.history.slice(-10).map(msg => ({
                role: msg.sender === 'User' ? 'user' : 'bot',
                content: msg.content
            }));

            const formData = new URLSearchParams({
                token: this.config.token || '',
                session_id: this.state.sessionId,
                query: query,
                history: JSON.stringify(formattedHistory)
            });

            const response = await fetch(`${this.API_BASE}.send_message`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                body: formData
            });

            return await response.json().then(d => d.message || d);
        }

        addMessage(text, sender) {
            // Skip blank messages
            if (!text || (typeof text === 'string' && !text.trim())) {
                console.warn('Skipping blank message');
                return;
            }

            // Add to history
            this.state.history.push({
                content: text,
                sender: sender === 'user' ? 'User' : 'Bot',
                timestamp: new Date().toISOString()
            });
            this.saveHistoryToStorage();

            // Add to UI
            this.addMessageToUI(text, sender, true);

            // Note: Messages are automatically saved to DB by send_message API endpoint
            // No need to call saveMessageToServer separately to avoid duplicates
        }

        addMessageToUI(text, sender, animate = true) {
            const msgDiv = document.createElement('div');
            msgDiv.className = `saas-message ${sender}`;
            if (!animate) msgDiv.style.animation = 'none';

            const now = new Date();
            const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

            // Render markdown for bot messages, plain text for user messages
            const messageContent = sender === 'bot' ? this.renderMarkdown(text) : this.escapeHtml(text);

            msgDiv.innerHTML = `
                <div class="saas-message-bubble">
                    ${messageContent}
                    ${sender === 'bot' ? '<button class="saas-copy-btn" title="Copy message" aria-label="Copy message">📋</button>' : ''}
                </div>
                <div class="saas-message-time">${timeStr}</div>
            `;

            this.elements.messages.appendChild(msgDiv);

            // Add copy functionality for bot messages
            if (sender === 'bot') {
                const copyBtn = msgDiv.querySelector('.saas-copy-btn');
                if (copyBtn) {
                    copyBtn.addEventListener('click', () => this.copyMessage(text, copyBtn));
                }
            }

            this.scrollToBottom();
        }

        async copyMessage(text, button) {
            try {
                await navigator.clipboard.writeText(text);
                const originalHTML = button.innerHTML;
                button.innerHTML = '✓';
                button.classList.add('copied');
                setTimeout(() => {
                    button.innerHTML = originalHTML;
                    button.classList.remove('copied');
                }, 2000);
            } catch (err) {
                console.warn('Failed to copy:', err);
            }
        }

        async saveMessageToServer(text, sender) {
            if (!this.config.token) return;

            try {
                await fetch(`${this.API_BASE}.save_message`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({
                        token: this.config.token,
                        session_id: this.state.sessionId,
                        message: text,
                        sender: sender === 'user' ? 'User' : 'Bot'
                    })
                });
            } catch (err) {
                console.warn('Failed to save message:', err);
            }
        }

        showTyping() {
            this.state.isTyping = true;
            const typing = document.createElement('div');
            typing.className = 'saas-message bot';
            typing.id = 'saas-typing';
            typing.innerHTML = `
                <div class="saas-typing-indicator">
                    <span></span><span></span><span></span>
                </div>
            `;
            this.elements.messages.appendChild(typing);
            this.scrollToBottom();
        }

        hideTyping() {
            this.state.isTyping = false;
            const typing = document.getElementById('saas-typing');
            if (typing) typing.remove();
        }

        showQuickReplies() {
            const replies = [
                '👋 What can you do?',
                '📋 Our Services',
                '📞 Contact Us'
            ];

            this.elements.quickReplies.innerHTML = replies.map(r =>
                `<button class="saas-quick-reply-btn">${r}</button>`
            ).join('');

            this.elements.quickReplies.querySelectorAll('button').forEach(btn => {
                btn.addEventListener('click', () => {
                    this.elements.input.value = btn.textContent;
                    this.handleSend();
                });
            });
        }

        hideQuickReplies() {
            this.elements.quickReplies.innerHTML = '';
        }

        clearChat() {
            if (!confirm('Clear chat history?')) return;

            this.state.history = [];
            this.elements.messages.innerHTML = '';
            // Clear cached history
            try {
                localStorage.removeItem(this.historyStorageKey());
            } catch (e) {
                console.warn('Failed to clear cached history:', e);
            }

            // Generate new session
            this.state.sessionId = 'sess_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
            this.saveSession();

            // Show welcome again
            this.addMessage(this.config.welcomeMessage, 'bot');
            this.showQuickReplies();
        }

        scrollToBottom() {
            this.elements.messages.scrollTop = this.elements.messages.scrollHeight;
        }

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        renderMarkdown(text) {
            // Simple markdown parser for common formatting
            let html = this.escapeHtml(text);

            // Bold: **text** or __text__
            html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

            // Italic: *text* or _text_
            html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
            html = html.replace(/(?<!_)_(?!_)(.+?)(?<!_)_(?!_)/g, '<em>$1</em>');

            // Code: `code`
            html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

            // Code blocks: ```code```
            html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');

            // Markdown links: [text](url) - must come before plain URL detection
            html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

            // Auto-link plain URLs that are NOT already inside an href
            // Match URLs that are not preceded by href=" or > (meaning already in a link)
            html = html.replace(/(?<!href="|>)(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');

            // Bullet points: lines starting with * or - or •
            html = html.replace(/^[\*\-•]\s+(.+)$/gm, '<li>$1</li>');
            // Wrap consecutive <li> items in <ul>
            html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

            // Line breaks
            html = html.replace(/\n/g, '<br>');

            return html;
        }
    }

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            window.SaasChatbot = new SaasChatbot();
        });
    } else {
        window.SaasChatbot = new SaasChatbot();
    }

})();
