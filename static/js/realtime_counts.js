(function () {
  // Only run for logged-in users
  try {
    if (!window.LIFELINE_CURRENT_USER_ID) return;
  } catch (e) {
    return;
  }

  function ensureSocketIO(ready) {
    if (typeof window.io === "function") return ready();

    const existing = document.querySelector('script[data-socketio="1"]');
    if (existing) {
      existing.addEventListener("load", ready, { once: true });
      return;
    }

    const s = document.createElement("script");
    s.src = "https://cdn.socket.io/4.5.4/socket.io.min.js";
    s.async = true;
    s.dataset.socketio = "1";
    s.addEventListener("load", ready, { once: true });
    document.head.appendChild(s);
  }

  function setHomeChatBadge(count) {
    const el = document.getElementById("home-chat-unread-badge");
    if (!el) return;

    const n = parseInt(count || 0, 10) || 0;
    if (n > 0) {
      el.textContent = n > 9 ? "9+" : String(n);
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  }

  function setNotifBadge(count) {
    // Prefer the existing base.html updater if present
    if (typeof window.lifelineUpdateNotificationBadge === "function") {
      window.lifelineUpdateNotificationBadge(count);
      return;
    }

    const el = document.getElementById("notif-badge");
    if (!el) return;
    const n = parseInt(count || 0, 10) || 0;
    if (n > 0) {
      el.textContent = n > 9 ? "9+" : String(n);
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  }

  ensureSocketIO(function () {
    const socket = window.io(window.location.origin, {
      transports: ["websocket", "polling"],
    });

    socket.on("connect", function () {
      // Join personal room for count updates
      try {
        socket.emit("join", {});
      } catch (e) {}
    });

    // Server sends authoritative counts so UI never lies.
    socket.on("counts_update", function (payload) {
      try {
        if (payload && typeof payload.notification_count !== "undefined") {
          setNotifBadge(payload.notification_count);
        }
        if (payload && typeof payload.unread_chat_count !== "undefined") {
          setHomeChatBadge(payload.unread_chat_count);
        }
      } catch (e) {}
    });

    // When a new notification arrives, refresh dropdown list instantly (if available)
    socket.on("notification_new", function () {
      try {
        if (typeof window.lifelineFetchNotifications === "function") {
          window.lifelineFetchNotifications();
        } else if (
          typeof window.lifelineFetchNotificationCount === "function"
        ) {
          window.lifelineFetchNotificationCount();
        }
      } catch (e) {}
    });
  });
})();
