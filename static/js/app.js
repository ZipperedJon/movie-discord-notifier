/* Shared helpers: fetch wrapper, toasts, the TMDB movie picker, date handling. */

// ---------------------------------------------------------------- API
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON body */ }
  if (!res.ok) {
    const detail = data && data.detail
      ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))
      : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return data;
}

// ---------------------------------------------------------------- Toasts
function toast(message, kind = 'ok', ms = 5000) {
  let host = document.getElementById('toasts');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toasts';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// ---------------------------------------------------------------- Dates
// <input type="datetime-local"> gives a wall-clock string in the browser's
// timezone. Date parses it as local time, so this lands on the right instant.
function localToEpoch(value) {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
}

function epochToLocalString(epoch) {
  if (!epoch) return '—';
  return new Date(epoch * 1000).toLocaleString(undefined, {
    weekday: 'short', year: 'numeric', month: 'short',
    day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function relativeTime(epoch) {
  if (!epoch) return '';
  const diff = epoch * 1000 - Date.now();
  const abs = Math.abs(diff);
  const units = [['day', 86400000], ['hour', 3600000], ['minute', 60000]];
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, ms] of units) {
    if (abs >= ms) return rtf.format(Math.round(diff / ms), unit);
  }
  return 'now';
}

// Prefill a datetime-local input with "now, rounded up to the next 15 minutes".
function defaultDateTime(offsetHours = 0) {
  const d = new Date(Date.now() + offsetHours * 3600000);
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ---------------------------------------------------------------- Movie picker
/**
 * Live TMDB search bound to an <input>. Shows posters + release year, and
 * calls onSelect(movie) with the full TMDB detail record once one is chosen.
 */
class MoviePicker {
  constructor({ input, results, selected, onSelect }) {
    this.input = document.getElementById(input);
    this.results = document.getElementById(results);
    this.selectedEl = document.getElementById(selected);
    this.onSelect = onSelect || (() => {});
    this.movie = null;
    this.timer = null;
    this.seq = 0;
    this.cursor = -1;

    this.input.addEventListener('input', () => this.onType());
    this.input.addEventListener('keydown', (e) => this.onKey(e));
    document.addEventListener('click', (e) => {
      if (!this.input.closest('.picker').contains(e.target)) this.clearResults();
    });
  }

  onType() {
    clearTimeout(this.timer);
    const q = this.input.value.trim();
    if (q.length < 2) { this.clearResults(); return; }
    this.timer = setTimeout(() => this.search(q), 250);
  }

  async search(q) {
    const seq = ++this.seq;
    try {
      const data = await api('GET', `/api/tmdb/search?q=${encodeURIComponent(q)}`);
      if (seq !== this.seq) return; // a newer keystroke already fired
      this.render(data.results);
    } catch (err) {
      this.clearResults();
      toast(err.message, 'err');
    }
  }

  render(list) {
    this.cursor = -1;
    this.results.innerHTML = '';
    if (!list.length) {
      this.results.innerHTML = '<div class="picker-item"><div class="pi-meta">No matches.</div></div>';
      return;
    }
    for (const m of list) {
      const item = document.createElement('div');
      item.className = 'picker-item';
      const poster = m.poster_url
        ? `<img src="${m.poster_url}" alt="">`
        : '<img alt="" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">';
      item.innerHTML = `${poster}<div>
        <div class="pi-title">${escapeHtml(m.title)} ${m.year ? `<span class="pi-meta">(${m.year})</span>` : ''}</div>
        <div class="pi-meta">${escapeHtml(m.overview || 'No synopsis on TMDB.')}</div>
      </div>`;
      item.addEventListener('click', () => this.choose(m));
      this.results.appendChild(item);
    }
  }

  onKey(e) {
    const items = [...this.results.querySelectorAll('.picker-item')];
    if (!items.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      this.cursor += e.key === 'ArrowDown' ? 1 : -1;
      this.cursor = Math.max(0, Math.min(items.length - 1, this.cursor));
      items.forEach((el, i) => el.classList.toggle('active', i === this.cursor));
      items[this.cursor].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && this.cursor >= 0) {
      e.preventDefault();
      items[this.cursor].click();
    } else if (e.key === 'Escape') {
      this.clearResults();
    }
  }

  clearResults() { this.results.innerHTML = ''; this.cursor = -1; }

  async choose(summary) {
    this.clearResults();
    this.input.value = summary.title;
    this.selectedEl.innerHTML = '<div class="sm-body"><div class="pi-meta">Loading trailer and details…</div></div>';
    try {
      const movie = await api('GET', `/api/tmdb/movie/${summary.tmdb_id}`);
      this.movie = movie;
      this.renderSelected(movie);
      this.onSelect(movie);
    } catch (err) {
      this.selectedEl.innerHTML = '';
      toast(err.message, 'err');
    }
  }

  renderSelected(m) {
    const poster = m.poster_path
      ? `<img src="https://image.tmdb.org/t/p/w185${m.poster_path}" alt="">` : '';
    const trailer = m.trailer_url
      ? `<a href="${m.trailer_url}" target="_blank" rel="noopener">▶ Trailer found on YouTube</a>`
      : '<span style="color:var(--amber)">⚠ No YouTube trailer on TMDB — paste one below if you have it.</span>';
    this.selectedEl.innerHTML = `${poster}<div class="sm-body">
      <h4>${escapeHtml(m.title)} ${m.release_date ? `<span class="sm-meta">(${m.release_date.slice(0, 4)})</span>` : ''}</h4>
      <div class="sm-meta">${escapeHtml(m.genres || '—')}${m.runtime ? ` · ${m.runtime} min` : ''}</div>
      <div>${trailer}</div>
    </div>`;
  }

  reset() {
    this.movie = null;
    this.input.value = '';
    this.selectedEl.innerHTML = '';
    this.clearResults();
  }
}

/**
 * Open a <dialog> and resolve with its field values on submit, or null on cancel.
 * Nothing else on the page is touched, so whatever the user already filled in
 * on the main form stays exactly as it was.
 */
function openModal(dialogId, { onSubmit } = {}) {
  const dlg = document.getElementById(dialogId);
  const form = dlg.querySelector('form');
  const submitBtn = form.querySelector('[data-submit]');
  const first = form.querySelector('input, select, textarea');

  form.reset();
  dlg.showModal();
  if (first) setTimeout(() => first.focus(), 30);

  return new Promise((resolve) => {
    const cleanup = () => {
      form.removeEventListener('submit', handleSubmit);
      dlg.removeEventListener('close', handleClose);
    };
    const handleClose = () => { cleanup(); resolve(null); };

    async function handleSubmit(e) {
      e.preventDefault();
      const values = Object.fromEntries(
        [...form.elements]
          .filter((el) => el.name)
          .map((el) => [el.name, el.type === 'checkbox' ? el.checked : el.value.trim()])
      );
      const original = submitBtn.textContent;
      submitBtn.disabled = true;
      submitBtn.textContent = 'Saving…';
      try {
        const result = onSubmit ? await onSubmit(values) : values;
        cleanup();
        dlg.close();
        resolve(result);
      } catch (err) {
        toast(err.message, 'err');
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = original;
      }
    }

    form.addEventListener('submit', handleSubmit);
    dlg.addEventListener('close', handleClose);
  });
}

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/** Wrap an async click handler so the button disables and shows progress. */
function busy(btn, label, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  return Promise.resolve()
    .then(fn)
    .catch((err) => toast(err.message, 'err'))
    .finally(() => { btn.disabled = false; btn.textContent = original; });
}
