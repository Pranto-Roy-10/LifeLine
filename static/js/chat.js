(function(){
  const socket = io();
  const messagesEl = document.getElementById('messages');
  const input = document.getElementById('message_input');
  const sendBtn = document.getElementById('send_btn');
  const typingEl = document.getElementById('typing');
  const translateSelect = document.getElementById('translate_to');
  const autoTranslateChk = document.getElementById('auto_translate');

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
    const container = messagesEl.parentElement;
    try{
      return (container.scrollHeight - container.scrollTop - container.clientHeight) < threshold;
    }catch(e){return true}
  }

  function scrollBottom(force=false){
    const container = messagesEl.parentElement;
    // Only auto-scroll when user is near bottom unless forced
    if(force || isNearBottom()){
      // smooth scroll to bottom
      try{ 
        container.scrollTo({
          top: container.scrollHeight,
          behavior: force ? 'auto' : 'smooth'
        });
      }catch(e){ 
        container.scrollTop = container.scrollHeight; 
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
    console.log('appendMessage called with:', {
      id: m.id,
      text: m.text?.substring(0, 50),
      has_file_data: !!m.file_data,
      file_name: m.file_name,
      is_image: m.is_image
    });
    
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

    // timestamp: show below the bubble
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
        console.log('Found temp message, updating ID');
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
    // For receivers or if temp not found, append the message
    console.log('Appending new message from broadcast');
    appendMessage(m);
    // if message is from other user, ack delivered
    if(m.sender_id !== CURRENT_USER_ID){
      socket.emit('message_delivered', {message_id: m.id});
      // auto-translate if requested
      if(translateSelect && autoTranslateChk){
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

  // typing indicator with animation
  let typingTimeout = null;
  input.addEventListener('input', ()=>{
    socket.emit('typing', {conversation_id: CONVERSATION_ID});
    if(typingTimeout) clearTimeout(typingTimeout);
    typingTimeout = setTimeout(()=>{
      socket.emit('stop_typing', {conversation_id: CONVERSATION_ID});
    }, 1200);
  });

  socket.on('typing', (d)=>{
    typingEl.textContent = 'Typing...';
    typingEl.style.display = 'flex';
  });
  socket.on('stop_typing', (d)=>{
    typingEl.textContent = '';
    typingEl.style.display = 'none';
  });

  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keydown', (e)=>{ if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendMessage(); } });

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

  // Ensure we scroll smoothly on resize
  window.addEventListener('resize', ()=>{ setTimeout(()=> scrollBottom(false), 50); });

  // Add smooth entrance animation for input
  input.addEventListener('focus', ()=>{
    input.parentElement.style.transform = 'scale(1.01)';
  });
  
  input.addEventListener('blur', ()=>{
    input.parentElement.style.transform = 'scale(1)';
  });

  // Emoji Picker Functionality
  const emojiToggleBtn = document.getElementById('emoji-toggle-btn');
  const emojiPicker = document.querySelector('.emoji-picker');
  const emojiCategories = document.querySelectorAll('.emoji-category');
  const emojiGrid = document.querySelector('.emoji-grid');

  // Comprehensive emoji database by category
  const emojis = {
    smileys: ['😀','😃','😄','😁','😆','😅','🤣','😂','🙂','🙃','😉','😊','😇','🥰','😍','🤩','😘','😗','😚','😙','🥲','😋','😛','😜','🤪','😝','🤑','🤗','🤭','🤫','🤔','🤐','🤨','😐','😑','😶','😏','😒','🙄','😬','🤥','😌','😔','😪','🤤','😴','😷','🤒','🤕','🤢','🤮','🤧','🥵','🥶','🥴','😵','🤯','🤠','🥳','🥸','😎','🤓','🧐','😕','😟','🙁','☹️','😮','😯','😲','😳','🥺','😦','😧','😨','😰','😥','😢','😭','😱','😖','😣','😞','😓','😩','😫','🥱'],
    gestures: ['👋','🤚','🖐️','✋','🖖','👌','🤌','🤏','✌️','🤞','🤟','🤘','🤙','👈','👉','👆','🖕','👇','☝️','👍','👎','✊','👊','🤛','🤜','👏','🙌','👐','🤲','🤝','🙏','✍️','💪','🦾','🦿','🦵','🦶','👂','🦻','👃','🧠','🫀','🫁','🦷','🦴','👀','👁️','👅','👄','💋','🩸'],
    hearts: ['❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❤️‍🔥','❤️‍🩹','💕','💞','💓','💗','💖','💘','💝','💟','💌','💢','💥','💫','💦','💨','🕊️','🦋','🌹','🥀','🌺','🌸','🌼','🌻','🌷','⚡','🔥','✨','🌟','⭐','🌠','💯','🎉','🎊','🎈','🎁','🎀','🏆','🥇','🥈','🥉'],
    objects: ['⚽','🏀','🏈','⚾','🥎','🎾','🏐','🏉','🥏','🎱','🪀','🏓','🏸','🏒','🏑','🥍','🏏','🥅','⛳','🪁','🏹','🎣','🤿','🥊','🥋','🎽','🛹','🛼','🛷','⛸️','🥌','🎿','⛷️','🏂','🪂','🏋️','🤼','🤸','🤺','🤾','🏌️','🏇','🧘','🏄','🏊','🤽','🚣','🧗','🚵','🚴','🏎️','🏍️','🛺','🚲','🛴','🛵','🚗','🚕','🚙','🚌','🚎','🏎️','🚓','🚑','🚒','🚐','🛻','🚚','🚛','🚜','🦯','🦽','🦼','🛴','🚏','⛽','🚨','🚥','🚦','🛑','🚧','⚓','⛵','🛶','🚤','🛳️','⛴️','🛥️','🚢','✈️','🛩️','🛫','🛬','🪂','💺','🚁','🚟','🚠','🚡','🛰️','🚀','🛸']
  };

  // Current active category
  let activeCategory = 'smileys';

  // Populate emoji grid
  function populateEmojiGrid(category) {
    emojiGrid.innerHTML = '';
    const categoryEmojis = emojis[category] || emojis.smileys;
    
    categoryEmojis.forEach(emoji => {
      const emojiItem = document.createElement('span');
      emojiItem.className = 'emoji-item';
      emojiItem.textContent = emoji;
      emojiItem.style.animation = 'popIn 0.2s ease-out';
      emojiItem.addEventListener('click', () => {
        insertEmoji(emoji);
      });
      emojiGrid.appendChild(emojiItem);
    });
  }

  // Insert emoji into input
  function insertEmoji(emoji) {
    const startPos = input.selectionStart;
    const endPos = input.selectionEnd;
    const currentText = input.value;
    
    // Insert emoji at cursor position
    input.value = currentText.substring(0, startPos) + emoji + currentText.substring(endPos);
    
    // Move cursor after emoji
    input.selectionStart = input.selectionEnd = startPos + emoji.length;
    
    // Focus input and add pulse effect
    input.focus();
    input.style.animation = 'pulse 0.3s ease-out';
    setTimeout(() => {
      input.style.animation = '';
    }, 300);
  }

  // Toggle emoji picker
  emojiToggleBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    emojiPicker.classList.toggle('show');
    
    // Populate grid on first open
    if (emojiPicker.classList.contains('show') && emojiGrid.children.length === 0) {
      populateEmojiGrid(activeCategory);
    }
  });

  // Category switching
  emojiCategories.forEach(category => {
    category.addEventListener('click', () => {
      // Update active category
      emojiCategories.forEach(c => c.classList.remove('active'));
      category.classList.add('active');
      
      // Get category name from data attribute or default
      activeCategory = category.dataset.category || 'smileys';
      
      // Repopulate grid
      populateEmojiGrid(activeCategory);
    });
  });

  // Set first category as active
  if (emojiCategories.length > 0) {
    emojiCategories[0].classList.add('active');
  }

  // Close emoji picker when clicking outside
  document.addEventListener('click', (e) => {
    if (!emojiPicker.contains(e.target) && e.target !== emojiToggleBtn) {
      emojiPicker.classList.remove('show');
    }
  });

  // Prevent picker from closing when clicking inside
  emojiPicker.addEventListener('click', (e) => {
    e.stopPropagation();
  });

})();
