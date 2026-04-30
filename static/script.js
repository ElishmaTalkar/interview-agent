// ── State ──────────────────────────────────────────────────────────────────────
const S = {
  setup: null,
  questions: [],
  history: [],
  scores: [],
  currentQ: 0,
  probeCount: 0,
  recording: false,
  mediaRecorder: null,
  audioChunks: [],
};

// ── Panel navigation ───────────────────────────────────────────────────────────
const PANELS   = ['panel-intake','panel-qbank','panel-interview','panel-feedback'];
const PROGRESS = [25, 50, 75, 100];

function goToPanel(id) {
  PANELS.forEach(p => document.getElementById(p).classList.remove('active'));
  document.getElementById(id).classList.add('active');

  // Update step indicators only if they exist in the DOM
  const idx = PANELS.indexOf(id);
  ['step1','step2','step3','step4'].forEach((s, i) => {
    const el = document.getElementById(s);
    if (!el) return;
    const num = el.querySelector('.step-num');
    el.classList.remove('active','done');
    if      (i < idx)  { el.classList.add('done');   num.textContent = '✓'; }
    else if (i === idx) { el.classList.add('active'); num.textContent = i+1; }
    else                { num.textContent = i+1; }
  });

  // Update progress bar only if it exists
  const prog = document.getElementById('progress');
  if (prog) prog.style.width = PROGRESS[idx] + '%';

  // Update status badge only if it exists
  const badge = document.getElementById('status-badge');
  if (badge) {
    if (id === 'panel-interview') { badge.textContent = 'Live';     badge.classList.add('live'); }
    else { badge.classList.remove('live'); badge.textContent = id === 'panel-feedback' ? 'Complete' : 'Ready'; }
  }
}

// ── Intake ─────────────────────────────────────────────────────────────────────
async function startInterview() {
  const role  = document.getElementById('role-input').value.trim();
  const bg    = document.getElementById('bg-input').value.trim();
  const focus = document.querySelector('#focus-group .chip.selected')?.textContent || 'Mixed';
  const diff  = document.querySelector('.diff-btn.selected')?.textContent || 'Junior';
  const co    = document.getElementById('company-input')?.value.trim() || '';

  if (!role || !bg) { alert('Please fill in Target Role and Your Background.'); return; }

  // Show loading state on the button
  const btn = document.querySelector('#panel-intake .btn-primary');
  const originalText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:18px;height:18px;border-width:2px;margin:0"></div> Generating…';

  try {
    const res  = await fetch('/api/intake', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ role, background: bg, focus, difficulty: diff, company: co }),
    });
    const data = await res.json();
    S.setup     = data.setup;
    S.questions = data.questions;
    // Skip question bank — go straight to interview
    beginVoiceInterview();
  } catch(e) {
    alert('Failed to generate questions: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalText;
  }
}

// ── Question bank ──────────────────────────────────────────────────────────────
function renderQuestions(questions) {
  const list = document.getElementById('question-list');
  list.innerHTML = '';
  questions.forEach((q, i) => {
    const prio = Math.min(q.priority || (i+1), 5);
    const dots = Array.from({length:5}, (_,j) =>
      `<div class="prio-dot ${j < prio ? 'filled':''}"></div>`).join('');
    const type = q.type || 'behavioral';
    list.innerHTML += `
      <div class="question-card" onclick="toggleQ(this)">
        <div class="q-header">
          <span class="q-num">${String(i+1).padStart(2,'0')}</span>
          <span class="q-type-tag ${type}">${type}</span>
          <div class="q-priority">${dots}</div>
        </div>
        <div class="q-text">${q.question}</div>
        <div class="q-details">
          <div class="detail-row">
            <span class="detail-label">Follow-up probe</span>
            <span class="detail-value">${q.follow_up_probe || '—'}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Evaluation criteria</span>
            <span class="detail-value">${q.evaluation_criteria || '—'}</span>
          </div>
        </div>
      </div>`;
  });
}

function toggleQ(card) {
  const d = card.querySelector('.q-details');
  const open = d.classList.contains('open');
  document.querySelectorAll('.q-details').forEach(x => x.classList.remove('open'));
  document.querySelectorAll('.question-card').forEach(x => x.classList.remove('expanded'));
  if (!open) { d.classList.add('open'); card.classList.add('expanded'); }
}

function regenerateQuestions() {
  if (!S.setup) return;
  document.getElementById('question-list').innerHTML =
    '<div class="generating-state"><div class="spinner"></div><p>Regenerating…</p></div>';
  startInterview();
}

// ── Interview init ─────────────────────────────────────────────────────────────
async function beginVoiceInterview() {
  S.history    = [];
  S.scores     = [];
  S.currentQ   = 0;
  S.probeCount = 0;
  goToPanel('panel-interview');
  updateTurnDots();
  await doAgentTurn(true);
}

// ── Agent turn ─────────────────────────────────────────────────────────────────
async function doAgentTurn(isFirst = false) {
  const q = S.questions[S.currentQ];
  if (!q) { endInterview(); return; }

  setVoiceStatus('Interviewer is thinking…');
  document.getElementById('avatar').classList.add('speaking');

  try {
    const res  = await fetch('/api/agent-turn', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        history: S.history,
        current_question: q,
        probe_count: S.probeCount,
        is_first: isFirst,
      }),
    });
    const data = await res.json();

    S.history.push({ role: 'assistant', content: data.text });
    document.getElementById('question-display').textContent = `"${data.text}"`;
    setVoiceStatus('Interviewer speaking…');

    if (data.audio_url) {
      await playAudio(data.audio_url);
    }

    document.getElementById('avatar').classList.remove('speaking');

    if (data.action === 'next' && !isFirst) {
      S.currentQ++;
      S.probeCount = 0;
      updateTurnDots();
      if (S.currentQ >= S.questions.length) { endInterview(); return; }
    } else if (!isFirst) {
      S.probeCount++;
    }

    setVoiceStatus('Your turn — press mic to answer');
  } catch(e) {
    setVoiceStatus('Error — try again');
    document.getElementById('avatar').classList.remove('speaking');
  }
}

// ── Mic recording ──────────────────────────────────────────────────────────────
async function toggleMic() {
  if (S.recording) {
    S.mediaRecorder?.stop();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    S.audioChunks  = [];
    S.mediaRecorder = new MediaRecorder(stream);

    S.mediaRecorder.ondataavailable = e => S.audioChunks.push(e.data);
    S.mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      S.recording = false;
      document.getElementById('mic-btn').classList.remove('recording');
      document.getElementById('voice-stage').classList.remove('speaking');
      await handleUserAudio();
    };

    S.mediaRecorder.start();
    S.recording = true;
    document.getElementById('mic-btn').classList.add('recording');
    document.getElementById('voice-stage').classList.add('speaking');
    setVoiceStatus('Recording… press again to stop');
    document.getElementById('transcript-live').innerHTML =
      'Listening<span class="cursor"></span>';
  } catch(e) {
    alert('Microphone access denied. Please allow mic access and try again.');
  }
}

async function handleUserAudio() {
  setVoiceStatus('Transcribing…');
  document.getElementById('transcript-live').innerHTML =
    'Transcribing your answer<span class="cursor"></span>';

  const blob = new Blob(S.audioChunks, { type: 'audio/wav' });
  const form = new FormData();
  form.append('audio', blob, 'answer.wav');

  try {
    const txRes  = await fetch('/api/transcribe', { method: 'POST', body: form });
    if (!txRes.ok) {
      const err = await txRes.json();
      document.getElementById('transcript-live').textContent = err.detail || 'Too short — try again.';
      setVoiceStatus('Your turn — press mic to answer');
      return;
    }
    const txData = await txRes.json();
    const text   = txData.text?.trim();
    if (!text) { setVoiceStatus('No speech detected — try again'); return; }

    document.getElementById('transcript-live').textContent = `"${text}"`;
    S.history.push({ role: 'user', content: text });

    // Silent eval (fire and forget)
    evaluateSilently(text, S.questions[S.currentQ]);

    await doAgentTurn(false);
  } catch(e) {
    setVoiceStatus('Error — try again');
  }
}

// ── Silent evaluation ──────────────────────────────────────────────────────────
async function evaluateSilently(text, question) {
  try {
    const res  = await fetch('/api/evaluate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ user_text: text, question }),
    });
    const sc = await res.json();
    S.scores.push({ question: question.question, user_answer: text, scores: sc });
    updateLiveScores();
  } catch { /* silent */ }
}

function updateLiveScores() {
  if (!S.scores.length) return;
  const dims = { Clarity:'clarity', Relevance:'relevance', Structure:'technical_accuracy',
                 Depth:'communication_skills', Completeness:'completeness' };
  const ids  = ['score-clarity','score-relevance','score-structure','score-depth','score-completeness'];

  Object.entries(dims).forEach(([label, key], i) => {
    const vals = S.scores.map(s => s.scores?.[key] || 0).filter(Boolean);
    const avg  = vals.length ? (vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(1) : '—';
    const el   = document.getElementById(ids[i]);
    if (el) {
      el.querySelector('.score-val').textContent = avg;
      el.querySelector('.score-fill').style.width = (parseFloat(avg)||0)*10 + '%';
    }
  });
}

// ── Audio playback ─────────────────────────────────────────────────────────────
function playAudio(dataUri) {
  return new Promise(resolve => {
    const audio = new Audio(dataUri);
    audio.onended = resolve;
    audio.onerror = resolve;
    audio.play().catch(resolve);
  });
}

// ── End interview ──────────────────────────────────────────────────────────────
async function endInterview() {
  goToPanel('panel-feedback');
  document.getElementById('report-body').innerHTML =
    '<div class="generating-state"><div class="spinner"></div><p>Coach is writing your report…</p></div>';

  // Render score bubbles
  renderFeedbackScores();

  try {
    const res  = await fetch('/api/report', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ history: S.history, scores: S.scores, setup: S.setup || {} }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Backend error');
    renderReport(data.report);
  } catch(e) {
    document.getElementById('report-body').innerHTML =
      `<p style="color:var(--danger)">Report generation failed: ${e.message}</p>`;
  }
}

function renderFeedbackScores() {
  if (!S.scores.length) return;
  const dims = [
    ['Clarity','clarity'], ['Relevance','relevance'], ['Structure','technical_accuracy'],
    ['Depth','communication_skills'], ['Completeness','completeness'],
  ];
  const container = document.getElementById('score-summary');
  container.innerHTML = dims.map(([label, key]) => {
    const vals = S.scores.map(s => s.scores?.[key]||0).filter(Boolean);
    const avg  = vals.length ? (vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(1) : '—';
    return `<div class="score-bubble">
      <div class="score-num">${avg}</div>
      <div class="score-dim">${label}</div>
    </div>`;
  }).join('');
}

function renderReport(markdown) {
  const sections = [
    { title: '## ✅ Strengths',              cls: 'feedback-block', titleCls: 'fb-title strength' },
    { title: '## ⚠️ Gaps & Areas to Improve', cls: 'feedback-block', titleCls: 'fb-title gap' },
    { title: '## 🎯 3-Drill Practice Plan',   cls: 'feedback-block practice', titleCls: 'fb-title practice' },
  ];

  let html = '';
  sections.forEach(({ title, cls, titleCls }, i) => {
    const next    = sections[i+1]?.title;
    const pattern = new RegExp(escRe(title) + '([\\s\\S]*?)' + (next ? '(?=' + escRe(next) + ')' : '$'));
    const match   = markdown.match(pattern);
    const body    = match ? match[1].trim() : '';
    const dotCls  = titleCls.includes('strength') ? 'strength' : titleCls.includes('gap') ? 'gap' : 'practice';
    html += `<div class="${cls}">
      <div class="${titleCls}"><div class="dot"></div>${title.replace(/^##\s*/,'')}</div>
      <div class="fb-list">${formatBody(body, dotCls)}</div>
    </div>`;
  });

  document.getElementById('report-body').innerHTML = html || `<div style="padding:20px">${markdown}</div>`;
}

function formatBody(text, type) {
  return text.split('\n')
    .filter(l => l.trim())
    .map(l => {
      // Strip leading bullets/numbers
      let clean = l.replace(/^[-*\d.]+\s*/, '');
      // Convert markdown bold to HTML strong tags
      clean = clean.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      return `<div class="fb-item ${type}-item">${clean}</div>`;
    }).join('');
}

function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

// ── Helpers ────────────────────────────────────────────────────────────────────
function setStatus(txt) { const el = document.getElementById('status-badge'); if (el) el.textContent = txt; }
function setVoiceStatus(txt) { const el = document.getElementById('ai-status'); if (el) el.textContent = txt; }

function updateTurnDots() {
  const container = document.getElementById('turn-dots');
  container.innerHTML = S.questions.map((_, i) => {
    const cls = i < S.currentQ ? 'done' : i === S.currentQ ? 'current' : '';
    return `<div class="turn-dot ${cls}">${i+1}</div>`;
  }).join('');
}

// ── Chip / diff selectors ──────────────────────────────────────────────────────
function selectChip(btn) {
  document.querySelectorAll('#focus-group .chip').forEach(c => c.classList.remove('selected'));
  btn.classList.add('selected');
}
function selectDiff(btn) {
  document.querySelectorAll('.diff-btn').forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
}

function resetApp() {
  Object.assign(S, { setup:null, questions:[], history:[], scores:[], currentQ:0, probeCount:0 });
  goToPanel('panel-intake');
}
