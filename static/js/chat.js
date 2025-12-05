(function(){
  const socket = io();
  const messagesEl = document.getElementById('messages');
  const input = document.getElementById('message_input');
  const sendBtn = document.getElementById('send_btn');
  const typingEl = document.getElementById('typing');
  const translateSelect = document.getElementById('translate_to');
  const autoTranslateChk = document.getElementById('auto_translate');

  function scrollBottom(){ messagesEl.scrollTop = messagesEl.scrollHeight; }

  function formatTime(ts){
    try{
      return new Date(ts*1000).toLocaleTimeString();
    }catch(e){return ''}
  }

  function appendMessage(m){
    // m: {id, conversation_id, sender_id, text, created_at, delivered, read}
    const div = document.createElement('div');
    const me = (m.sender_id === CURRENT_USER_ID);
    div.className = 'chat-message ' + (me ? 'sent' : 'received');
    div.dataset.messageId = m.id;

    const text = document.createElement('div');
    text.className = 'text';
    text.textContent = m.text;
    div.appendChild(text);

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = formatTime(m.created_at);

    if(me){
      const status = document.createElement('span');
      status.className = 'status';
      status.textContent = m.read ? 'Read' : (m.delivered ? 'Delivered' : 'Sent');
      meta.appendChild(status);
    }
    div.appendChild(meta);

    messagesEl.appendChild(div);
    scrollBottom();
  }

  // Preload messages if present
  if(typeof MESSAGES !== 'undefined' && Array.isArray(MESSAGES)){
    MESSAGES.forEach(m => appendMessage(m));
  }

  socket.on('connect', () => {
    socket.emit('join', {conversation_id: CONVERSATION_ID});
  });

  socket.on('new_message', (m) => {
    appendMessage(m);
    // if message is from other user, ack delivered
    if(m.sender_id !== CURRENT_USER_ID){
      socket.emit('message_delivered', {message_id: m.id});
      // auto-translate if requested
      const tgt = translateSelect.value;
      if(autoTranslateChk.checked && tgt){
        fetch('/api/translate', {method:'POST',headers:{'Content-Type':'application/json'},body: JSON.stringify({text: m.text, target: tgt})})
          .then(r=>r.json()).then(j=>{
            if(j.translated){
                    const tr = document.createElement('div');
                    tr.className = 'translation';
                    tr.textContent = 'Translated: ' + j.translated;
              // append under last message
              const last = messagesEl.lastChild;
              if(last) last.appendChild(tr);
              scrollBottom();
            }
          }).catch(()=>{});
      }
    }
  });

  socket.on('delivered', (d) => {
    const mid = d.message_id;
    const el = messagesEl.querySelector('[data-message-id="'+mid+'"]');
    if(el){
      const sts = el.querySelector('.status');
      if(sts) sts.textContent = 'Delivered';
    }
  });

  socket.on('read', (d) => {
    const mid = d.message_id;
    const el = messagesEl.querySelector('[data-message-id="'+mid+'"]');
    if(el){
      const sts = el.querySelector('.status');
      if(sts) sts.textContent = 'Read';
    }
  });

  // typing indicator
  let typingTimeout = null;
  input.addEventListener('input', ()=>{
    socket.emit('typing', {conversation_id: CONVERSATION_ID});
    if(typingTimeout) clearTimeout(typingTimeout);
    typingTimeout = setTimeout(()=>{
      socket.emit('stop_typing', {conversation_id: CONVERSATION_ID});
    }, 1200);
  });

  socket.on('typing', (d)=>{
    typingEl.textContent = 'User is typing...';
  });
  socket.on('stop_typing', (d)=>{
    typingEl.textContent = '';
  });

  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keydown', (e)=>{ if(e.key === 'Enter'){ e.preventDefault(); sendMessage(); } });

  function sendMessage(){
    const txt = input.value.trim();
    if(!txt) return;
    socket.emit('send_message', {conversation_id: CONVERSATION_ID, text: txt});
    input.value = '';
    socket.emit('stop_typing', {conversation_id: CONVERSATION_ID});
  }

})();
