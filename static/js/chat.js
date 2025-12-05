(function(){
  const socket = io();
  const messagesEl = document.getElementById('messages');
  const input = document.getElementById('message_input');
  const sendBtn = document.getElementById('send_btn');
  const typingEl = document.getElementById('typing');
  const translateSelect = document.getElementById('translate_to');
  const autoTranslateChk = document.getElementById('auto_translate');

  // Keep input bar in normal flow inside chat-window (no fixed overlay)
  const chatWindow = document.querySelector('.chat-window');
  const inputBar = document.querySelector('.chat-input-bar');

  console.log('Chat.js loaded. CURRENT_USER_ID:', CURRENT_USER_ID, 'CONVERSATION_ID:', CONVERSATION_ID);

  socket.on('connect', () => {
    console.log('Socket connected:', socket.id);
    console.log('Emitting join to conversation', CONVERSATION_ID);
    socket.emit('join', {conversation_id: CONVERSATION_ID});
  });

  socket.on('disconnect', () => {
    console.log('Socket disconnected');
  });

  socket.on('connect_error', (error) => {
    console.error('Socket connection error:', error);
  });

  function isNearBottom(threshold = 150){
    try{
      return (messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight) < threshold;
    }catch(e){return true}
  }

  function scrollBottom(force=false){
    // Only auto-scroll when user is near bottom unless forced
    if(force || isNearBottom()){
      // smooth scroll into view of last message
      const last = messagesEl.lastElementChild;
      if(last){
        try{ last.scrollIntoView({behavior: 'auto', block: 'end'}); }catch(e){ messagesEl.scrollTop = messagesEl.scrollHeight; }
      }else{
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }
  }

  function formatTime(ts){
    try{
      // Convert to Bangladesh time (UTC+6)
      const date = new Date(ts*1000);
      const options = { timeZone: 'Asia/Dhaka', hour: '2-digit', minute: '2-digit', hour12: true };
      const timeStr = date.toLocaleTimeString('en-US', options);
      return timeStr;
    }catch(e){return ''}
  }

  function appendMessage(m){
    // m: {id, conversation_id, sender_id, text, created_at, delivered, read}
    const div = document.createElement('div');
    const me = (m.sender_id === CURRENT_USER_ID);
    div.className = 'chat-message ' + (me ? 'sent' : 'received');
    div.dataset.messageId = m.id;

    // avatar for received messages
    if(!me){
      const av = document.createElement('div');
      av.className = 'msg-avatar';
      // show initial if available
      const initial = (m.sender_name && m.sender_name.length>0) ? m.sender_name[0].toUpperCase() : 'U';
      av.textContent = initial;
      div.appendChild(av);
    }

    const text = document.createElement('div');
    text.className = 'text';
    text.textContent = m.text;
    div.appendChild(text);

    // timestamp: show below the bubble (bottom-right)
    const meta = document.createElement('div');
    meta.className = 'meta';
    try{
      meta.textContent = formatTime(m.created_at || Math.floor(Date.now()/1000));
    }catch(e){ meta.textContent = ''; }
    div.appendChild(meta);

    messagesEl.appendChild(div);
    // allow layout to settle before scrolling
    setTimeout(()=> scrollBottom(false), 10);
  }

  // Preload messages if present
  if(typeof MESSAGES !== 'undefined' && Array.isArray(MESSAGES)){
    console.log('Preloading', MESSAGES.length, 'messages');
    MESSAGES.forEach(m => appendMessage(m));
    // ensure we start scrolled to bottom on load
    setTimeout(()=> scrollBottom(true), 50);
  }

  socket.on('new_message', (m) => {
    console.log('Received new_message:', m);
    // If this message corresponds to a temp (optimistic) message, replace it instead of appending
    if(m.temp_id){
      const tempEl = messagesEl.querySelector('[data-message-id="'+m.temp_id+'"]');
      if(tempEl){
        // update dataset id
        tempEl.dataset.messageId = m.id;
        // update timestamp/meta
        const metaEl = tempEl.querySelector('.meta');
        if(metaEl){
          try{ metaEl.textContent = formatTime(m.created_at || Math.floor(Date.now()/1000)); }catch(e){}
        }
        // ensure data is consistent
        return;
      }
    }
    // otherwise append normally
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
    
    // Optimistic UI: show message immediately on screen
    const tempMsg = {
      id: 'temp-' + Date.now(),
      conversation_id: CONVERSATION_ID,
      sender_id: CURRENT_USER_ID,
      text: txt,
      created_at: Math.floor(Date.now() / 1000),
      delivered: false,
      read: false
    };
    appendMessage(tempMsg);
    // Immediately scroll to show the optimistic message (user expects to see their sent text)
    setTimeout(()=> scrollBottom(true), 30);
    
    // Send to server
    console.log('Sending message:', txt, 'temp_id:', tempMsg.id);
    socket.emit('send_message', {conversation_id: CONVERSATION_ID, text: txt, temp_id: tempMsg.id}, (ack) => {
      console.log('Message ack:', ack);
      // in case server ack arrives before broadcast, update temp element
      if(ack && ack.id){
        const tempEl = messagesEl.querySelector('[data-message-id="'+ack.temp_id+'"]');
        if(tempEl){
          tempEl.dataset.messageId = ack.id;
          // timestamps removed — nothing to update
        }
      }
    });
    
    input.value = '';
    socket.emit('stop_typing', {conversation_id: CONVERSATION_ID});
  }

  // CSS now handles padding-bottom for messages area (120px for absolute input)
  // Just ensure we scroll smoothly on resize
  window.addEventListener('resize', ()=>{ setTimeout(()=> scrollBottom(false), 50); });

})();
