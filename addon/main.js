// InterDAW: DOM wiring. Connects to LUNA, takes a set by dialog or drop, shows what will and
// won't come across, then runs the import that logic.js performs.

const $ = (id) => document.getElementById(id);
const METER_SEGMENTS = 24;
const HELPER_RETRY_MS = 3000;
let luna;
let plan;
let busy = false;

function status(word, detail = '', markState = 'ready', isError = false) {
  $('word').textContent = word;
  $('detail').textContent = detail;
  $('mark').dataset.state = markState;
  $('app').classList.toggle('is-error', isError);
}

function list(id, items) {
  const root = $(id);
  root.innerHTML = '';
  for (const text of items) {
    const li = document.createElement('li');
    li.textContent = text;
    root.appendChild(li);
  }
  $(`${id}-group`).hidden = items.length === 0;
  $(`${id}-count`).textContent = String(items.length);
}

function setProgress(step, total) {
  const lit = Math.round((step / total) * METER_SEGMENTS);
  [...$('meter').children].forEach((segment, i) => segment.classList.toggle('is-lit', i < lit));
  $('percent').textContent = `${Math.round((step / total) * 100)}%`;
}

async function renderChecks() {
  const session = luna.sessions.current();
  if (session) await session.ready();
  const reasons = AddOnLogic.checks(plan, session ? session.visibleTracks.length : 0);
  $('check').hidden = reasons.length === 0;
  $('check-why').textContent = reasons.join(' ');
  $('slot').dataset.risk = reasons.length ? 'check' : 'ok';
}

async function renderPlan() {
  const s = AddOnLogic.summary(plan);
  $('set-name').textContent = s.name;
  const facts = $('facts');
  facts.innerHTML = '';
  for (const [key, value] of AddOnLogic.factRows(plan)) {
    const row = document.createElement('div');
    row.className = 'idw-kv';
    const k = document.createElement('span');
    k.className = 'idw-kv-k';
    k.textContent = key;
    const v = document.createElement('span');
    v.className = 'idw-kv-v';
    v.textContent = value;
    row.append(k, v);
    facts.appendChild(row);
  }
  await renderChecks();
  list('missing', s.missing.map((m) => `${m.track}, ${m.clip}: ${m.path}`));
  list('warnings', s.warnings);
  list('notes', plan.notes);
  renderSuggestions();
  list('failed', []);
  $('drop').hidden = true;
  $('done').hidden = true;
  $('slot').hidden = false;
  $('import').hidden = false;
  $('stat').hidden = true;
  $('sec-title').textContent = 'READY TO IMPORT';
}

const MATCH_LABEL = { exact: 'EXACT', close: 'CLOSE', check: 'CHECK' };
let suggestionState = {};

function suggestionSlot(suggestion, session) {
  const state = suggestionState[suggestion.id] ?? {};
  const slot = document.createElement('div');
  if (state.status === 'done') {
    slot.className = 'idw-slot idw-slot--done';
    slot.innerHTML = '<svg class="idw-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg><span class="idw-done-title"></span>';
    slot.querySelector('.idw-done-title').textContent = suggestion.title;
    return slot;
  }
  const ready = session && AddOnLogic.canRun(session, suggestion);
  slot.className = 'idw-slot';
  slot.dataset.risk = suggestion.match === 'check' || !ready ? 'check' : 'ok';
  const body = document.createElement('div');
  body.className = 'idw-slot-body';
  const head = document.createElement('div');
  head.className = 'idw-slot-head';
  const title = document.createElement('div');
  title.className = 'idw-slot-title';
  title.textContent = suggestion.title;
  const tag = document.createElement('span');
  tag.className = `idw-tag idw-tag--${suggestion.match}`;
  tag.textContent = MATCH_LABEL[suggestion.match];
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'idw-dismiss';
  dismiss.title = 'Dismiss';
  dismiss.innerHTML = '<svg class="idw-ico idw-ico--grey" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  dismiss.onclick = () => {
    suggestionState[suggestion.id] = { status: 'dismissed' };
    renderSuggestions();
  };
  head.append(title, tag, dismiss);
  const why = document.createElement('div');
  why.className = 'idw-reason';
  why.textContent = suggestion.why;
  body.append(head, why);
  slot.appendChild(body);
  if (state.status === 'running') {
    const stat = document.createElement('div');
    stat.className = 'idw-stat';
    stat.innerHTML = '<span class="idw-stat-small">WORKING</span>';
    slot.appendChild(stat);
  } else if (ready) {
    const go = document.createElement('button');
    go.type = 'button';
    go.className = 'idw-go';
    go.disabled = busy;
    go.innerHTML = '<span class="idw-go-label">GO</span>';
    go.onclick = () => runOne(suggestion);
    slot.appendChild(go);
  } else {
    const stat = document.createElement('div');
    stat.className = 'idw-stat';
    stat.innerHTML = '<span class="idw-stat-small">IMPORT FIRST</span>';
    slot.appendChild(stat);
  }
  return slot;
}

function renderSuggestions() {
  const session = luna.sessions.current();
  const groups = AddOnLogic.suggestionGroups(plan, suggestionState);
  for (const [id, group, items] of [['suggest', 'suggest-group', groups.audible], ['optional', 'optional-group', groups.optional]]) {
    $(id).replaceChildren(...items.map((s) => suggestionSlot(s, session)));
    $(group).hidden = items.length === 0;
  }
  list('rejected', groups.failed);
  $('go-all').disabled = busy || !pendingAudible(session).length;
}

function pendingAudible(session) {
  if (!session) return [];
  return (plan.suggestions ?? []).filter((s) => s.audible && !suggestionState[s.id]?.status && AddOnLogic.canRun(session, s));
}

async function runOne(suggestion) {
  const session = luna.sessions.current();
  if (!session || busy) return;
  busy = true;
  suggestionState[suggestion.id] = { status: 'running' };
  renderSuggestions();
  status('WORKING', suggestion.title, 'working');
  try {
    await AddOnLogic.runSuggestion(luna, session, suggestion);
    suggestionState[suggestion.id] = { status: 'done' };
    status('READY', `Done: ${suggestion.title}`);
  } catch (e) {
    suggestionState[suggestion.id] = { status: 'failed', error: e.message };
    status('READY', 'Couldn\'t do that one. Noted at the bottom.');
  }
  busy = false;
  renderSuggestions();
  refreshSend();
}

const COUNT_LABEL = [['same', 'UNTOUCHED', 'stay as theirs'], ['mix', 'MIX', 'written into their tracks'], ['print', 'PRINTED', 'with their originals kept, muted'], ['added', 'NEW', 'added as audio'], ['removed', 'REMOVED', 'muted in their set']];
const FORMAT_WORDS = {
  both: 'Their set with your work added, plus stems for any DAW.',
  live: 'Their set with your work added. Smallest and quickest to print.',
  stems: 'Every track that sounds, from bar 1, for any DAW. No Live needed.',
};
let lastFolder;
let sendFormat = 'both';

async function refreshSend() {
  const session = luna.sessions.current();
  if (!plan || !session || busy) return;
  await session.ready();
  $('send-group').hidden = !AddOnLogic.isImported(session, plan);
  if ($('send-group').hidden) return;
  $('send-done').hidden = true;
  try {
    const diff = await AddOnLogic.compareSession(session, plan);
    if (busy) return;
    const counts = AddOnLogic.exportCounts(diff);
    const facts = $('send-facts');
    facts.innerHTML = '';
    const about = document.createElement('div');
    about.className = 'idw-reason';
    about.textContent = FORMAT_WORDS[sendFormat];
    facts.appendChild(about);
    for (const [key, label, words] of COUNT_LABEL) {
      if (!counts[key] || sendFormat === 'stems') continue;
      const row = document.createElement('div');
      row.className = 'idw-kv';
      const k = document.createElement('span');
      k.className = 'idw-kv-k';
      k.textContent = label;
      const v = document.createElement('span');
      v.className = 'idw-kv-v';
      v.textContent = `${counts[key]} ${words}`;
      row.append(k, v);
      facts.appendChild(row);
    }
    const warnings = sendFormat === 'stems' ? [] : diff.warnings;
    $('send-check').hidden = warnings.length === 0;
    $('send-why').textContent = warnings.join(' ');
    $('send').dataset.risk = warnings.length ? 'check' : 'ok';
  } catch (e) {
    $('send-check').hidden = false;
    $('send-why').textContent = e.message;
    $('send').dataset.risk = 'check';
  }
}

async function runSend() {
  const session = luna.sessions.current();
  if (!session || busy) return;
  if (!(await luna.gui.available())) {
    status('NO FILE DIALOG', 'Open InterDAW inside LUNA to send a set back.', 'ready', true);
    return;
  }
  const [parent] = await luna.gui.chooseFolders();
  if (!parent) return;
  busy = true;
  $('choose').disabled = true;
  $('send-go').hidden = true;
  $('send-stat').hidden = false;
  $('meter').hidden = false;
  renderSuggestions();
  setProgress(0, 1);
  try {
    const result = await AddOnLogic.runExport(session, plan, parent, ({ text, fraction }) => {
      setProgress(fraction, 1);
      $('send-percent').textContent = `${Math.round(fraction * 100)}%`;
      status('SENDING', text, 'working');
    }, fetch, sendFormat);
    lastFolder = result.folder;
    $('send').hidden = true;
    $('send-done').hidden = false;
    $('send-done-title').textContent = result.set ? `${result.set} and ${result.stems} stems` : `${result.stems} stems`;
    status('SENT', result.folder.slice(result.folder.lastIndexOf('/') + 1));
    if (result.missing.length) list('failed', result.missing.map((m) => `Missing when sent: ${m}`));
  } catch (e) {
    status('COULDN\'T SEND', e.message, 'ready', true);
    $('send-check').hidden = false;
    $('send-why').textContent = e.message;
  }
  $('send-go').hidden = false;
  $('send-stat').hidden = true;
  $('meter').hidden = true;
  $('choose').disabled = false;
  busy = false;
  renderSuggestions();
  refreshSend();
}

async function runAll() {
  for (const suggestion of pendingAudible(luna.sessions.current())) await runOne(suggestion);
}

async function openSet(path) {
  if (busy) return;
  if (!/\.als$/i.test(path)) {
    status('NOT A SET', 'That is not an Ableton Live set (.als).', 'ready', true);
    return;
  }
  status('READING', path.slice(path.lastIndexOf('/') + 1), 'working');
  try {
    plan = await AddOnLogic.readSet(path);
    suggestionState = {};
    lastFolder = undefined;
    $('send').hidden = false;
    await renderPlan();
    status('READY', 'Check the list, then import.');
    const session = luna.sessions.current();
    if (session) AddOnLogic.rememberSet(session, path).catch(() => {});
    refreshSend();
  } catch (e) {
    status('COULDN\'T READ SET', e.message, 'ready', true);
    watchHelper();
  }
}

async function runImport() {
  const session = luna.sessions.current();
  if (!session) {
    status('NO SESSION', 'Open a session first.', 'ready', true);
    return;
  }
  busy = true;
  $('choose').disabled = true;
  $('import').hidden = true;
  $('stat').hidden = false;
  $('meter').hidden = false;
  $('sec-title').textContent = 'IMPORTING';
  setProgress(0, 1);
  status('IMPORTING', plan.source.name, 'working');
  try {
    const result = await AddOnLogic.runImport(session, plan, ({ step, total, text }) => {
      setProgress(step, total);
      status('IMPORTING', text, 'working');
    });
    list('failed', result.failed);
    const line = `Imported ${result.tracks} of ${plan.tracks.length} tracks`;
    $('slot').hidden = true;
    $('done').hidden = false;
    $('done-title').textContent = `${plan.source.name}: ${line.toLowerCase()}`;
    $('sec-title').textContent = 'IMPORTED';
    if (result.failed.length) status('FINISHED WITH ERRORS', `${line}. See FAILED below.`, 'ready', true);
    else status('IMPORTED', line);
  } catch (e) {
    $('import').hidden = false;
    $('stat').hidden = true;
    $('sec-title').textContent = 'READY TO IMPORT';
    status('IMPORT STOPPED', e.message, 'ready', true);
  }
  $('meter').hidden = true;
  $('choose').disabled = false;
  busy = false;
  renderSuggestions();
  refreshSend();
}

/** Show how to install (or update) the helper until one this panel can use answers. */
let watchingHelper = false;
async function watchHelper() {
  if (watchingHelper) return;
  watchingHelper = true;
  let state;
  while ((state = await AddOnLogic.helperStatus()) !== 'running') {
    const outdated = state === 'outdated';
    $('offline-title').textContent = outdated ? 'Update the InterDAW helper' : 'Install the InterDAW helper';
    $('offline-why').textContent = outdated
      ? 'The helper on this Mac is from another version of InterDAW. Paste this into Terminal, press Return, then come back here:'
      : 'InterDAW reads Live sets with a small helper on your Mac, because an Add On can\'t open files itself. Paste this into Terminal, press Return, then come back here:';
    $('offline').hidden = false;
    if (!plan) status(outdated ? 'HELPER OUT OF DATE' : 'HELPER NEEDED', outdated ? 'Update it to read a set.' : 'Install it to read a set.', 'offline');
    await new Promise((r) => setTimeout(r, HELPER_RETRY_MS));
  }
  $('offline').hidden = true;
  if (!plan) status('READY', 'Choose or drop a Live set.');
  watchingHelper = false;
}

/** Copy the install command. LUNA's page may refuse the async clipboard, so fall back to a selection copy. */
async function copyInstall() {
  const field = $('install-cmd');
  let copied = false;
  try {
    await navigator.clipboard.writeText(field.value);
    copied = true;
  } catch {
    field.focus();
    field.select();
    copied = document.execCommand('copy');
  }
  $('install-copy').textContent = copied ? 'COPIED' : 'SELECTED: PRESS ⌘C';
  setTimeout(() => { $('install-copy').textContent = 'COPY'; }, 2500);
}

async function connect() {
  for (let i = 0; i < METER_SEGMENTS; i++) $('meter').appendChild(document.createElement('i'));
  for (;;) {
    try {
      luna = await Luna.connect();
      break;
    } catch (e) {
      status('WAITING FOR LUNA', e instanceof Luna.NotRunning ? '' : e.message, 'offline');
      await new Promise((r) => setTimeout(r, 1500));
    }
  }
  Luna.bindShortcuts(luna);
  await luna.sessions.ready();
  luna.status.onChange(() => {
    if (luna.status.value === 'disconnected') status('RECONNECTING', '', 'offline');
  });
  luna.sessions.focused.onChange(() => {
    if (plan && !busy) {
      renderChecks();
      renderSuggestions();
      refreshSend();
    }
  });
  $('choose').onclick = async () => {
    if (!(await luna.gui.available())) {
      status('NO FILE DIALOG', 'Open InterDAW inside LUNA to choose a set.', 'ready', true);
      return;
    }
    const [path] = await luna.gui.chooseFiles({ filters: ['*.als'] });
    if (path) await openSet(path);
  };
  $('install-cmd').value = AddOnLogic.HELPER_INSTALL;
  $('install-cmd').onfocus = () => $('install-cmd').select();
  $('install-copy').onclick = copyInstall;
  $('install-help').onclick = () => (luna.gui.available().then((ok) => (ok ? luna.gui.openUrl(AddOnLogic.HELP_URL) : window.open(AddOnLogic.HELP_URL))));
  $('import').onclick = runImport;
  $('go-all').onclick = runAll;
  $('send-go').onclick = runSend;
  $('send-refresh').onclick = refreshSend;
  for (const button of document.querySelectorAll('#send-format .idw-seg-btn')) {
    button.onclick = () => {
      if (busy) return;
      sendFormat = button.dataset.format;
      for (const other of document.querySelectorAll('#send-format .idw-seg-btn')) other.classList.toggle('is-on', other === button);
      refreshSend();
    };
  }
  $('send-reveal').onclick = () => {
    if (lastFolder) luna.gui.revealPath(lastFolder);
  };
  if (Luna.canDropPaths()) {
    Luna.onPathDrop({
      dragOver: () => $('drop').classList.add('is-hot'),
      dragLeave: () => $('drop').classList.remove('is-hot'),
      drop: ({ paths }) => {
        $('drop').classList.remove('is-hot');
        if (paths[0]) openSet(paths[0]);
      },
    });
  }
  status('READY', 'Choose or drop a Live set.');
  await watchHelper();
  const session = luna.sessions.current();
  if (session) {
    await session.ready();
    const remembered = await AddOnLogic.rememberedSet(session).catch(() => undefined);
    if (remembered && !plan) openSet(remembered);
  }
}

connect();
