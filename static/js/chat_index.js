(function(){
  // Parallax effect for home page backgrounds
  if (window.location.pathname === "/" || window.location.pathname === "/home") {
    document.addEventListener('mousemove', function(e) {
      const x = (e.clientX / window.innerWidth - 0.5) * 30;
      const y = (e.clientY / window.innerHeight - 0.5) * 30;
      const bg1 = document.getElementById('parallax-bg-1');
      const bg2 = document.getElementById('parallax-bg-2');
      if(bg1) bg1.style.transform = `translate(${x}px, ${y}px)`;
      if(bg2) bg2.style.transform = `translate(${-x}px, ${-y}px)`;
    });

    // Scroll reveal for sections
    function revealOnScroll() {
      const reveals = document.querySelectorAll('.scroll-reveal');
      for (const el of reveals) {
        const rect = el.getBoundingClientRect();
        if (rect.top < window.innerHeight - 80) {
          el.classList.add('visible');
        }
      }
    }
    window.addEventListener('scroll', revealOnScroll);
    window.addEventListener('DOMContentLoaded', revealOnScroll);
    revealOnScroll();
  }
(function(){
  // Parallax effect for home page backgrounds
  if (window.location.pathname === "/" || window.location.pathname === "/home") {
    document.addEventListener('mousemove', function(e) {
      const x = (e.clientX / window.innerWidth - 0.5) * 30;
      const y = (e.clientY / window.innerHeight - 0.5) * 30;
      const bg1 = document.getElementById('parallax-bg-1');
      const bg2 = document.getElementById('parallax-bg-2');
      if(bg1) bg1.style.transform = `translate(${x}px, ${y}px)`;
      if(bg2) bg2.style.transform = `translate(${-x}px, ${-y}px)`;
    });
  }
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
        let badge = convEl.querySelector('.unread-indicator-badge');
        let unreadDot = convEl.querySelector('.unread-dot');
        
        if(!badge){
          // Create new badge next to name
          badge = document.createElement('span');
          badge.className = 'unread-indicator-badge';
          badge.textContent = '1';
          const conversationName = convEl.querySelector('.conversation-name');
          if(conversationName){
            conversationName.appendChild(badge);
          }
        } else {
          // increment number if present
          const num = parseInt(badge.textContent) || 1;
          badge.textContent = (num + 1).toString();
        }
        
        // Add or ensure unread dot on avatar
        if(!unreadDot){
          unreadDot = document.createElement('span');
          unreadDot.className = 'unread-dot';
          const avatar = convEl.querySelector('.conversation-avatar');
          if(avatar){
            avatar.appendChild(unreadDot);
          }
        }

        // Update preview text and style
        const preview = convEl.querySelector('.conversation-preview');
        if(preview){
          preview.classList.add('unread');
          if(m.text) preview.textContent = m.text;
        }

        // Update timestamp
        const timeEl = convEl.querySelector('.conversation-time');
        if(timeEl && m.created_at){
          const now = Math.floor(Date.now() / 1000);
          const diff = now - m.created_at;
          if(diff < 60) {
            timeEl.textContent = 'Just now';
          } else if(diff < 3600) {
            timeEl.textContent = Math.floor(diff / 60) + 'm ago';
          } else if(diff < 86400) {
            timeEl.textContent = Math.floor(diff / 3600) + 'h ago';
          } else {
            timeEl.textContent = Math.floor(diff / 86400) + 'd ago';
          }
        }

        // Move conversation to top with smooth animation
        const conversationsGrid = document.querySelector('.conversations-grid');
        if(conversationsGrid && conversationsGrid.firstChild !== convEl){
          // Add slide-out animation
          convEl.style.animation = 'slideOutRight 0.3s ease-out';
          
          setTimeout(() => {
            // Move to top
            conversationsGrid.insertBefore(convEl, conversationsGrid.firstChild);
            
            // Add slide-in animation from left
            convEl.style.animation = 'slideInFromTop 0.4s ease-out';
            
            // Add highlight effect
            convEl.style.backgroundColor = 'rgba(16, 185, 129, 0.15)';
            setTimeout(() => {
              convEl.style.transition = 'background-color 1s ease';
              convEl.style.backgroundColor = 'rgba(15, 23, 42, 0.8)';
            }, 500);
          }, 300);
        } else {
          // Just add pulse animation if already at top
          convEl.style.animation = 'none';
          setTimeout(() => {
            convEl.style.animation = 'pulse 0.5s ease-out';
          }, 10);
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
      
      const badge = convEl.querySelector('.unread-indicator-badge');
      const unreadDot = convEl.querySelector('.unread-dot');
      
      if(!badge) return;
      
      // Parse the number
      const num = parseInt(badge.textContent) || 0;
      if(num <= 1){ 
        // Fade out and remove badge
        badge.style.animation = 'fadeOut 0.3s ease-out';
        setTimeout(() => badge.remove(), 300);
        
        // Remove unread dot
        if(unreadDot) {
          unreadDot.style.animation = 'fadeOut 0.3s ease-out';
          setTimeout(() => unreadDot.remove(), 300);
        }
        
        // Reset preview style
        const preview = convEl.querySelector('.conversation-preview');
        if(preview){ 
          preview.classList.remove('unread');
        }
      } else { 
        badge.textContent = (num - 1).toString(); 
      }
    }catch(e){ console.error('chat_index read handler error', e); }
  });

  // Optimistically clear badge when user opens a conversation
  function attachClickHandlers(){
    document.querySelectorAll('[data-conv-id] .conversation-link').forEach(a => {
      // avoid adding multiple listeners
      if(a.dataset._hasClick) return;
      a.dataset._hasClick = '1';
      a.addEventListener('click', (ev) => {
        try{
          const convEl = a.closest('[data-conv-id]');
          if(!convEl) return;
          const convId = convEl.getAttribute('data-conv-id');
          
          // Remove badge with fade animation
          const badge = convEl.querySelector('.unread-indicator-badge');
          const unreadDot = convEl.querySelector('.unread-dot');
          
          if(badge) {
            badge.style.animation = 'fadeOut 0.3s ease-out';
            setTimeout(() => badge.remove(), 300);
          }
          
          if(unreadDot) {
            unreadDot.style.animation = 'fadeOut 0.3s ease-out';
            setTimeout(() => unreadDot.remove(), 300);
          }
          
          // Reset preview styling
          const preview = convEl.querySelector('.conversation-preview');
          if(preview){ 
            preview.classList.remove('unread');
          }

          // fire-and-forget POST to mark conversation read on server
          if(convId){
            fetch('/api/conversations/' + convId + '/mark_read', { method: 'POST', credentials: 'same-origin' }).catch(()=>{});
          }
        }catch(e){ console.error('click handler failed', e); }
        // allow navigation to continue
      });
    });
  }

  // Attach handlers now and also observe for future changes
  attachClickHandlers();
  
  // Simple observer to re-attach if DOM changes (e.g., badges inserted)
  const obs = new MutationObserver((m)=>{ attachClickHandlers(); });
  obs.observe(document.body, { childList: true, subtree: true });

  // Add hover effects for conversation cards
  document.querySelectorAll('.conversation-card').forEach(card => {
    card.addEventListener('mouseenter', () => {
      card.style.transform = 'translateY(-4px)';
    });
    card.addEventListener('mouseleave', () => {
      card.style.transform = 'translateY(0)';
    });
  });

  // Add hover effects for helper cards
  document.querySelectorAll('.helper-card').forEach(card => {
    card.addEventListener('mouseenter', () => {
      card.style.transform = 'translateY(-4px) scale(1.02)';
    });
    card.addEventListener('mouseleave', () => {
      card.style.transform = 'translateY(0) scale(1)';
    });
  });

  // Add fade-in animation for cards on page load
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }
    });
  }, { threshold: 0.1 });

  document.querySelectorAll('.conversation-card, .helper-card').forEach(card => {
    observer.observe(card);
  });

  // Search functionality
  const searchInput = document.getElementById('search-input');
  const searchBtn = document.getElementById('search-btn');
  
  function performSearch() {
    if (!searchInput) return;
    
    const searchTerm = searchInput.value.toLowerCase().trim();
    const conversationCards = document.querySelectorAll('.conversation-card');
    const helperCards = document.querySelectorAll('.helper-card');
    let visibleConvCount = 0;
    let visibleHelperCount = 0;
    
    console.log('Searching for:', searchTerm);
    console.log('Found conversation cards:', conversationCards.length);
    console.log('Found helper cards:', helperCards.length);
    
    // Search in conversations
    conversationCards.forEach(card => {
      const name = card.querySelector('.conversation-name');
      const preview = card.querySelector('.conversation-preview');
      
      if (name) {
        const nameText = name.textContent.toLowerCase().trim();
        const previewText = preview ? preview.textContent.toLowerCase().trim() : '';
        
        const matches = searchTerm === '' || 
                       nameText.includes(searchTerm) || 
                       previewText.includes(searchTerm);
        
        if (matches) {
          card.classList.remove('hidden');
          card.style.display = 'flex';
          card.style.animation = 'fadeIn 0.3s ease-out';
          visibleConvCount++;
        } else {
          card.classList.add('hidden');
          card.style.display = 'none';
        }
      }
    });
    
    // Search in helpers
    helperCards.forEach(card => {
      const name = card.querySelector('h4');
      
      if (name) {
        const nameText = name.textContent.toLowerCase().trim();
        
        const matches = searchTerm === '' || nameText.includes(searchTerm);
        
        if (matches) {
          card.classList.remove('hidden');
          card.style.display = 'flex';
          card.style.animation = 'fadeIn 0.3s ease-out';
          visibleHelperCount++;
        } else {
          card.classList.add('hidden');
          card.style.display = 'none';
        }
      }
    });
    
    console.log('Visible conversations:', visibleConvCount);
    console.log('Visible helpers:', visibleHelperCount);
    
    // Show/hide no results message for conversations
    const conversationsSection = document.querySelector('.conversations-section');
    const conversationsGrid = document.querySelector('.conversations-grid');
    let noResultsMsg = conversationsSection ? conversationsSection.querySelector('.no-results-message') : null;
    
    if (visibleConvCount === 0 && conversationsGrid && searchTerm !== '') {
      if (!noResultsMsg) {
        noResultsMsg = document.createElement('div');
        noResultsMsg.className = 'no-results-message';
        noResultsMsg.textContent = `No conversations found for "${searchInput.value}"`;
        conversationsSection.appendChild(noResultsMsg);
      } else {
        noResultsMsg.textContent = `No conversations found for "${searchInput.value}"`;
        noResultsMsg.style.display = 'block';
      }
    } else if (noResultsMsg) {
      noResultsMsg.style.display = 'none';
    }
    
    // Show/hide no results message for helpers
    const helpersSection = document.querySelector('.helpers-section');
    const helpersGrid = document.querySelector('.helpers-grid');
    let helperNoResultsMsg = helpersSection ? helpersSection.querySelector('.no-results-message') : null;
    
    if (visibleHelperCount === 0 && helpersGrid && searchTerm !== '') {
      if (!helperNoResultsMsg) {
        helperNoResultsMsg = document.createElement('div');
        helperNoResultsMsg.className = 'no-results-message';
        helperNoResultsMsg.textContent = `No helpers found for "${searchInput.value}"`;
        helpersSection.appendChild(helperNoResultsMsg);
      } else {
        helperNoResultsMsg.textContent = `No helpers found for "${searchInput.value}"`;
        helperNoResultsMsg.style.display = 'block';
      }
    } else if (helperNoResultsMsg) {
      helperNoResultsMsg.style.display = 'none';
    }
  }
  
  if (searchBtn) {
    searchBtn.addEventListener('click', (e) => {
      e.preventDefault();
      console.log('Search button clicked');
      performSearch();
    });
  }
  
  if (searchInput) {
    // Also search on Enter key
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        console.log('Enter key pressed');
        performSearch();
      }
    });
    
    // Real-time search as user types
    searchInput.addEventListener('input', () => {
      console.log('Input changed:', searchInput.value);
      performSearch();
    });
    
    // Focus effect
    searchInput.addEventListener('focus', () => {
      const container = searchInput.closest('.search-container');
      if (container) {
        container.style.borderColor = '#10b981';
        container.style.boxShadow = '0 0 0 3px rgba(16, 185, 129, 0.1)';
      }
    });
    
    searchInput.addEventListener('blur', () => {
      const container = searchInput.closest('.search-container');
      if (container && searchInput.value === '') {
        container.style.borderColor = 'rgba(16, 185, 129, 0.2)';
        container.style.boxShadow = 'none';
      }
    });
  }

})();
