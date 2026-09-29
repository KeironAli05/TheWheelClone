(() => {
  const page = document.body.dataset.page;
  const socket = window.io ? io() : null;
  const byId = (id) => document.getElementById(id);
  let latestState = null;
  let previousPhase = null;
  let revealTimer = null;
  let audioEnabled = false;
  let shareLinkState = { status: 'starting', url: null, message: 'The address will appear here when it is ready.' };

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);

  function showToast(target, message) {
    const node = byId(target);
    if (!node) return;
    node.textContent = message || '';
    if (message) window.setTimeout(() => { node.textContent = ''; }, 4200);
  }

  function command(action, values = {}) {
    if (!socket) return;
    socket.emit('host_command', { action, ...values }, (result) => {
      if (!result?.ok) showToast('host-toast', result?.error || 'The action could not be completed.');
    });
  }

  function selectForm(title, field, options, action, buttonText, emptyText) {
    if (!options.length) return `<div class="control-block"><h2>${escapeHtml(title)}</h2><p class="control-note">${escapeHtml(emptyText)}</p></div>`;
    return `<form class="control-block host-form" data-command="${action}"><h2>${escapeHtml(title)}</h2><select name="${field}" required><option value="">Choose one</option>${options.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join('')}</select><button class="button button-lime" type="submit">${escapeHtml(buttonText)} <span aria-hidden="true">→</span></button></form>`;
  }

  function answerForm(title, action) {
    const question = latestState?.current_question;
    if (!question) return '';
    return `<form class="control-block host-form answer-form" data-command="${action}"><h2>${escapeHtml(title)}</h2><div class="choice-grid">${question.options.map((option, index) => `<button class="answer-choice" type="submit" name="answer" value="${'ABCD'[index]}"><b>${'ABCD'[index]}</b><span>${escapeHtml(option)}</span></button>`).join('')}</div></form>`;
  }

  const findPowerup = (state, id) => (state.powerups || []).find((powerup) => powerup.id === id);
  const powerupConfirm = (state, powerup) => `Use ${powerup.name} for ${state.current_player_name}? This can't be undone.`;

  function powerupButton(state, id) {
    const powerup = findPowerup(state, id);
    if (!powerup) return '';
    const note = powerup.used ? ' · USED' : powerup.available ? '' : ' · NOT AVAILABLE';
    return `<button class="button ${powerup.available ? 'button-coral' : 'button-muted'}" type="button" data-action="use_powerup" data-powerup="${id}" data-confirm="${escapeHtml(powerupConfirm(state, powerup))}" ${powerup.available ? '' : 'disabled'}>${escapeHtml(powerup.name)}${note}</button>`;
  }

  function powerupControls(state, experts) {
    const peek = findPowerup(state, 'peek');
    const audience = state.audience;
    let html = `<div class="control-block powerup-controls"><h2>Power-ups</h2><div class="powerup-buttons">${powerupButton(state, 'fifty_fifty')}${powerupButton(state, 'ask_players')}</div>`;
    if (audience?.status === 'open') {
      html += `<p class="control-note">Players voting: ${audience.vote_count} / ${audience.voter_total} in.</p><button class="button button-lime" type="button" data-action="close_vote">Close vote and show results <span aria-hidden="true">→</span></button>`;
    } else if (audience?.status === 'closed') {
      html += `<p class="control-note">Players voted: ${'ABCD'.split('').map((letter) => `${letter} ${audience.counts[letter]}`).join(' · ')}</p>`;
    }
    if (state.peek) {
      html += `<p class="control-note">Peek: ${escapeHtml(state.peek.expert_name)} ${state.peek.answer ? `said ${escapeHtml(state.peek.answer)}` : 'has not answered yet'}.</p>`;
    } else if (peek?.available) {
      html += `<form class="host-form" data-command="use_powerup" data-confirm="${escapeHtml(powerupConfirm(state, peek))}"><input type="hidden" name="powerup" value="peek"><select name="expert_id" required><option value="">Peek at which expert?</option>${experts.map((expert) => `<option value="${escapeHtml(expert.id)}">${escapeHtml(expert.name)} · ${escapeHtml(expert.category)}</option>`).join('')}</select><button class="button button-coral" type="submit">Peek at an Expert</button></form>`;
    } else if (peek) {
      html += `<button class="button button-muted" type="button" disabled>Peek at an Expert · USED</button>`;
    }
    return `${html}</div>`;
  }

  function renderHost(state) {
    latestState = state;
    const phase = state.phase;
    const phaseTitle = {
      LOBBY: 'Ready when you are', PLAYER_SELECT: 'Pick a player', CATEGORY_SELECT: 'Choose their category',
      SHUTDOWN_SELECT: 'Shut an expert down', SPINNING: 'Spin the chair', LANDED: 'Keep or re-spin?', QUESTION: 'Question time',
      ANSWER_REVEAL: 'The reveal', FINAL_QUESTION: 'The birthday question', GAME_WON: 'We have a winner'
    }[phase] || phase;
    byId('host-phase').textContent = phaseTitle;
    byId('host-round').textContent = state.current_category ? state.current_category.toUpperCase() : '';
    const selectedPlayer = state.current_player_name ? `${state.current_player_name} is in the chair.` : '';
    const messages = {
      LOBBY: 'Get everyone in, then start the game.', PLAYER_SELECT: 'The player wheel picks at random. A player can come up more than once.',
      CATEGORY_SELECT: selectedPlayer, SHUTDOWN_SELECT: selectedPlayer,
      SPINNING: `${selectedPlayer} ${state.current_category ? `Category: ${state.current_category}.` : ''} Enter where the chair lands.`,
      LANDED: `${selectedPlayer} The chair landed on ${state.current_expert_name}.`,
      QUESTION: `${selectedPlayer} ${state.current_expert_name || ''} Experts have answered: ${state.expert_answer_count} / ${state.expert_answer_total}.`,
      ANSWER_REVEAL: state.last_result?.type === 'correct' ? 'Correct. That category is cleared.' : state.last_result?.type === 'final_incorrect' ? 'Wrong. The run is over; all categories are back.' : 'Wrong. All cleared categories are back.',
      FINAL_QUESTION: 'One last answer to win the presents.', GAME_WON: 'The birthday girl gets her presents!'
    };
    byId('host-message').textContent = messages[phase] || '';

    const experts = state.experts || [];
    const activeExperts = experts.filter((expert) => !expert.locked);
    const controls = byId('host-controls');
    if (phase === 'LOBBY') {
      controls.innerHTML = `<div class="control-block"><h2>${state.players.length} player${state.players.length === 1 ? '' : 's'} joined</h2><p class="control-note">${state.players.length ? 'Take your places.' : 'At least one player needs to join.'}</p><button class="button button-lime" type="button" data-action="start" ${state.players.length ? '' : 'disabled'}>Start game <span aria-hidden="true">→</span></button></div>`;
    } else if (phase === 'PLAYER_SELECT') {
      controls.innerHTML = `<div class="control-block"><h2>Who’s up?</h2><button class="button button-lime" type="button" data-action="select_player">Spin player wheel <span aria-hidden="true">→</span></button></div>`;
    } else if (phase === 'CATEGORY_SELECT') {
      controls.innerHTML = selectForm('Player’s pick', 'category', state.available_categories.map((name) => ({ value: name, label: name })), 'choose_category', 'Lock category', 'No categories remain.');
    } else if (phase === 'SHUTDOWN_SELECT') {
      controls.innerHTML = selectForm('Choose one expert to shut down', 'expert_id', activeExperts.map((expert) => ({ value: expert.id, label: `${expert.name} · ${expert.category}` })), 'choose_shutdown', 'Continue to spin', 'All experts are already locked out.');
    } else if (phase === 'SPINNING') {
      controls.innerHTML = `<div class="control-block host-form"><h2>Chair spinning</h2><button class="button button-muted" type="button" data-action="spin">Play spin jingle <span aria-hidden="true">♫</span></button></div>${selectForm('Where did they land?', 'expert_id', experts.map((expert) => ({ value: expert.id, label: `${expert.name} · ${expert.category}${expert.locked ? ' · LOCKED' : ''}` })), 'resolve_landing', 'Confirm landing', 'No experts configured.')}`;
    } else if (phase === 'LANDED') {
      controls.innerHTML = `<div class="control-block"><h2>Landed on ${escapeHtml(state.current_expert_name)}</h2><p class="control-note">Keep this expert, or use the player's one Re-spin. Shut-downs stay the same.</p><button class="button button-lime" type="button" data-action="confirm_landing">Show question <span aria-hidden="true">→</span></button>${powerupButton(state, 'respin')}</div>`;
    } else if (phase === 'QUESTION') {
      controls.innerHTML = `<div class="control-block"><h2>Expert answers</h2><p class="control-note">${state.expert_answer_count} of ${state.expert_answer_total} submitted. Answers stay private until the reveal.</p></div>${powerupControls(state, experts)}${answerForm('Enter the player’s answer', 'reveal_answer')}`;
    } else if (phase === 'ANSWER_REVEAL') {
      const resultName = state.last_result?.type === 'correct' ? 'PLAYER CORRECT' : state.last_result?.type === 'final_incorrect' ? 'FINAL ANSWER WRONG' : 'PLAYER WRONG · RUN RESET';
      const advanceLabel = state.pending_final_question ? 'Go to final question' : state.last_result?.type === 'correct' ? 'Choose next category' : 'Select next player';
      controls.innerHTML = `<div class="control-block"><h2>${resultName}</h2><p class="control-note">Correct answer: ${escapeHtml(state.current_question?.correct)}. Expert answers are shown in the roster.</p><button class="button button-lime" type="button" data-action="advance">${advanceLabel} <span aria-hidden="true">→</span></button></div>`;
    } else if (phase === 'FINAL_QUESTION') {
      controls.innerHTML = answerForm('Enter the final answer', 'reveal_final_answer');
    } else if (phase === 'GAME_WON') {
      controls.innerHTML = `<div class="control-block"><h2>Birthday presents unlocked.</h2><button class="button button-coral" type="button" data-action="reset">Reset game</button></div>`;
    }

    byId('player-roster').innerHTML = state.players.length ? state.players.map((player) => `<li>${escapeHtml(player.name)}<small>${player.correct_answers} / ${player.questions_answered}</small></li>`).join('') : '<li class="empty-row">No players yet</li>';
    byId('expert-roster').innerHTML = experts.map((expert) => {
      const score = expert.questions_answered ? `${expert.correct_answers} / ${expert.questions_answered}` : 'No answers yet';
      const details = expert.joined ? `${expert.category} · ${score}` : 'Not joined';
      const status = expert.locked ? ' · LOCKED' : expert.turn_shutdown ? ' · SHUT DOWN' : '';
      return `<li>${escapeHtml(expert.name)}<small>${escapeHtml(details)}${status}</small></li>`;
    }).join('') || '<li class="empty-row">No experts configured</li>';
    byId('room-count').textContent = String(state.players.length + experts.filter((expert) => expert.joined).length);
    if (phase !== 'GAME_WON') controls.insertAdjacentHTML('beforeend', '<button class="button button-muted reset-control" type="button" data-action="reset">Reset game</button>');
  }

  function renderParticipant(state) {
    latestState = state;
    const content = byId('participant-content');
    const role = document.body.dataset.role;
    if (role === 'expert') {
      const expertId = document.body.dataset.expertId;
      const expert = state.experts.find((entry) => entry.id === expertId);
      const category = byId('participant-category');
      if (category && expert) category.textContent = expert.category;
      const isLocked = expert?.locked;
      if (state.phase === 'QUESTION' && state.current_question) {
        if (expert?.answered) {
          content.innerHTML = '<div class="answered-banner">ANSWER LOCKED ✓</div><p class="waiting-copy">Your answer is in. Stay tuned for the reveal.</p>';
          return;
        }
        content.innerHTML = `<h2>${escapeHtml(state.current_question.text)}</h2><div class="answer-grid">${state.current_question.options.map((option, index) => `<button class="phone-answer" type="button" data-answer="${'ABCD'[index]}"><b>${'ABCD'[index]}</b><span>${escapeHtml(option)}</span></button>`).join('')}</div>`;
        return;
      }
      const status = isLocked ? 'LOCKED FOR THIS SPIN' : state.phase === 'ANSWER_REVEAL' ? 'REVEAL TIME' : state.phase === 'GAME_WON' ? 'GAME WON' : 'ACTIVE';
      content.innerHTML = `<div class="status-line"><span class="status-orb ${isLocked ? 'locked' : 'active'}"></span><strong>${status}</strong></div><p class="waiting-copy">${state.phase === 'ANSWER_REVEAL' ? 'The answer and expert results are on the screen.' : 'Your phone will show the question when it is time to answer.'}</p>`;
      return;
    }

    const isUp = state.current_player_id === document.body.dataset.id;
    if (state.phase === 'GAME_WON') {
      content.innerHTML = '<div class="status-line"><span class="status-orb active"></span><strong>GAME WON</strong></div><p class="waiting-copy">The birthday girl gets her presents!</p>';
    } else if (!isUp && state.phase === 'QUESTION' && state.audience?.status === 'open') {
      content.innerHTML = state.audience_voted
        ? '<div class="answered-banner">VOTE LOCKED ✓</div><p class="waiting-copy">Results will appear on the TV.</p>'
        : `<h2>Ask the Players</h2><p class="waiting-copy">Read the question on the TV, then vote.</p><div class="answer-grid vote-grid">${'ABCD'.split('').map((letter) => `<button class="phone-answer" type="button" data-vote="${letter}"><b>${letter}</b></button>`).join('')}</div>`;
    } else if (isUp) {
      content.innerHTML = '<div class="youre-up">YOU’RE UP!</div><p class="waiting-copy">Head to the chair. The host will guide your turn.</p>';
    } else {
      content.innerHTML = `<div class="status-line"><span class="status-orb"></span><strong>${state.phase === 'LOBBY' ? 'READY' : 'WAITING'}</strong></div><p class="waiting-copy">${state.phase === 'LOBBY' ? 'You’re in. The host will start when everyone is ready.' : 'The host will let you know when it’s your turn.'}</p>`;
    }
  }

  function renderDisplay(state) {
    latestState = state;
    const question = state.current_question;
    const phase = state.phase;
    byId('display-category').textContent = state.current_category || (phase === 'PLAYER_SELECT' ? 'NEXT UP' : '');
    byId('display-question').textContent = question?.text || (phase === 'LOBBY' ? 'Happy birthday!' : phase === 'GAME_WON' ? 'The birthday girl gets her presents!' : phase === 'PLAYER_SELECT' ? 'Who’s next?' : phase === 'LANDED' ? `Landed on ${state.current_expert_name}` : 'Get ready.');
    byId('active-player-name').textContent = state.current_player_name || 'No player yet';
    byId('display-subtext').hidden = Boolean(question);
    byId('answer-count').textContent = `${state.expert_answer_count} / ${state.expert_answer_total} in`;
    const revealed = phase === 'ANSWER_REVEAL' || phase === 'GAME_WON';
    const playerAnswer = revealed ? state.player_answer : null;
    byId('display-options').innerHTML = question ? question.options.map((option, index) => {
      const letter = 'ABCD'[index];
      const classes = ['display-option'];
      if (revealed && question.correct === letter) classes.push('is-correct');
      if (revealed && playerAnswer === letter) classes.push('is-player-answer');
      if ((state.fifty_fifty_removed || []).includes(letter)) classes.push('is-removed');
      return `<div class="${classes.join(' ')}"><b>${letter}</b><span>${escapeHtml(option)}</span></div>`;
    }).join('') : '';
    renderPowerupEffects(state);
    const resultLabel = state.last_result?.type === 'correct' ? 'CORRECT · CATEGORY CLEARED' : state.last_result?.type === 'incorrect' ? 'WRONG · ALL CATEGORIES RESET' : state.last_result?.type === 'final_incorrect' ? 'NOT THIS TIME · RUN RESET' : state.last_result?.type === 'game_won' ? 'THE WHEEL IS WON' : '';
    byId('display-result').textContent = revealed ? resultLabel : '';
    byId('category-list').innerHTML = state.categories.map((category) => `<div class="category-item ${category.cleared ? 'cleared' : ''} ${category.name === state.current_category ? 'current' : ''}"><span>${escapeHtml(category.name)}</span><i></i></div>`).join('');
    byId('progress-count').textContent = `${state.categories.filter((category) => category.cleared).length} / ${state.categories.length}`;
    byId('display-experts').innerHTML = state.experts.map((expert) => `<div class="expert-item ${expert.locked ? 'locked' : ''}"><span>${escapeHtml(expert.name)}</span><span class="expert-answer">${revealed ? escapeHtml(state.expert_answers[expert.id] || '—') : expert.answered ? 'IN' : ''}</span></div>`).join('');
    renderShareLink(phase);
    if (phase === 'CATEGORY_SELECT' && previousPhase === 'PLAYER_SELECT') animatePlayerSelection(state);
    else if (phase !== 'CATEGORY_SELECT') hidePlayerReveal();
    previousPhase = phase;
  }

  function renderPowerupEffects(state) {
    byId('powerup-strip').innerHTML = (state.powerups || []).map((powerup) => {
      const status = powerup.used ? 'USED' : powerup.available ? 'READY' : '';
      return `<div class="powerup-item ${powerup.used ? 'used' : ''} ${powerup.available ? 'ready' : ''}"><span>${escapeHtml(powerup.name)}</span><small>${status}</small></div>`;
    }).join('');
    const peek = state.peek;
    byId('display-peek').textContent = peek ? `PEEK · ${peek.expert_name.toUpperCase()} ${peek.answer ? `SAID ${peek.answer}` : 'IS STILL THINKING…'}` : '';
    const audience = state.audience;
    const chart = byId('audience-chart');
    if (!audience) {
      chart.innerHTML = '';
    } else if (audience.status === 'open') {
      chart.innerHTML = `<p class="audience-title">ASK THE PLAYERS · ${audience.vote_count} / ${audience.voter_total} VOTED</p>`;
    } else {
      const total = Object.values(audience.counts).reduce((sum, count) => sum + count, 0);
      chart.innerHTML = `<p class="audience-title">THE PLAYERS SAY</p><div class="audience-bars">${'ABCD'.split('').map((letter) => {
        const percent = total ? Math.round((audience.counts[letter] / total) * 100) : 0;
        return `<div class="audience-bar"><span class="audience-value">${percent}%</span><i style="height:${percent}%"></i><b>${letter}</b></div>`;
      }).join('')}</div>`;
    }
  }

  function renderShareLink(phase = latestState?.phase) {
    const panel = byId('display-share');
    if (!panel) return;
    panel.hidden = phase !== 'LOBBY';
    byId('display-share-url').textContent = shareLinkState.url || shareLinkState.message;
    byId('display-share-message').textContent = shareLinkState.url
      ? 'Share this address with guests.'
      : 'The server terminal also reports tunnel startup status.';
  }

  function hidePlayerReveal() {
    window.clearTimeout(revealTimer);
    const reveal = byId('player-reveal');
    if (reveal) reveal.hidden = true;
  }

  function animatePlayerSelection(state) {
    const reveal = byId('player-reveal');
    if (!reveal) return;
    window.clearTimeout(revealTimer);
    reveal.hidden = false;
    const names = state.players.map((player) => player.name);
    const winner = state.current_player_name;
    let count = 0;
    const cycle = () => {
      reveal.textContent = count > 10 ? winner : names[Math.floor(Math.random() * names.length)] || winner;
      reveal.classList.remove('animate');
      void reveal.offsetWidth;
      reveal.classList.add('animate');
      count += 1;
      if (count > 13) {
        revealTimer = window.setTimeout(() => { reveal.textContent = `YOU’RE UP, ${winner.toUpperCase()}`; }, 200);
      } else {
        revealTimer = window.setTimeout(cycle, 95 + count * 31);
      }
    };
    cycle();
  }

  function updateLobby(lobby) {
    if (page !== 'landing') return;
    const hostInput = document.querySelector('#host-role input');
    hostInput.disabled = !lobby.host_available;
    document.getElementById('host-role').classList.toggle('role-unavailable', !lobby.host_available);
    const select = byId('expert_id');
    const selected = select?.value;
    if (select) {
      const available = lobby.experts.filter((expert) => expert.available);
      select.innerHTML = '<option value="">Select an available expert</option>' + available.map((expert) => `<option value="${escapeHtml(expert.id)}">${escapeHtml(expert.name)} · ${escapeHtml(expert.category)}</option>`).join('');
      if (available.some((expert) => expert.id === selected)) select.value = selected;
      byId('expert-empty').hidden = available.length > 0;
    }
    const joined = lobby.experts.filter((expert) => !expert.available).length;
    byId('join-count').textContent = `${joined} expert${joined === 1 ? '' : 's'} joined`;
  }

  if (page === 'landing') {
    document.querySelectorAll('input[name="role"]').forEach((radio) => radio.addEventListener('change', () => {
      byId('expert-fields').hidden = radio.value !== 'expert';
      byId('player-fields').hidden = radio.value !== 'player';
    }));
  }

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-command]');
    if (!form) return;
    event.preventDefault();
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    const values = Object.fromEntries(new FormData(form, event.submitter).entries());
    command(form.dataset.command, values);
  });

  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-action]');
    if (action) {
      if (action.dataset.action === 'reset' && !window.confirm('Reset the game and clear all progress?')) return;
      if (action.dataset.confirm && !window.confirm(action.dataset.confirm)) return;
      command(action.dataset.action, action.dataset.powerup ? { powerup: action.dataset.powerup } : {});
    }
    const vote = event.target.closest('[data-vote]');
    if (vote && socket) {
      vote.disabled = true;
      socket.emit('submit_vote', { answer: vote.dataset.vote }, (result) => {
        if (!result?.ok) {
          vote.disabled = false;
          showToast('participant-toast', result?.error || 'The vote could not be submitted.');
        }
      });
    }
    const answer = event.target.closest('[data-answer]');
    if (answer && socket) {
      answer.disabled = true;
      socket.emit('submit_answer', { answer: answer.dataset.answer }, (result) => {
        if (!result?.ok) {
          answer.disabled = false;
          showToast('participant-toast', result?.error || 'The answer could not be submitted.');
        }
      });
    }
  });

  if (socket) {
    socket.on('connect', () => {
      const status = byId('connection-status');
      if (status) { status.classList.remove('offline'); status.innerHTML = '<i></i> Connected'; }
      if (page === 'display') socket.emit('display_join');
    });
    socket.on('disconnect', () => {
      const status = byId('connection-status');
      if (status) { status.classList.add('offline'); status.innerHTML = '<i></i> Reconnecting'; }
    });
    socket.on('lobby', updateLobby);
    socket.on('state', (state) => {
      if (page === 'host') renderHost(state);
      if (page === 'participant') renderParticipant(state);
      if (page === 'display') renderDisplay(state);
    });
    socket.on('share_link', (state) => {
      shareLinkState = state;
      renderShareLink();
    });
    socket.on('cue', ({ name }) => {
      const audio = byId('game-audio');
      if (!audio || !audioEnabled) return;
      const filenames = {
        player_select: 'player-select.mp3', spin: 'spin-start.mp3', spin_stop: 'spin-stop.mp3',
        question: 'question-reveal.mp3', correct: 'correct.mp3', incorrect: 'incorrect.mp3',
        final_reveal: 'final-reveal.mp3', victory: 'victory.mp3', powerup: 'powerup.mp3'
      };
      if (!filenames[name]) return;
      audio.src = `/static/audio/${filenames[name]}`;
      audio.play().catch(() => {});
    });
  }

  const audioToggle = byId('audio-toggle');
  if (audioToggle) {
    audioToggle.addEventListener('click', () => {
      audioEnabled = !audioEnabled;
      audioToggle.setAttribute('aria-pressed', String(audioEnabled));
      audioToggle.setAttribute('aria-label', audioEnabled ? 'Disable TV audio' : 'Enable TV audio');
      audioToggle.title = audioEnabled ? 'Disable TV audio' : 'Enable TV audio';
      audioToggle.querySelector('span').textContent = audioEnabled ? 'Sound on' : 'Sound off';
    });
  }
})();