const messages = document.querySelector('#messages');
const form = document.querySelector('#chatForm');
const input = document.querySelector('#messageInput');
const sendButton = document.querySelector('#sendButton');
let conversationId = null;

const escapeHtml = (text) => text.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);

function messageElement(text, role) {
  const element = document.createElement('div');
  element.className = `message ${role}`;
  element.innerHTML = `<span class="message-label">${role === 'assistant' ? '✦ Helia' : 'You'}</span><p>${escapeHtml(text).replace(/\n/g, '<br>')}</p>`;
  return element;
}

function appendMessage(text, role) {
  messages.querySelector('.empty-state')?.remove();
  messages.append(messageElement(text, role));
  messages.scrollTop = messages.scrollHeight;
}

function setLoading(isLoading) {
  sendButton.disabled = isLoading;
  input.disabled = isLoading;
  if (isLoading) {
    const loader = document.createElement('div');
    loader.className = 'message assistant loading-message';
    loader.id = 'loader';
    loader.innerHTML = '<span class="message-label">✦ Helia</span><p><i></i><i></i><i></i></p>';
    messages.append(loader); messages.scrollTop = messages.scrollHeight;
  } else document.querySelector('#loader')?.remove();
}

async function refreshStats() {
  try {
    const response = await fetch('/api/dashboard');
    if (!response.ok) return;
    const stats = await response.json();
    document.querySelector('#conversationCount').textContent = stats.conversations;
    document.querySelector('#resolvedCount').textContent = stats.ai_resolved;
    document.querySelector('#waitingCount').textContent = stats.waiting_for_agent;
    document.querySelector('#responseTime').textContent = `${stats.avg_response_seconds}s`;
  } catch (_) { /* Dashboard stays usable while offline. */ }
}

async function createConversation() {
  const response = await fetch('/api/conversations', { method: 'POST' });
  if (!response.ok) throw new Error('Could not create a new conversation.');
  const data = await response.json(); conversationId = data.conversation_id;
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  appendMessage(text, 'user'); input.value = ''; input.style.height = 'auto'; setLoading(true);
  try {
    const response = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text, conversation_id: conversationId }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Something went wrong.');
    conversationId = data.conversation_id; appendMessage(data.reply, 'assistant'); refreshStats();
  } catch (error) { appendMessage(error.message || 'Unable to send your message. Please try again.', 'assistant'); }
  finally { setLoading(false); input.focus(); }
});

document.querySelector('#newConversation').addEventListener('click', async () => {
  try { await createConversation(); messages.innerHTML = '<div class="empty-state"><div>✦</div><h3>New conversation, fresh context</h3><p>Your assistant is ready. What can it help with?</p></div>'; refreshStats(); input.focus(); } catch (error) { appendMessage('Could not create a new conversation. Please try again.', 'assistant'); }
});
document.querySelector('#startChat').addEventListener('click', () => document.querySelector('#chat').scrollIntoView({ behavior: 'smooth', block: 'start' }));
document.querySelector('#menuToggle').addEventListener('click', () => document.querySelector('#sidebar').classList.toggle('open'));
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; });
refreshStats();
