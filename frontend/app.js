/**
 * ═══════════════════════════════════════════════════════════════════
 * TxnRanker – Frontend Application Logic
 *
 * Consumes the three backend APIs:
 *   POST /api/transaction
 *   GET  /api/summary/{user_id}
 *   GET  /api/ranking
 *
 * All API calls go through the `api()` helper which handles errors,
 * loading states, and JSON parsing in one place.
 * ═══════════════════════════════════════════════════════════════════
 */

(() => {
    'use strict';

    // ── Config ──────────────────────────────────────────────────────
    const API_BASE = '/api';

    // ── DOM refs ────────────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // Tabs & Panels
    const tabBtns = $$('.tabs__btn');
    const panels  = $$('.panel');

    // Transaction form
    const txnForm    = $('#txn-form');
    const txnResult  = $('#txn-result');
    const btnGenKey  = $('#btn-gen-key');
    const btnSubmit  = $('#btn-submit-txn');

    // Summary
    const summaryForm   = $('#summary-form');
    const summaryResult = $('#summary-result');
    const btnSummary    = $('#btn-fetch-summary');
    const chipBtns      = $$('.btn--chip[data-user]');

    // Ranking
    const btnRanking    = $('#btn-fetch-ranking');
    const rankingResult = $('#ranking-result');

    // Toast
    const toast = $('#toast');

    // ════════════════════════════════════════════════════════════════
    // UTILITY FUNCTIONS
    // ════════════════════════════════════════════════════════════════

    /**
     * Generate a random alphanumeric string for use as an
     * idempotency key.
     */
    function generateKey(len = 24) {
        const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
        let key = '';
        const arr = new Uint8Array(len);
        crypto.getRandomValues(arr);
        for (let i = 0; i < len; i++) {
            key += chars[arr[i] % chars.length];
        }
        return key;
    }

    /**
     * Format a number as currency (USD-style with commas).
     */
    function fmt(n) {
        return new Intl.NumberFormat('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(n);
    }

    /**
     * Show a brief toast notification.
     */
    function showToast(message, type = 'success') {
        toast.textContent = message;
        toast.className = `toast toast--${type} toast--visible`;
        toast.hidden = false;
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => {
            toast.classList.remove('toast--visible');
            setTimeout(() => { toast.hidden = true; }, 300);
        }, 3000);
    }

    /**
     * Toggle loading state on a submit button.
     */
    function setLoading(btn, loading) {
        const text   = btn.querySelector('.btn__text');
        const loader = btn.querySelector('.btn__loader');
        if (text)   text.hidden = loading;
        if (loader) loader.hidden = !loading;
        btn.disabled = loading;
    }

    /**
     * Centralised API caller with error handling.
     */
    async function api(path, options = {}) {
        const res = await fetch(`${API_BASE}${path}`, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
        const data = await res.json();
        if (!res.ok) {
            const msg = typeof data.detail === 'string'
                ? data.detail
                : data.detail?.message || JSON.stringify(data.detail);
            throw new Error(msg);
        }
        return data;
    }

    /**
     * Format an ISO timestamp to a human-readable locale string.
     */
    function fmtDate(iso) {
        try {
            return new Date(iso).toLocaleString();
        } catch {
            return iso;
        }
    }


    // ════════════════════════════════════════════════════════════════
    // TAB NAVIGATION
    // ════════════════════════════════════════════════════════════════

    tabBtns.forEach((btn) => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.tab;

            tabBtns.forEach((b) => b.classList.remove('tabs__btn--active'));
            btn.classList.add('tabs__btn--active');

            panels.forEach((p) => {
                p.classList.toggle('panel--active', p.dataset.panel === target);
            });
        });
    });


    // ════════════════════════════════════════════════════════════════
    // POST /transaction
    // ════════════════════════════════════════════════════════════════

    // Auto-generate key on page load
    $('#txn-idem-key').value = generateKey();

    // "Generate Key" button
    btnGenKey.addEventListener('click', () => {
        $('#txn-idem-key').value = generateKey();
    });

    txnForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        setLoading(btnSubmit, true);
        txnResult.hidden = true;

        const body = {
            user_id:         $('#txn-user-id').value.trim(),
            amount:          parseFloat($('#txn-amount').value),
            type:            $('#txn-type').value,
            description:     $('#txn-desc').value.trim(),
            idempotency_key: $('#txn-idem-key').value.trim(),
        };

        try {
            const data = await api('/transaction', {
                method: 'POST',
                body: JSON.stringify(body),
            });

            txnResult.innerHTML = `
                <div class="result-box result-box--success">
                    <strong>✓ Transaction Created</strong><br>
                    ID: <code>${data.id}</code><br>
                    User: ${data.user_id} &middot; ${data.type === 'credit' ? '💰' : '💸'} ${data.type} &middot; $${fmt(data.amount)}<br>
                    ${data.description ? `Memo: ${data.description}<br>` : ''}
                    Created at: ${fmtDate(data.created_at)}
                </div>
            `;
            txnResult.hidden = false;

            // Auto-generate a new key for the next request
            $('#txn-idem-key').value = generateKey();

            showToast('Transaction created successfully!');
        } catch (err) {
            const isDup = err.message.toLowerCase().includes('duplicate');
            txnResult.innerHTML = `
                <div class="result-box ${isDup ? 'result-box--warn' : 'result-box--error'}">
                    <strong>${isDup ? '⚠ Duplicate Request' : '✕ Error'}</strong><br>
                    ${err.message}
                </div>
            `;
            txnResult.hidden = false;
            showToast(err.message, 'error');
        } finally {
            setLoading(btnSubmit, false);
        }
    });


    // ════════════════════════════════════════════════════════════════
    // GET /summary/{user_id}
    // ════════════════════════════════════════════════════════════════

    async function fetchSummary(userId) {
        setLoading(btnSummary, true);
        summaryResult.hidden = true;

        try {
            const data = await api(`/summary/${encodeURIComponent(userId)}`);

            const balClass = data.net_balance >= 0 ? 'stat-card__value--green' : 'stat-card__value--red';

            let txnRows = '';
            for (const t of data.transactions) {
                txnRows += `
                    <tr>
                        <td>${fmtDate(t.created_at)}</td>
                        <td><span class="badge badge--${t.type}">${t.type}</span></td>
                        <td>$${fmt(t.amount)}</td>
                        <td>${t.description || '—'}</td>
                    </tr>
                `;
            }

            summaryResult.innerHTML = `
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-card__label">Total Credits</div>
                        <div class="stat-card__value stat-card__value--green">$${fmt(data.total_credits)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Total Debits</div>
                        <div class="stat-card__value stat-card__value--red">$${fmt(data.total_debits)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Net Balance</div>
                        <div class="stat-card__value ${balClass}">$${fmt(data.net_balance)}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Transactions</div>
                        <div class="stat-card__value stat-card__value--amber">${data.transaction_count}</div>
                    </div>
                </div>
                <h3 style="font-size:0.85rem; margin-bottom:0.75rem; color:var(--text-secondary)">Transaction History</h3>
                <div class="table-wrap">
                    <table class="table">
                        <thead><tr><th>Date</th><th>Type</th><th>Amount</th><th>Description</th></tr></thead>
                        <tbody>${txnRows || '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No transactions</td></tr>'}</tbody>
                    </table>
                </div>
            `;
            summaryResult.hidden = false;

        } catch (err) {
            summaryResult.innerHTML = `
                <div class="result-box result-box--error">
                    <strong>✕ Error</strong><br>${err.message}
                </div>
            `;
            summaryResult.hidden = false;
            showToast(err.message, 'error');
        } finally {
            setLoading(btnSummary, false);
        }
    }

    summaryForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const uid = $('#summary-user-id').value.trim();
        if (uid) fetchSummary(uid);
    });

    // Quick-select user chips
    chipBtns.forEach((btn) => {
        btn.addEventListener('click', () => {
            const uid = btn.dataset.user;
            $('#summary-user-id').value = uid;
            fetchSummary(uid);
        });
    });


    // ════════════════════════════════════════════════════════════════
    // GET /ranking
    // ════════════════════════════════════════════════════════════════

    btnRanking.addEventListener('click', async () => {
        setLoading(btnRanking, true);
        rankingResult.hidden = true;

        try {
            const data = await api('/ranking');

            if (!data.length) {
                rankingResult.innerHTML = `
                    <div class="result-box result-box--warn">No users in the system yet.</div>
                `;
                rankingResult.hidden = false;
                return;
            }

            const maxScore = Math.max(...data.map((d) => d.score)) || 1;

            let html = '<div class="leaderboard">';
            for (const entry of data) {
                const medalClass =
                    entry.rank === 1 ? 'lb-entry--gold' :
                    entry.rank === 2 ? 'lb-entry--silver' :
                    entry.rank === 3 ? 'lb-entry--bronze' : '';

                const rankClass =
                    entry.rank <= 3 ? `lb-rank--${entry.rank}` : 'lb-rank--default';

                const barWidth = ((entry.score / maxScore) * 100).toFixed(1);

                html += `
                    <div class="lb-entry ${medalClass}">
                        <div class="lb-rank ${rankClass}">${entry.rank}</div>
                        <div class="lb-info">
                            <div class="lb-user">${entry.user_id}</div>
                            <div class="lb-details">
                                <span>💰 $${fmt(entry.total_credits)}</span>
                                <span>💸 $${fmt(entry.total_debits)}</span>
                                <span>📊 ${entry.transaction_count} txns</span>
                            </div>
                        </div>
                        <div class="lb-bar"><div class="lb-bar__fill" style="width:${barWidth}%"></div></div>
                        <div class="lb-score">${entry.score.toFixed(2)}</div>
                    </div>
                `;
            }
            html += '</div>';

            rankingResult.innerHTML = html;
            rankingResult.hidden = false;

        } catch (err) {
            rankingResult.innerHTML = `
                <div class="result-box result-box--error">
                    <strong>✕ Error</strong><br>${err.message}
                </div>
            `;
            rankingResult.hidden = false;
            showToast(err.message, 'error');
        } finally {
            setLoading(btnRanking, false);
        }
    });

})();
