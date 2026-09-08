/* Shared helpers: fetch wrapper, toasts, the TMDB movie picker, date handling. */

// ---------------------------------------------------------------- Mobile nav
(function navDrawer() {
  const toggle = document.getElementById('nav-toggle');
  const scrim = document.getElementById('nav-scrim');
  const sidebar = document.getElementById('sidebar');
  if (!toggle || !scrim || !sidebar) return;

  const isOpen = () => document.body.classList.contains('nav-open');

  function open() {
    document.body.classList.add('nav-open');
    document.body.style.overflow = 'hidden';   // don't scroll the page behind
    scrim.hidden = false;
    requestAnimationFrame(() => scrim.classList.add('show'));
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-label', 'Close menu');
    const first = sidebar.querySelector('a');
    if (first) first.focus({ preventScroll: true });
  }

  function close({ restoreFocus = true } = {}) {
    document.body.classList.remove('nav-open');
    document.body.style.overflow = '';
    scrim.classList.remove('show');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Open menu');
    // Keep the scrim in the DOM until the fade finishes.
    setTimeout(() => { if (!isOpen()) scrim.hidden = true; }, 240);
    if (restoreFocus) toggle.focus({ preventScroll: true });
  }

  toggle.addEventListener('click', () => (isOpen() ? close() : open()));
  scrim.addEventListener('click', () => close());

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen()) close();
  });

  // Following a link navigates away; closing first avoids a flash of the
  // drawer sliding shut over the new page.
  sidebar.querySelectorAll('a').forEach((a) =>
    a.addEventListener('click', () => close({ restoreFocus: false })));

  // Rotating to landscape / resizing past the breakpoint reveals the permanent
  // sidebar, so drop the open state and the scroll lock with it.
  window.addEventListener('resize', () => {
    if (isOpen() && window.innerWidth > 820) close({ restoreFocus: false });
  });
})();

// Registered for home-screen installs. It is deliberately network-only: caching
// pages here would fight the ?v= asset busting and serve stale UI after updates.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

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

// Format a Date for a <input type="datetime-local"> value, in local time.
function dateToInputValue(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function epochToInputValue(epoch) {
  return epoch ? dateToInputValue(new Date(epoch * 1000)) : '';
}

// Prefill a datetime-local input with "now, rounded up to the next 15 minutes".
function defaultDateTime(offsetHours = 0) {
  const d = new Date(Date.now() + offsetHours * 3600000);
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  return dateToInputValue(d);
}

/** "155" -> "2h 35m", for showing a movie's runtime. */
function formatRuntime(minutes) {
  if (!minutes) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h}h${m ? ` ${m}m` : ''}` : `${m}m`;
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
 * Keeps an "Ends" datetime in step with a "Starts" datetime.
 *
 * Moving the start shifts the end by the same amount, so the duration you have
 * set is preserved — that matters when the end was padded for previews. With no
 * usable end yet, it falls back to start + the movie's runtime. Either way the
 * end can never be left stranded before the start.
 */
class EndTimeSync {
  constructor({ start, end, hint, getRuntime }) {
    this.start = document.getElementById(start);
    this.end = document.getElementById(end);
    this.hint = document.getElementById(hint);
    this.getRuntime = getRuntime || (() => null);
    this.custom = false;           // true once the user picks their own duration
    this.lastStart = localToEpoch(this.start.value);

    this.start.addEventListener('input', () => this.onStartChange());
    this.end.addEventListener('input', () => {
      this.custom = true;
      this.render();
    });
  }

  /** Duration currently in the fields, in seconds, or null. */
  duration() {
    const s = localToEpoch(this.start.value);
    const e = localToEpoch(this.end.value);
    return s && e && e > s ? e - s : null;
  }

  onStartChange() {
    const started = localToEpoch(this.start.value);
    if (!started) return;

    // Prefer the duration that was on screen a moment ago, measured against the
    // PREVIOUS start — reading it after the start moved would give the wrong span.
    const ended = localToEpoch(this.end.value);
    let span = null;
    if (ended && this.lastStart && ended > this.lastStart) span = ended - this.lastStart;

    const runtime = this.getRuntime();
    if (span === null && runtime) span = runtime * 60;

    if (span !== null) this.end.value = epochToInputValue(started + span);
    this.lastStart = started;
    this.render();
  }

  /** Movie changed: its runtime is now the source of truth again. */
  resetToRuntime() {
    this.custom = false;
    const started = localToEpoch(this.start.value);
    const runtime = this.getRuntime();
    if (started && runtime) this.end.value = epochToInputValue(started + runtime * 60);
    this.lastStart = started;
    this.render();
  }

  render() {
    if (!this.hint) return;
    const runtime = this.getRuntime();
    if (!runtime) {
      this.hint.textContent = this.end.value ? '' : '(optional)';
      return;
    }
    const span = this.duration();
    const matchesRuntime = span !== null && Math.abs(span - runtime * 60) < 60;
    this.hint.textContent = matchesRuntime
      ? `(${formatRuntime(runtime)} runtime)`
      : `(${formatRuntime(runtime)} runtime · ${span ? formatRuntime(Math.round(span / 60)) : '—'} booked)`;
  }
}

/**
 * Highlights a save button once something changes and warns before leaving with
 * unsaved edits. Every tracker registers itself so one beforeunload covers them all.
 */
const dirtyTrackers = new Set();

class DirtyTracker {
  constructor(saveButton, { row = null } = {}) {
    this.button = saveButton;
    this.row = row;
    this.dirty = false;
    this.label = saveButton ? saveButton.textContent : '';
    dirtyTrackers.add(this);
  }

  /** Flag every listed input as something that makes this tracker dirty. */
  watch(elements) {
    for (const el of elements) {
      if (!el) continue;
      const evt = el.type === 'checkbox' || el.tagName === 'SELECT' ? 'change' : 'input';
      el.addEventListener(evt, () => this.mark());
    }
    return this;
  }

  mark() {
    if (this.dirty) return;
    this.dirty = true;
    if (this.button) {
      this.button.classList.add('dirty');
      this.button.textContent = `${this.label} •`;
    }
    if (this.row) this.row.classList.add('dirty');
  }

  clear() {
    this.dirty = false;
    if (this.button) {
      this.button.classList.remove('dirty');
      this.button.textContent = this.label;
    }
    if (this.row) this.row.classList.remove('dirty');
  }
}

window.addEventListener('beforeunload', (e) => {
  if ([...dirtyTrackers].some((t) => t.dirty)) {
    e.preventDefault();
    e.returnValue = '';   // required by older browsers to trigger the prompt
  }
});

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

/**
 * Sorts an already-rendered list in place.
 *
 * Reordering the existing nodes rather than re-fetching keeps every handler
 * (Post, Delete, …) attached and needs no round trip. Sort keys ride along as
 * data-* attributes on each row. The choice is remembered per list.
 */
class SortableList {
  constructor({ select, container, rowSelector, storageKey, fallback }) {
    this.select = document.getElementById(select);
    this.container = document.getElementById(container);
    this.rowSelector = rowSelector;
    this.storageKey = storageKey;
    this.fallback = fallback;
    if (!this.select || !this.container) return;

    let saved = null;
    try { saved = localStorage.getItem(storageKey); } catch { /* private mode */ }
    if (saved && [...this.select.options].some((o) => o.value === saved)) {
      this.select.value = saved;
    }

    this.select.addEventListener('change', () => {
      try { localStorage.setItem(storageKey, this.select.value); } catch { /* ignore */ }
      this.apply();
    });
    this.apply();
  }

  /** Missing values always sort last, whichever direction is chosen. */
  static compare(a, b, numeric, dir) {
    const missingA = a === '' || a === undefined || a === null;
    const missingB = b === '' || b === undefined || b === null;
    if (missingA && missingB) return 0;
    if (missingA) return 1;
    if (missingB) return -1;

    let result;
    if (numeric) {
      result = Number(a) - Number(b);
    } else {
      result = String(a).localeCompare(String(b), undefined, { sensitivity: 'base' });
    }
    return dir === 'desc' ? -result : result;
  }

  apply() {
    const [field, dir] = this.select.value.split(':');
    const numeric = this.select.selectedOptions[0]?.dataset.numeric === '1';
    const rows = [...this.container.querySelectorAll(this.rowSelector)];

    rows.sort((rowA, rowB) => {
      const primary = SortableList.compare(
        rowA.dataset[field], rowB.dataset[field], numeric, dir);
      if (primary !== 0) return primary;
      // Tie-break on a stable secondary key so equal values don't shuffle.
      return SortableList.compare(
        rowA.dataset[this.fallback], rowB.dataset[this.fallback], true, 'asc');
    });

    for (const row of rows) this.container.appendChild(row);
  }
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
