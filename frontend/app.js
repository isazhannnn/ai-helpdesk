const messages = document.querySelector('#messages');
const form = document.querySelector('#chatForm');
const input = document.querySelector('#messageInput');
const sendButton = document.querySelector('#sendButton');
const voiceButton = document.querySelector('#voiceButton');
const speakButton = document.querySelector('#speakButton');
const authOverlay = document.querySelector('#authOverlay');
let conversationId = null;
let profile = null;
let isLogin = false;
let voiceRepliesEnabled = true;

const escapeHtml = text => text.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
const api = async (url, options = {}) => {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Something went wrong. Please try again.');
  return data;
};
const initials = name => name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();

function speak(text, language = 'ru') {
  if (!voiceRepliesEnabled || !('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
  const voice = new SpeechSynthesisUtterance(text);
  voice.lang = language === 'kk' ? 'kk-KZ' : 'ru-RU';
  voice.rate = 1.08;
  window.speechSynthesis.speak(voice);
}

function applyProfile(user) {
  profile = user;
  document.querySelector('#greeting').innerHTML = `Good to see you, ${escapeHtml(user.name.split(' ')[0])} <span>✦</span>`;
  ['profileName', 'settingsName'].forEach(id => document.querySelector(`#${id}`).textContent = user.name);
  ['profileEmail', 'settingsEmail'].forEach(id => document.querySelector(`#${id}`).textContent = user.email);
  ['profileInitials', 'settingsInitials'].forEach(id => document.querySelector(`#${id}`).textContent = initials(user.name));
}

function messageElement(text, role, messageId = null) {
  const element = document.createElement('div');
  element.className = `message ${role}`;
  const feedback = role === 'assistant' && messageId ? `<div class="feedback" data-message-id="${messageId}"><span>Was this helpful?</span><button data-helpful="true">✓ Yes</button><button data-helpful="false">No</button></div>` : '';
  element.innerHTML = `<span class="message-label">${role === 'assistant' ? '✦ Helia' : 'You'}</span><p>${escapeHtml(text).replace(/\n/g, '<br>')}</p>${feedback}`;
  return element;
}
function appendMessage(text, role, messageId = null) {
  messages.querySelector('.empty-state')?.remove(); const element = messageElement(text, role, messageId); messages.append(element); messages.scrollTop = messages.scrollHeight;
  element.querySelectorAll('[data-helpful]').forEach(button => button.addEventListener('click', async () => {
    const feedback = element.querySelector('.feedback');
    await api(`/api/responses/${messageId}/feedback`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ helpful: button.dataset.helpful === 'true' }) });
    feedback.innerHTML = '<span>Thanks — your feedback improves the quality view.</span>'; refreshAnalytics();
  }));
}
function setLoading(loading) {
  sendButton.disabled = loading; input.disabled = loading;
  if (loading) { const loader = document.createElement('div'); loader.className = 'message assistant loading-message'; loader.id = 'loader'; loader.innerHTML = '<span class="message-label">✦ Helia</span><p><i></i><i></i><i></i></p>'; messages.append(loader); messages.scrollTop = messages.scrollHeight; }
  else document.querySelector('#loader')?.remove();
}

async function refreshStats() {
  try { const stats = await api('/api/dashboard'); document.querySelector('#conversationCount').textContent = stats.conversations; document.querySelector('#resolvedCount').textContent = stats.ai_resolved; document.querySelector('#waitingCount').textContent = stats.waiting_for_agent; document.querySelector('#responseTime').textContent = `${stats.avg_response_seconds}s`; } catch (_) {}
}
async function refreshConversations() {
  try {
    const conversations = await api('/api/conversations'); const list = document.querySelector('#conversationList');
    list.innerHTML = conversations.length ? conversations.map(item => `<article class="conversation-item"><span>◌</span><div><b>${escapeHtml(item.title.slice(0, 85))}</b><small>Updated ${new Date(item.updated_at + 'Z').toLocaleString()}</small></div><button data-conversation="${item.id}">Open chat →</button></article>`).join('') : '<div class="empty-list">No conversations yet. Start your first one from the dashboard.</div>';
    list.querySelectorAll('[data-conversation]').forEach(button => button.addEventListener('click', async () => {
      conversationId = button.dataset.conversation;
      const history = await api(`/api/conversations/${conversationId}/messages`);
      messages.innerHTML = '';
      history.forEach(item => appendMessage(item.content, item.role, item.role === 'assistant' ? item.id : null));
      if (!history.length) resetChat();
      switchView('dashboard'); document.querySelector('#chat').scrollIntoView({ behavior: 'smooth' });
    }));
    const traceSelect = document.querySelector('#traceSelect');
    traceSelect.innerHTML = '<option value="">Select a conversation</option>' + conversations.map(item => `<option value="${item.id}">${escapeHtml(item.title.slice(0, 62))}</option>`).join('');
  } catch (_) {}
}
function speedLabel(latency) { return latency <= 5000 ? 'Smooth' : latency <= 12000 ? 'Acceptable' : 'Slow'; }
async function refreshAnalytics() {
  try {
    const data = await api('/api/analytics'); const summary = data.summary;
    document.querySelector('#analyticsSummary').innerHTML = `<article><span>AI replies</span><b>${summary.responses}</b><small>Completed responses</small></article><article><span>Average response</span><b>${(summary.avg_latency / 1000).toFixed(1)}s</b><small>${summary.smooth || 0} smooth (≤ 5 sec)</small></article><article><span>Helpful</span><b>${summary.helpful || 0}</b><small>${summary.awaiting_feedback || 0} awaiting feedback</small></article><article><span>Needs improvement</span><b>${summary.unhelpful || 0}</b><small>Based on customer feedback</small></article>`;
    document.querySelector('#responseAnalysisList').innerHTML = data.responses.length ? data.responses.map(item => `<article class="response-row"><div><b>${escapeHtml(item.content.slice(0, 100))}</b><small>${(item.latency_ms / 1000).toFixed(1)}s · ${speedLabel(item.latency_ms)}</small></div><span class="quality ${item.helpful === 1 ? 'good' : item.helpful === 0 ? 'bad' : 'pending'}">${item.helpful === 1 ? 'Helpful' : item.helpful === 0 ? 'Not helpful' : 'Awaiting feedback'}</span></article>`).join('') : '<p class="empty-list">No AI responses yet. Send a message to begin measuring quality.</p>';
  } catch (_) {}
}
async function loadTrace(conversation) {
  const empty = document.querySelector('#traceEmpty'); const result = document.querySelector('#traceResult');
  if (!conversation) { empty.hidden = false; result.hidden = true; return; }
  try {
    const trace = await api(`/api/conversations/${conversation}/trace`);
    empty.hidden = true; result.hidden = false;
    document.querySelector('#traceQuestion').textContent = trace.question;
    document.querySelector('#traceMeta').textContent = `${trace.message_count} message(s) in this private conversation · ${trace.status}`;
    const route = trace.route;
    document.querySelector('#routeDecision').innerHTML = route ? `<span class="route-pill">${escapeHtml(route.scenario_id)}</span><div><b>${escapeHtml(route.scenario_id)} · ${route.confidence}% confidence</b><small>${escapeHtml(route.reason)}</small><small>Router: ${route.routing_latency_ms} ms · ${escapeHtml(route.language)}${route.topic_switched ? ' · topic switched' : ''}</small></div>` : '<small>No routing decision has been recorded yet.</small>';
    document.querySelector('#traceExplanation').textContent = trace.explanation;
    document.querySelector('#traceFlow').innerHTML = trace.steps.map((step, index) => `<article class="trace-step ${step.kind}"><span class="trace-node">${index + 1}</span><div><small>STEP ${index + 1}</small><h3>${escapeHtml(step.title)}</h3><p>${escapeHtml(step.detail)}</p></div><b>✓</b></article>`).join('');
  } catch (error) { empty.hidden = false; result.hidden = true; }
}
async function createConversation() { const data = await api('/api/conversations', { method: 'POST' }); conversationId = data.conversation_id; }
function resetChat(copy = 'New conversation, fresh context') { messages.innerHTML = `<div class="empty-state"><div>✦</div><h3>${copy}</h3><p>Your assistant knows your account profile and is ready to help.</p></div>`; }

form.addEventListener('submit', async event => {
  event.preventDefault(); const text = input.value.trim(); if (!text) return;
  appendMessage(text, 'user'); input.value = ''; input.style.height = 'auto'; setLoading(true);
  try { const data = await api('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text, conversation_id: conversationId }) }); conversationId = data.conversation_id; appendMessage(data.reply, 'assistant', data.assistant_message_id); speak(data.reply, data.route.reply_language); refreshStats(); refreshConversations(); refreshAnalytics(); }
  catch (error) { appendMessage(error.message, 'assistant'); }
  finally { setLoading(false); input.focus(); }
});

document.querySelectorAll('#newConversation, #newConversationList').forEach(button => button.addEventListener('click', async () => { try { await createConversation(); resetChat(); switchView('dashboard'); refreshStats(); input.focus(); } catch (error) { appendMessage(error.message, 'assistant'); } }));
document.querySelector('#startChat').addEventListener('click', () => document.querySelector('#chat').scrollIntoView({ behavior: 'smooth', block: 'start' }));
document.querySelector('#menuToggle').addEventListener('click', () => document.querySelector('#sidebar').classList.toggle('open'));
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; });

let recorder;
let recordingStream;
let audioChunks = [];
async function toggleRecording() {
  if (recorder?.state === 'recording') { recorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    appendMessage('Voice recording is not supported by this browser. Please use a recent version of Chrome or Edge.', 'assistant'); return;
  }
  try {
    recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : undefined;
    recorder = new MediaRecorder(recordingStream, mimeType ? { mimeType } : undefined); audioChunks = [];
    recorder.ondataavailable = event => { if (event.data.size) audioChunks.push(event.data); };
    recorder.onstart = () => { voiceButton.classList.add('recording'); voiceButton.title = 'Recording… click to finish'; };
    recorder.onstop = async () => {
      voiceButton.classList.remove('recording'); voiceButton.disabled = true; voiceButton.title = 'Transcribing…';
      recordingStream.getTracks().forEach(track => track.stop());
      try {
        const blob = new Blob(audioChunks, { type: recorder.mimeType || 'audio/webm' });
        const formData = new FormData(); formData.append('audio', blob, 'voice-message.webm');
        const data = await api('/api/transcribe', { method: 'POST', body: formData });
        input.value = data.text; input.dispatchEvent(new Event('input')); form.requestSubmit();
      } catch (error) { appendMessage(error.message || 'Voice recognition failed. Please try again.', 'assistant'); }
      finally { voiceButton.disabled = false; voiceButton.title = 'Speak your message'; }
    };
    recorder.start();
  } catch (_) { appendMessage('Microphone access was denied. Allow it in the browser and try again.', 'assistant'); }
}
voiceButton.addEventListener('click', toggleRecording);
speakButton.addEventListener('click', () => { voiceRepliesEnabled = !voiceRepliesEnabled; speakButton.classList.toggle('active', voiceRepliesEnabled); speakButton.title = voiceRepliesEnabled ? 'AI voice is on' : 'AI voice is off'; if (!voiceRepliesEnabled) window.speechSynthesis?.cancel(); });

function switchView(view) {
  document.querySelectorAll('.view').forEach(section => section.classList.toggle('active', section.id === `${view}-view`));
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  document.querySelector('#sidebar').classList.remove('open');
  if (view === 'chats') refreshConversations();
  if (view === 'analytics') refreshAnalytics();
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
document.querySelector('#traceSelect').addEventListener('change', event => loadTrace(event.target.value));

function showAuth(login) {
  isLogin = login; document.querySelector('#authTitle').textContent = login ? 'Welcome back' : 'Create your workspace';
  document.querySelector('#authCopy').textContent = login ? 'Sign in to continue your support conversations.' : 'A few details are all we need to personalize your AI support assistant.';
  document.querySelector('#nameField').hidden = login; document.querySelector('#authName').required = !login;
  document.querySelector('.auth-submit').innerHTML = `${login ? 'Sign in' : 'Create account'} <span>→</span>`;
  document.querySelector('#authSwitch').textContent = login ? 'New here? Create an account' : 'Already have an account? Sign in';
  document.querySelector('#authError').textContent = '';
}
document.querySelector('#authSwitch').addEventListener('click', () => showAuth(!isLogin));
document.querySelector('#authForm').addEventListener('submit', async event => {
  event.preventDefault(); const error = document.querySelector('#authError'); error.textContent = '';
  const payload = { email: document.querySelector('#authEmail').value, password: document.querySelector('#authPassword').value };
  if (!isLogin) payload.name = document.querySelector('#authName').value;
  try { const user = await api(`/api/auth/${isLogin ? 'login' : 'register'}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); applyProfile(user); authOverlay.classList.add('hidden'); refreshStats(); refreshConversations(); refreshAnalytics(); }
  catch (err) { error.textContent = err.message; }
});
document.querySelector('#logoutButton').addEventListener('click', async () => { await api('/api/auth/logout', { method: 'POST' }); profile = null; conversationId = null; resetChat('Sign in to start a private conversation'); authOverlay.classList.remove('hidden'); showAuth(false); switchView('dashboard'); });

(async () => { try { applyProfile(await api('/api/me')); authOverlay.classList.add('hidden'); refreshStats(); refreshConversations(); refreshAnalytics(); } catch (_) { showAuth(false); } })();
