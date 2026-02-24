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
        success: '✅',
        error: '❌',
        info: 'ℹ️',
    };

    const toast = document.createElement('div');
    toast.className = `${colors[type] || colors.info} text-white px-5 py-3 rounded-lg shadow-lg toast-enter flex items-center gap-3 min-w-[280px]`;
    toast.innerHTML = `
        <span class="text-lg">${icons[type] || icons.info}</span>
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
                    <div class="text-4xl mb-3">⚠️</div>
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
