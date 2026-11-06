'use strict';

window.Study = (() => {
  const state = {context: null, importId: null, report: null, replay: null, ply: 0, flipped: false, sequence: 0, replaySequence: 0, timer: null, preview: null};
  const $ = selector => document.querySelector(selector);
  const fmt = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
  const pct = value => value == null ? '—' : `${(100 * value).toFixed(1)}%`;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const button = (text, callback, className = 'secondary') => {
    const element = node('button', text, className);
    element.type = 'button';
    element.addEventListener('click', () => Promise.resolve(callback()).catch(showError));
    return element;
  };

  function status(message, error = false) {
    const element = $('#study-import-status');
    element.textContent = message;
    element.className = error ? 'callout' : 'muted';
  }
  function showError(error) { status(error.message || String(error), true); }

  async function api(route, body, method) {
    const options = {method: method || (body == null ? 'GET' : 'POST')};
    if (body != null) {
      options.headers = {'X-Study-Action': '1'};
      if (body instanceof FormData) options.body = body;
      else {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
      }
    }
    const response = await fetch(`/api/personal/${route}`, options);
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Personal study request failed');
    return value;
  }

  function table(headers, rows) {
    const wrapper = node('div', null, 'table-wrap');
    const result = node('table');
    const head = node('thead');
    const header = node('tr');
    for (const text of headers) {
      const cell = node('th', text);
      cell.scope = 'col';
      header.append(cell);
    }
    head.append(header);
    result.append(head);
    const body = node('tbody');
    for (const values of rows) {
      const row = node('tr');
      for (const value of values) {
        const cell = node('td');
        if (value instanceof Node) cell.append(value);
        else cell.textContent = value == null ? '—' : String(value);
        row.append(cell);
      }
      body.append(row);
    }
    result.append(body);
    wrapper.append(result);
    if (!rows.length) wrapper.append(node('p', 'No games match this selection.', 'muted'));
    return wrapper;
  }

  function filters() {
    return Object.fromEntries([...new FormData($('#study-filters'))].filter(([, value]) => value));
  }

  async function loadContext() {
    const previous = state.importId;
    state.context = await api('context');
    const c = state.context;
    $('#study-player').value = c.player;
    $('#study-month').value = c.month;
    $('#study-scope').textContent = `${c.player} · ${c.month} UTC · up to ${c.max_games} games per version. ${c.scope}`;
    $('#study-manual-download').href = c.manual_export_url;
    $('#study-version').replaceChildren(...c.versions.map(v => {
      const option = node('option', `v${v.version} · ${v.game_count} games`);
      option.value = v.id;
      return option;
    }));
    state.importId = c.versions.some(v => v.id === previous) ? previous : c.active_import_id;
    if (state.importId) $('#study-version').value = state.importId;
    $('#study-loaded').hidden = !state.importId;
    const box = $('#study-cohorts');
    box.replaceChildren(table(['Saved cohort', 'Version', 'Downloads'], c.cohorts.map(cohort => {
      const version = c.versions.find(v => v.id === cohort.import_id);
      const links = node('span');
      for (const type of ['parquet', 'pgn', 'report', 'manifest']) {
        const link = node('a', `${type} `, 'text-link');
        link.href = `/api/personal/export?cohort_id=${encodeURIComponent(cohort.id)}&format=${type}`;
        links.append(link);
      }
      return [cohort.name, version ? `v${version.version}` : cohort.import_id, links];
    })));
  }

  async function refresh() {
    if (!state.importId) return;
    const sequence = ++state.sequence;
    const report = await api('report', {import_id: state.importId, filters: filters()});
    if (sequence !== state.sequence) return;
    state.report = report;
    renderReport();
    renderEngine();
    if (report.analysis_jobs.some(j => ['queued', 'running'].includes(j.status))) {
      clearTimeout(state.timer);
      state.timer = setTimeout(() => {
        if (!$('#personal').hidden) refresh().catch(showError);
      }, 3000);
    }
  }

  function renderReport() {
    const r = state.report;
    const metrics = $('#study-metrics');
    metrics.replaceChildren();
    for (const [label, value, note] of [
      ['Games in study', fmt(r.summary.games), `Version ${r.version} · ${r.month}`],
      ['My score', pct(r.summary.score_rate), 'Win = 1 · Draw = ½ · Loss = 0'],
      ['Wins / draws / losses', `${r.summary.wins} / ${r.summary.draws} / ${r.summary.losses}`, 'Player-perspective results'],
      ['Average opponent', fmt(r.summary.average_opponent_rating), `${r.summary.missing_rating_games} games have missing ratings`]
    ]) {
      const card = node('article', null, 'metric');
      card.append(node('span', label, 'label'), node('strong', value), node('small', note));
      metrics.append(card);
    }
    const selected = $('#study-opening').value;
    const names = new Set([...$('#study-opening').options].map(o => o.value).filter(Boolean));
    r.openings.forEach(o => names.add(o.opening));
    $('#study-opening').replaceChildren(node('option', 'All openings'), ...[...names].sort().map(name => {
      const option = node('option', name); option.value = name; return option;
    }));
    $('#study-opening').options[0].value = '';
    $('#study-opening').value = selected;
    $('#study-openings').replaceChildren(table(
      ['Opening', 'Games', 'Score', 'Win-rate interval', 'Matched reference', 'Coverage', 'Interpretation'],
      r.openings.map(o => [
        button(`${o.eco || '—'} · ${o.opening}`, () => openGame(o.example_games[0]), 'study-game-button'),
        o.games, pct(o.score_rate), o.win_rate_interval_95 ? `${pct(o.win_rate_interval_95[0])}–${pct(o.win_rate_interval_95[1])}` : '—',
        pct(o.reference_score_rate), pct(o.matched_coverage), `${o.evidence_level}: ${o.sample_note}`
      ])
    ));
    const candidates = $('#study-candidates');
    candidates.replaceChildren();
    for (const item of r.review_candidates.slice(0, 8)) {
      const card = node('div', null, 'study-candidate');
      card.append(button(`${item.game_id}${item.ply ? ` · ply ${item.ply}` : ''}`, () => openGame(item.game_id, item.ply ? item.ply - 1 : 0), 'study-game-button'),
        node('p', item.reason), node('small', item.evidence_type, 'muted'));
      candidates.append(card);
    }
    const games = table(['Game', 'Date', 'Color', 'Opponent', 'Result', 'Opening'], r.games.map(g => [
      button(g.game_id, () => openGame(g.game_id), 'study-game-button'), g.game_date, g.player_color,
      `${fmt(g.opponent_rating)} · ${g.opponent_band}`, g.result, g.opening
    ]));
    games.classList.add('study-game-list');
    $('#study-games').replaceChildren(games);
    const progress = r.review_progress;
    $('#study-progress').textContent = `${progress.filter(p => p.status === 'reviewed').length} reviewed · ${progress.filter(p => p.status === 'practiced').length} practiced · ${progress.filter(p => p.status === 'queued').length} queued`;
    for (const type of ['parquet', 'pgn', 'report', 'manifest']) {
      $(`#study-export-${type}`).href = `/api/personal/export?import_id=${encodeURIComponent(r.import_id)}&format=${type}`;
    }
  }

  async function openGame(gameId, ply = 0) {
    const sequence = ++state.replaySequence;
    const replay = await api(`games/${gameId}?import_id=${encodeURIComponent(state.importId)}`);
    if (sequence !== state.replaySequence) return;
    state.replay = replay;
    state.preview = null;
    state.ply = Math.max(0, Math.min(ply, state.replay.moves.length));
    state.flipped = state.replay.player_color === 'black';
    $('#study-replay-title').textContent = `${state.replay.white} — ${state.replay.black}`;
    $('#study-ply').max = state.replay.moves.length;
    const list = $('#study-moves');
    list.replaceChildren(...state.replay.moves.map(move => button(
      `${move.fullmove}${move.color === 'white' ? '.' : '…'} ${move.san}`,
      () => { state.preview = null; state.ply = move.ply; renderBoard(); }
    )));
    $('#study-positions').replaceChildren();
    renderBoard();
    renderEngine();
  }

  function renderBoard() {
    if (!state.replay) return;
    // A position result belongs to the board that was searched, never a later ply.
    $('#study-positions').replaceChildren();
    const fen = state.preview ? state.preview.fen : state.replay.positions[state.ply];
    const ranks = fen.split(' ')[0].split('/').map(rank => {
      const cells = [];
      for (const char of rank) {
        if (/\d/.test(char)) cells.push(...Array(Number(char)).fill(''));
        else cells.push(char);
      }
      return cells;
    });
    const pieces = {k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟'};
    const board = $('#study-board');
    board.replaceChildren();
    const last = state.ply && !state.preview ? state.replay.moves[state.ply - 1] : null;
    for (let rankIndex = 0; rankIndex < 8; rankIndex++) {
      for (let fileIndex = 0; fileIndex < 8; fileIndex++) {
        const rank = state.flipped ? 7 - rankIndex : rankIndex;
        const file = state.flipped ? 7 - fileIndex : fileIndex;
        const square = `${'abcdefgh'[file]}${8 - rank}`;
        const piece = ranks[rank][file];
        const cell = node('div', null, `chess-square ${(rank + file) % 2 ? 'dark' : 'light'}`);
        if (last && [last.uci.slice(0, 2), last.uci.slice(2, 4)].includes(square)) cell.classList.add('last-move');
        if (piece) cell.append(node('span', pieces[piece.toLowerCase()], piece === piece.toUpperCase() ? 'chess-white' : 'chess-black'));
        cell.append(node('small', square));
        cell.title = square;
        board.append(cell);
      }
    }
    board.setAttribute('aria-label', `${state.preview ? 'Engine variation: '+state.preview.label : 'Position after ply '+state.ply+' of game '+state.replay.game_id}. ${fen.split(' ')[1] === 'w' ? 'White' : 'Black'} to move.`);
    $('#study-ply').value = state.ply;
    const annotation = last && last.uploaded_eval_cp != null ? ` · uploaded evaluation ${(last.uploaded_eval_cp / 100).toFixed(2)} (unverified)` : '';
    const clock = last && last.clock_seconds != null ? ` · clock ${last.clock_seconds.toFixed(1)}s` : '';
    $('#study-position-label').textContent = state.preview ? `ENGINE VARIATION · ${state.preview.label} · not the played game` : `${state.replay.game_id} · ply ${state.ply}/${state.replay.moves.length}${clock}${annotation}`;
    [...$('#study-moves').children].forEach((item, i) => item.classList.toggle('active', i + 1 === state.ply));
    const saved = state.report.review_progress.find(item => item.game_id === state.replay.game_id && item.ply === state.ply);
    $('#study-note textarea').value = saved ? saved.note : '';
    $('#study-note select').value = saved ? saved.status : 'queued';
  }

  function resetReplay() {
    state.replay = null;
    state.preview = null;
    state.replaySequence++;
    $('#study-board').replaceChildren();
    $('#study-replay-title').textContent = 'Select a game.';
    $('#study-position-label').textContent = 'No game selected.';
    $('#study-moves').replaceChildren();
    $('#study-engine').replaceChildren();
    $('#study-positions').replaceChildren();
    $('#study-answer').replaceChildren();
    $('#study-validation').replaceChildren();
  }

  function renderEngine() {
    const container = $('#study-engine');
    container.replaceChildren();
    if (!state.replay || !state.report) return;
    const jobs = state.report.analysis_jobs.filter(job => job.game_id === state.replay.game_id);
    for (const job of jobs) {
      container.append(node('p', `Engine job: ${job.status}`, 'badge'));
      if (job.error) container.append(node('p', job.error, 'callout'));
      if (!job.evidence || job.status !== 'complete') continue;
      const evidence = job.evidence;
      container.append(node('p', `${evidence.engine} · ${evidence.analyzed_plies}/${evidence.total_plies} plies analyzed · ${evidence.complete_coverage ? 'full game coverage' : 'partial coverage'} · ${evidence.budget.nodes} nodes/position · ${evidence.budget.threads} thread`, 'muted'));
      if (!evidence.candidates.length) container.append(node('p', 'No review candidate crossed the configured threshold in the analyzed portion. This does not prove error-free play.'));
      for (const candidate of evidence.candidates) {
        const item = node('div', null, 'study-engine-item');
        item.append(button(`Review before ply ${candidate.ply}: ${candidate.played_san}`, () => {
          state.preview = null; state.ply = candidate.ply - 1; renderBoard();
        }, 'study-game-button'), node('h3', candidate.motif || 'Engine review candidate'), node('p', candidate.explanation || candidate.summary), node('p', `Engine line: ${candidate.pv_san.join(' ')}`), node('p', candidate.practice, 'muted'));
        const previews = node('div', null, 'study-actions');
        for (const [name, positions, moves] of [['Preferred', candidate.preferred_positions || [], candidate.pv_san], ['Opponent reply', candidate.reply_positions || [], candidate.reply_pv_san || []]]) {
          moves.forEach((move, index) => {
            if (!positions[index + 1]) return;
            previews.append(button(`${name} ${index + 1}: ${move}`, () => {
              state.preview = {fen: positions[index + 1], label: `${name} line after ${move}`}; renderBoard();
            }));
          });
        }
        previews.append(button('Return to played game', () => { state.preview = null; renderBoard(); }));
        item.append(previews);
        container.append(item);
      }
    }
  }

  async function open() {
    try {
      await loadContext();
      if (state.importId) await refresh();
      const stamp = document.querySelector('.stamp strong');
      if (stamp) stamp.textContent = `${state.context.player} · ${state.context.month}`;
    } catch (error) { showError(error); }
  }

  $('#study-import').addEventListener('submit', async event => {
    event.preventDefault();
    const control = event.target.querySelector('button');
    control.disabled = true;
    try {
      if (!state.context) throw new Error('Load the approved personal-study configuration first.');
      const form = new FormData(event.target);
      const file = form.get('file');
      if (!file || file.size > state.context.max_upload_bytes) throw new Error('Choose a PGN file of at most 10 MiB.');
      status('Validating your PGN and publishing its immutable study version…');
      const result = await api('imports', form);
      state.importId = result.import_id;
      resetReplay();
      await loadContext();
      await refresh();
      status(`Version ${result.version}: ${result.games} games · ${result.duplicate_games} repeated · ${result.corrected_games} corrected${result.unchanged ? ' · existing version reused' : ''}.`);
    } catch (error) { showError(error); }
    finally { control.disabled = false; }
  });
  $('#study-filters').addEventListener('submit', event => {
    event.preventDefault();
    state.importId = $('#study-version').value;
    resetReplay();
    refresh().catch(showError);
  });
  $('#study-ply').addEventListener('input', event => { state.preview = null; state.ply = Number(event.target.value); renderBoard(); });
  for (const [id, value] of [['study-start', () => 0], ['study-prev', () => state.ply - 1], ['study-next', () => state.ply + 1], ['study-end', () => state.replay.moves.length]]) {
    $(`#${id}`).addEventListener('click', () => {
      if (!state.replay) return;
      state.preview = null; state.ply = Math.max(0, Math.min(value(), state.replay.moves.length)); renderBoard();
    });
  }
  $('#study-flip').addEventListener('click', () => { state.flipped = !state.flipped; renderBoard(); });
  $('#study-position').addEventListener('click', async () => {
    try {
      if (!state.replay) throw new Error('Select a game first.');
      if (state.preview) throw new Error('Return to the played game before searching its replay position.');
      const searched = [state.importId, state.replay.game_id, state.ply, JSON.stringify(filters())].join('|');
      const result = await api('positions', {import_id: state.importId, game_id: state.replay.game_id, ply: state.ply, filters: filters()});
      if (!state.replay || state.preview || searched !== [state.importId, state.replay.game_id, state.ply, JSON.stringify(filters())].join('|')) return;
      $('#study-positions').replaceChildren(node('h3', `${result.games} games reached this position`),
        table(['Observed continuation', 'Games', 'My score', 'Win-rate interval'], result.alternatives.map(move => {
          const example = result.examples.find(item => item.next_move === move.move);
          const link = example ? button(move.move, () => openGame(example.game_id, example.ply + (move.move === 'Game ended' ? 0 : 1)), 'study-game-button') : move.move;
          return [link, move.games, pct(move.score_rate), `${pct(move.win_rate_interval_95[0])}–${pct(move.win_rate_interval_95[1])}`];
        })),
        node('p', result.interpretation, 'muted'));
    } catch (error) { showError(error); }
  });
  $('#study-analyze').addEventListener('click', async () => {
    try {
      if (!state.replay) throw new Error('Select a game first.');
      const job = await api('analysis', {import_id: state.importId, game_id: state.replay.game_id});
      status(`Engine review ${job.status}; the bounded worker will publish its evidence here.`);
      await refresh();
    } catch (error) { showError(error); }
  });
  $('#study-note').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      if (!state.replay) throw new Error('Select a game first.');
      if (state.preview) throw new Error('Return to the played game before saving a note on its replay position.');
      await api('review', {import_id: state.importId, game_id: state.replay.game_id, ply: state.ply, ...Object.fromEntries(new FormData(event.target))});
      await refresh(); status('Study-list item saved.');
    } catch (error) { showError(error); }
  });
  $('#study-save-cohort').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api('cohorts', {import_id: state.importId, name: new FormData(event.target).get('name'), filters: filters()});
      await loadContext(); status('Cohort saved with its exact dataset version and filters.');
    } catch (error) { showError(error); }
  });
  $('#study-question').addEventListener('submit', async event => {
    event.preventDefault();
    const control = event.target.querySelector('button');
    control.disabled = true;
    try {
      if (state.preview) throw new Error('Return to the played game before asking about its replay position.');
      const answer = await api('assistant', {import_id: state.importId, question: new FormData(event.target).get('question'), filters: filters(), game_id: state.replay ? state.replay.game_id : null, ply: state.ply});
      const box = $('#study-answer');
      box.replaceChildren(node('p', answer.answer), node('p', `Evidence: version ${answer.version} · operation ${answer.intent} · routing ${answer.routing}`, 'muted'));
      box.append(node('p', `Applied filters: ${Object.entries(answer.filters).map(([key,value]) => `${key}=${value}`).join(', ') || 'all games in this version'}`, 'muted'));
      for (const reference of answer.references || []) box.append(button(reference.game_id, () => openGame(reference.game_id, reference.ply || 0), 'text-link'));
    } catch (error) { showError(error); }
    finally { control.disabled = false; }
  });
  $('#study-validate-model').addEventListener('click', async () => {
    const control = $('#study-validate-model');
    control.disabled = true;
    try {
      const result = await api('ml-validation', {import_id: state.importId});
      const r = result.report;
      $('#study-validation').replaceChildren(node('p', `${r.rows} later approved games · frozen model ${r.model_run_id}`),
        table(['Model', 'Accuracy', 'Log loss', 'Multiclass Brier'], Object.entries(r.models).map(([name, value]) => [name, pct(value.accuracy), value.log_loss.toFixed(4), value.calibration.multiclass_brier.toFixed(4)])),
        node('p', r.scope, 'callout'));
    } catch (error) { showError(error); }
    finally { control.disabled = false; }
  });
  return {open};
})();
