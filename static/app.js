// Stock Watcher front end. Talks only to this app's own /api/* routes —
// no browser storage, everything lives in memory for the page's lifetime.

const cardsEl = document.getElementById('cards');
const tickerTrackEl = document.getElementById('ticker-track');
const refreshBtn = document.getElementById('refresh-btn');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const logEl = document.getElementById('terminal-log');
const askForm = document.getElementById('ask-form');
const askInput = document.getElementById('ask-input');
const askBtn = askForm.querySelector('.btn-ask');

const ALERT_THRESHOLD = 3.0;

function fmtMoney(n) {
  return '$' + Number(n).toFixed(2);
}

function fmtPct(n) {
  const sign = n >= 0 ? '+' : '';
  return `${sign}${Number(n).toFixed(2)}%`;
}

function renderCards(prices) {
  const byTicker = {};
  prices.forEach(p => { byTicker[p.ticker] = p; });

  document.querySelectorAll('.card').forEach(card => {
    const ticker = card.dataset.ticker;
    const p = byTicker[ticker];
    if (!p) return;

    const priceEl = card.querySelector('[data-role="price"]');
    const changeEl = card.querySelector('[data-role="change"]');
    const updatedEl = card.querySelector('[data-role="updated"]');
    const alertEl = card.querySelector('[data-role="alert"]');

    priceEl.textContent = fmtMoney(p.price);
    changeEl.textContent = `${fmtPct(p.pct_change)} vs prev close`;
    changeEl.classList.remove('up', 'down');
    changeEl.classList.add(p.pct_change >= 0 ? 'up' : 'down');

    const t = new Date(p.timestamp);
    updatedEl.textContent = 'updated ' + (isNaN(t) ? p.timestamp : t.toLocaleTimeString());

    const isAlert = Math.abs(p.pct_change) >= ALERT_THRESHOLD;
    card.classList.toggle('card-alert', isAlert);
    alertEl.hidden = !isAlert;
  });
}

function renderTickerTape(prices) {
  if (!prices.length) return;
  const items = prices.map(p => {
    const dir = p.pct_change >= 0 ? 'up' : 'down';
    const arrow = p.pct_change >= 0 ? '▲' : '▼';
    return `<span class="ticker-item"><span class="sym">${p.ticker}</span>
      <span>${fmtMoney(p.price)}</span>
      <span class="${dir}">${arrow} ${fmtPct(p.pct_change)}</span></span>`;
  }).join('');
  // duplicate the content once so the CSS marquee (-50% translate) loops seamlessly
  tickerTrackEl.innerHTML = items + items;
}

function renderSparkline(svgEl, history) {
  if (!history || history.length < 2) {
    svgEl.innerHTML = '';
    return;
  }
  const prices = history.map(h => h.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1; // avoid divide-by-zero when every point is flat

  const points = prices.map((p, i) => {
    const x = (i / (prices.length - 1)) * 100;
    const y = 25 - ((p - min) / range) * 23; // 2..25 vertical padding inside the 0..28 viewBox
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');

  const trendClass = prices[prices.length - 1] > prices[0] ? 'spark-up'
    : prices[prices.length - 1] < prices[0] ? 'spark-down' : 'spark-flat';

  svgEl.innerHTML = `<polyline class="${trendClass}" points="${points}"></polyline>`;
}

async function loadSparklines() {
  document.querySelectorAll('.card').forEach(async card => {
    const ticker = card.dataset.ticker;
    const svgEl = card.querySelector('[data-role="sparkline"]');
    if (!svgEl) return;
    try {
      const res = await fetch(`/api/price_history/${encodeURIComponent(ticker)}`);
      const data = await res.json();
      renderSparkline(svgEl, data.history);
    } catch (e) {
      // no history yet — leave the sparkline empty, not an error worth surfacing
    }
  });
}

async function loadCachedPrices() {
  try {
    const res = await fetch('/api/prices');
    const data = await res.json();
    if (data.prices && data.prices.length) {
      renderCards(data.prices);
      renderTickerTape(data.prices);
    }
  } catch (e) {
    // fine — user just hasn't run anything yet, cards stay at placeholder state
  }
  loadSparklines();
}

async function refreshPrices() {
  refreshBtn.disabled = true;
  refreshBtn.classList.add('spinning');
  try {
    const res = await fetch('/api/refresh_prices', { method: 'POST' });
    const data = await res.json();
    if (data.prices && data.prices.length) {
      renderCards(data.prices);
      // ticker tape wants ALL 5, so re-pull the merged cached set
      await loadCachedPrices();
    }
    if (data.errors && data.errors.length) {
      appendLog('error', `Could not fetch: ${data.errors.map(e => e.ticker).join(', ')} — network may be unavailable.`);
    }
  } catch (e) {
    appendLog('error', 'Price refresh failed — check the server is running and has network access.');
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.classList.remove('spinning');
  }
}

async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (data.index_ready) {
      statusDot.className = 'status-dot ok';
      statusText.textContent = `index ready (${data.news_articles} news, ${data.filing_chunks} filing chunks)`;
    } else {
      statusDot.className = 'status-dot warn';
      statusText.textContent = 'no index yet — run the fetch scripts + build_index.py';
    }
  } catch (e) {
    statusText.textContent = 'status unavailable';
  }
}

function appendLog(kind, text, extra) {
  const div = document.createElement('div');
  if (kind === 'user') {
    div.className = 'log-entry log-user';
    div.innerHTML = `<span class="prefix">You&gt;</span> ${escapeHtml(text)}`;
  } else if (kind === 'bot') {
    div.className = 'log-entry log-bot';
    const sourcesHtml = (extra && extra.sources && extra.sources.length)
      ? `<div class="sources-list">${extra.sources.map(s => `
          <div class="source-row">
            <span class="source-tag ${s.source_type}">${s.source_type}</span>
            <span>${escapeHtml(s.title)} (similarity ${s.similarity})</span>
          </div>`).join('')}</div>`
      : '';
    div.innerHTML = `<span class="prefix">Bot&gt;</span><div class="log-bot-text">${escapeHtml(text)}</div>${sourcesHtml}`;
  } else if (kind === 'error') {
    div.className = 'log-entry log-error';
    div.innerHTML = `<span class="prefix">!&gt;</span> ${escapeHtml(text)}`;
  } else {
    div.className = 'log-entry log-system';
    div.textContent = text;
  }
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

askForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const question = askInput.value.trim();
  if (!question) return;

  appendLog('user', question);
  askInput.value = '';
  askInput.disabled = true;
  askBtn.disabled = true;

  const thinkingEl = appendLog('bot', '');
  thinkingEl.querySelector('.log-bot-text').innerHTML = '<span class="typing-dots">thinking</span>';

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    thinkingEl.remove();

    if (!res.ok) {
      appendLog('error', data.error || 'Something went wrong.');
    } else {
      appendLog('bot', data.answer, { sources: data.sources });
    }
  } catch (err) {
    thinkingEl.remove();
    appendLog('error', 'Could not reach the server.');
  } finally {
    askInput.disabled = false;
    askBtn.disabled = false;
    askInput.focus();
  }
});

refreshBtn.addEventListener('click', refreshPrices);

// ---- watchlist management: add ----
const addForm = document.getElementById('add-form');
const addTickerInput = document.getElementById('add-ticker');
const addNameInput = document.getElementById('add-name');
const addBtn = document.getElementById('add-btn');
const addStatus = document.getElementById('add-status');

addForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const ticker = addTickerInput.value.trim().toUpperCase();
  const name = addNameInput.value.trim();
  if (!ticker) {
    addStatus.textContent = 'Enter a ticker symbol first.';
    addStatus.className = 'add-status err';
    return;
  }

  addBtn.disabled = true;
  addTickerInput.disabled = true;
  addNameInput.disabled = true;
  addStatus.className = 'add-status';
  addStatus.innerHTML = `fetching price, news, and 10-K for ${ticker}, then embedding<span class="typing-dots"></span> (this can take up to ~20s)`;

  try {
    const res = await fetch('/api/watchlist/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, name }),
    });
    const data = await res.json();

    if (!res.ok) {
      addStatus.textContent = data.error || 'Could not add that ticker.';
      addStatus.className = 'add-status err';
      addBtn.disabled = false;
      addTickerInput.disabled = false;
      addNameInput.disabled = false;
      return;
    }

    let msg = `Added ${data.entry.ticker} — ${data.news_count} news article(s), ${data.filing_chunks} filing chunk(s) indexed.`;
    if (data.warnings && data.warnings.length) {
      msg += ' Note: ' + data.warnings.join(' ');
    }
    addStatus.textContent = msg + ' Reloading…';
    addStatus.className = 'add-status ok';
    setTimeout(() => location.reload(), 900);
  } catch (err) {
    addStatus.textContent = 'Could not reach the server.';
    addStatus.className = 'add-status err';
    addBtn.disabled = false;
    addTickerInput.disabled = false;
    addNameInput.disabled = false;
  }
});

// ---- watchlist management: remove (click once to arm, click again to confirm) ----
document.querySelectorAll('[data-role="remove"]').forEach(btn => {
  let armed = false;
  let armTimeout = null;

  btn.addEventListener('click', async () => {
    if (!armed) {
      armed = true;
      btn.textContent = 'CONFIRM?';
      btn.classList.add('confirming');
      armTimeout = setTimeout(() => {
        armed = false;
        btn.textContent = 'REMOVE';
        btn.classList.remove('confirming');
      }, 3000);
      return;
    }

    clearTimeout(armTimeout);
    btn.disabled = true;
    btn.textContent = 'REMOVING…';
    const ticker = btn.dataset.ticker;

    try {
      const res = await fetch('/api/watchlist/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      if (res.ok) {
        location.reload();
      } else {
        const data = await res.json();
        appendLog('error', data.error || `Could not remove ${ticker}.`);
        btn.disabled = false;
        btn.textContent = 'REMOVE';
        btn.classList.remove('confirming');
        armed = false;
      }
    } catch (err) {
      appendLog('error', `Could not remove ${ticker} — server unreachable.`);
      btn.disabled = false;
      btn.textContent = 'REMOVE';
      btn.classList.remove('confirming');
      armed = false;
    }
  });
});

loadStatus();
loadCachedPrices();
