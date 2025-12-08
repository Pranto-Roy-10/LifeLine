(function(){
  const socket = io();
  console.log('chat_index.js loaded');

  socket.on('connect', ()=>{
    console.log('Socket connected on index page', socket.id);
  });

  socket.on('new_message', (m) => {
    try{
      // m: message payload from server. Expect conversation_id, sender_id, text
      const convId = m.conversation_id;
      if(!convId) return;
      const convEl = document.querySelector('[data-conv-id="'+convId+'"]');
      if(!convEl) return;

      // If the message is from the other user, show unread indicator
      const fromOther = (typeof CURRENT_USER_ID !== 'undefined' && CURRENT_USER_ID !== null) ? (m.sender_id != CURRENT_USER_ID) : true;
      if(fromOther){
        // update/create badge
        let badge = convEl.querySelector('.unread-badge');
        if(!badge){
          badge = document.createElement('div');
          badge.className = 'bg-red-600 text-white text-xs px-2 py-1 rounded-full font-semibold flex-shrink-0 unread-badge';
          // insert before the timestamp element
          const rightGroup = convEl.querySelector('.flex.items-center.gap-4');
          if(rightGroup){
            rightGroup.insertBefore(badge, rightGroup.firstChild);
          } else {
            convEl.insertBefore(badge, convEl.firstChild);
          }
          badge.textContent = 'New 1';
        } else {
          // increment number if present
          const parts = badge.textContent.trim().split(/\s+/);
          const num = parts.length>1 ? parseInt(parts[1]) || 1 : 1;
          badge.textContent = 'New ' + (num + 1);
        }

        // bold preview and update preview text
        const preview = convEl.querySelector('a .text-[12px]');
        if(preview){
          preview.classList.remove('text-slate-400');
          preview.classList.add('font-bold','text-white');
          if(m.text) preview.textContent = m.text;
        }
      }
    }catch(e){ console.error('chat_index new_message handler error', e); }
  });

  // When messages are marked read (by someone opening the conversation), remove or decrement badge
  socket.on('read', (d) => {
    try{
      const convId = d.conversation_id || null;
      if(!convId) return;
      const convEl = document.querySelector('[data-conv-id="'+convId+'"]');
      if(!convEl) return;
      const badge = convEl.querySelector('.unread-badge');
      if(!badge) return;
      // If badge shows a number, decrement; otherwise remove
      const parts = badge.textContent.trim().split(/\s+/);
      const num = parts.length>1 ? parseInt(parts[1]) || 0 : 0;
      if(num <= 1){ badge.remove();
        // also reset preview style
        const preview = convEl.querySelector('a .text-[12px]');
        if(preview){ preview.classList.remove('font-bold','text-white'); preview.classList.add('text-slate-400'); }
      } else { badge.textContent = 'New ' + (num - 1); }
    }catch(e){ console.error('chat_index read handler error', e); }
  });

  // Optimistically clear badge when user opens a conversation
  function attachClickHandlers(){
    document.querySelectorAll('[data-conv-id] a').forEach(a => {
      // avoid adding multiple listeners
      if(a.dataset._hasClick) return;
      a.dataset._hasClick = '1';
      a.addEventListener('click', (ev) => {
        try{
          const convEl = a.closest('[data-conv-id]');
          if(!convEl) return;
          const convId = convEl.getAttribute('data-conv-id');
          // remove badge and reset preview styling optimistically
          const badge = convEl.querySelector('.unread-badge');
          if(badge) badge.remove();
          const preview = convEl.querySelector('a .text-[12px]');
          if(preview){ preview.classList.remove('font-bold','text-white'); preview.classList.add('text-slate-400'); }

          // fire-and-forget POST to mark conversation read on server
          if(convId){
            fetch('/api/conversations/' + convId + '/mark_read', { method: 'POST', credentials: 'same-origin' }).catch(()=>{});
          }
        }catch(e){ console.error('click handler failed', e); }
        // allow navigation to continue
      });
    });
  }

  // attach handlers now and also observe for future changes
  attachClickHandlers();
  // simple observer to re-attach if DOM changes (e.g., badges inserted)
  const obs = new MutationObserver((m)=>{ attachClickHandlers(); });
  obs.observe(document.body, { childList: true, subtree: true });

})();
