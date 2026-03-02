/* ── Dashboard JavaScript Helpers ────────────────────────── */

/**
 * Make an API call with proper headers and error handling.
 * Redirects to /login on auth failure.
 *
 * @param {string} url - API endpoint
 * @param {string} method - HTTP method
 * @param {object|null} body - Request body (auto-serialized to JSON)
 * @returns {Promise<object>} Response JSON
 */
async function apiCall(url, method = 'GET', body = null) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
    };
    if (body && method !== 'GET') {
        opts.body = JSON.stringify(body);
    }

    try {
        const res = await fetch(url, opts);

        // Auth redirect
        if (res.status === 303 || res.status === 401 || res.redirected) {
            if (res.url && res.url.includes('/login')) {
                window.location.href = '/login';
                return null;
            }
        }

        const data = await res.json();

        if (!res.ok) {
            const msg = data.detail || data.message || 'حدث خطأ غير متوقع';
            showToast(msg, 'error');
            throw new Error(msg);
        }

        return data;
    } catch (err) {
        if (err.message && !err.message.includes('حدث خطأ')) {
            showToast('خطأ في الاتصال بالخادم', 'error');
        }
        throw err;
    }
}

/**
 * Show a temporary toast notification.
 *
 * @param {string} message - Notification text
 * @param {'success'|'error'|'info'} type - Visual style
 */
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const colors = {
        success: 'bg-emerald-500',
        error: 'bg-red-500',
        info: 'bg-blue-500',
    };
    const icons = {
        success: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        error: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        info: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z"/></svg>',
    };

    const toast = document.createElement('div');
    toast.className = `${colors[type] || colors.info} text-white px-5 py-3 rounded-lg shadow-lg toast-enter flex items-center gap-3 min-w-[280px]`;
    toast.innerHTML = `
        <span class="shrink-0">${icons[type] || icons.info}</span>
        <span class="text-sm font-medium">${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.remove('toast-enter');
        toast.classList.add('toast-exit');
        toast.addEventListener('animationend', () => toast.remove());
    }, 3000);
}

/**
 * Show a confirmation dialog.
 *
 * @param {string} message - Confirmation message in Arabic
 * @returns {Promise<boolean>}
 */
function confirmAction(message) {
    return new Promise((resolve) => {
        // Create overlay
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 bg-black/50 z-50 flex items-center justify-center fade-in';
        overlay.innerHTML = `
            <div class="bg-white rounded-2xl shadow-2xl p-6 mx-4 max-w-sm w-full slide-in" dir="rtl">
                <div class="text-center mb-5">
                    <div class="mb-3">
                        <svg class="w-10 h-10 text-amber-500 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/>
                        </svg>
                    </div>
                    <p class="text-gray-700 text-sm leading-relaxed">${message}</p>
                </div>
                <div class="flex gap-3">
                    <button id="confirm-yes"
                        class="flex-1 bg-red-500 hover:bg-red-600 text-white py-2.5 px-4 rounded-xl text-sm font-medium transition-colors">
                        نعم، تأكيد
                    </button>
                    <button id="confirm-no"
                        class="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-700 py-2.5 px-4 rounded-xl text-sm font-medium transition-colors">
                        إلغاء
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        overlay.querySelector('#confirm-yes').addEventListener('click', () => {
            overlay.remove();
            resolve(true);
        });
        overlay.querySelector('#confirm-no').addEventListener('click', () => {
            overlay.remove();
            resolve(false);
        });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                overlay.remove();
                resolve(false);
            }
        });
    });
}

/**
 * Format an ISO date string to a readable Arabic-friendly format.
 *
 * @param {string} isoString - ISO 8601 date string
 * @returns {string} Formatted date
 */
function formatDate(isoString) {
    if (!isoString) return '—';
    try {
        const d = new Date(isoString);
        return d.toLocaleDateString('ar-SA', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    } catch {
        return isoString;
    }
}

/**
 * Format a number with Arabic-style grouping.
 *
 * @param {number} num
 * @returns {string}
 */
function formatNumber(num) {
    if (num == null) return '0';
    return Number(num).toLocaleString('en-US');
}

/**
 * Get Arabic day name from date string.
 *
 * @param {string} dateStr - YYYY-MM-DD
 * @returns {string}
 */
function getArabicDay(dateStr) {
    const days = ['الأحد', 'الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];
    try {
        const d = new Date(dateStr + 'T00:00:00');
        return days[d.getDay()];
    } catch {
        return dateStr;
    }
}


/* ── OTP Input Component ──────────────────────────────── */

function initOTPInputs(containerId, length = 6, onComplete = null) {
    const container = document.getElementById(containerId);
    if (!container) return;

    for (let i = 0; i < length; i++) {
        const input = container.querySelector(`[data-otp-index="${i}"]`);
        if (!input) continue;

        input.addEventListener('input', (e) => {
            const val = e.target.value.replace(/\D/g, '');
            e.target.value = val.slice(-1);
            if (val && i < length - 1) {
                const next = container.querySelector(`[data-otp-index="${i + 1}"]`);
                if (next) next.focus();
            }
            if (val && i === length - 1 && onComplete) {
                const code = getOTPValue(containerId, length);
                if (code.length === length) onComplete(code);
            }
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && !e.target.value && i > 0) {
                const prev = container.querySelector(`[data-otp-index="${i - 1}"]`);
                if (prev) { prev.value = ''; prev.focus(); }
            }
        });

        input.addEventListener('paste', (e) => {
            e.preventDefault();
            const pasted = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, length);
            for (let j = 0; j < pasted.length; j++) {
                const inp = container.querySelector(`[data-otp-index="${j}"]`);
                if (inp) inp.value = pasted[j];
            }
            const focusIdx = Math.min(pasted.length, length - 1);
            const focusEl = container.querySelector(`[data-otp-index="${focusIdx}"]`);
            if (focusEl) focusEl.focus();
            if (pasted.length === length && onComplete) onComplete(pasted);
        });
    }
}

function getOTPValue(containerId, length = 6) {
    const container = document.getElementById(containerId);
    if (!container) return '';
    let code = '';
    for (let i = 0; i < length; i++) {
        const inp = container.querySelector(`[data-otp-index="${i}"]`);
        code += inp ? inp.value : '';
    }
    return code;
}

function clearOTPInputs(containerId, length = 6) {
    const container = document.getElementById(containerId);
    if (!container) return;
    for (let i = 0; i < length; i++) {
        const inp = container.querySelector(`[data-otp-index="${i}"]`);
        if (inp) inp.value = '';
    }
    const first = container.querySelector('[data-otp-index="0"]');
    if (first) first.focus();
}


/* ── Countdown Timer ──────────────────────────────────── */

function startCountdown(totalSeconds, onTick, onExpire = null) {
    let remaining = totalSeconds;
    onTick(remaining);
    const id = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
            clearInterval(id);
            if (onExpire) onExpire();
        } else {
            onTick(remaining);
        }
    }, 1000);
    return { stop() { clearInterval(id); } };
}

function formatCountdown(seconds) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
}


/* ── Password Strength ────────────────────────────────── */

function getPasswordStrength(password) {
    if (!password) return { level: 0, label: '', color: 'bg-gray-200' };
    const len = password.length;
    if (len < 8) return { level: 25, label: 'ضعيفة', color: 'bg-red-500' };
    const hasMixed = /[a-z]/.test(password) && /[A-Z]/.test(password);
    const hasNum = /\d/.test(password);
    const hasSpecial = /[^a-zA-Z0-9]/.test(password);
    if (len >= 12 && hasMixed && hasNum && hasSpecial) return { level: 100, label: 'ممتازة', color: 'bg-emerald-500' };
    if (len >= 12) return { level: 75, label: 'قوية', color: 'bg-emerald-500' };
    return { level: 50, label: 'متوسطة', color: 'bg-yellow-500' };
}


/* ── Relative Time ────────────────────────────────────── */

function timeAgo(isoString) {
    if (!isoString) return '—';
    const now = Date.now();
    const then = new Date(isoString).getTime();
    const seconds = Math.floor((now - then) / 1000);
    if (seconds < 60) return 'الآن';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `منذ ${minutes} دقيقة`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `منذ ${hours} ساعة`;
    const days = Math.floor(hours / 24);
    return `منذ ${days} يوم`;
}


/* ── Auto-logout on Inactivity ────────────────────────── */

(function () {
    const TIMEOUT = 30 * 60 * 1000;
    const WARNING_BEFORE = 5 * 60 * 1000;
    let lastActivity = Date.now();
    let warningShown = false;

    if (window.location.pathname === '/login' || window.location.pathname === '/recover') return;

    ['click', 'keypress', 'mousemove', 'scroll', 'touchstart'].forEach(ev => {
        document.addEventListener(ev, () => {
            lastActivity = Date.now();
            if (warningShown) {
                warningShown = false;
                const w = document.getElementById('inactivity-warning');
                if (w) w.style.display = 'none';
            }
        }, { passive: true });
    });

    setInterval(() => {
        const elapsed = Date.now() - lastActivity;
        if (elapsed >= TIMEOUT) {
            window.location.href = '/logout';
        } else if (elapsed >= TIMEOUT - WARNING_BEFORE && !warningShown) {
            warningShown = true;
            let w = document.getElementById('inactivity-warning');
            if (!w) {
                w = document.createElement('div');
                w.id = 'inactivity-warning';
                w.className = 'fixed top-0 left-0 right-0 z-50 bg-amber-500 text-white text-center py-2 px-4 text-sm font-medium shadow-lg';
                w.textContent = 'ستنتهي جلستك قريباً بسبب عدم النشاط. حرّك الماوس للمتابعة.';
                document.body.appendChild(w);
            }
            w.style.display = 'block';
        }
    }, 30000);
})();


/* ── Sensitive Setting Labels ─────────────────────────── */

const SENSITIVE_LABELS = {
    "telegram.admin_chat_id": "معرّف المسؤول (تلقرام)",
    "telegram.channel_id": "معرّف القناة",
    "amazon.partner_tag": "رمز التتبع (Partner Tag)",
};
