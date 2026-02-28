// fcm.js - Final (balanced braces + foreground popup)

let messagingInstance = null;

function lifelineCanUseFCM() {
  // Push + Service Workers require a secure context (HTTPS) or localhost.
  if (!window.isSecureContext) return false;
  if (!("serviceWorker" in navigator)) return false;
  if (!("Notification" in window)) return false;
  if (!("PushManager" in window)) return false;
  if (typeof firebase === "undefined") return false;
  return true;
}

function lifelineDefer(fn) {
  // Defer work off the critical render path.
  if ("requestIdleCallback" in window) {
    window.requestIdleCallback(() => fn(), { timeout: 3000 });
  } else {
    setTimeout(fn, 1500);
  }
}

const firebaseConfig = {
  apiKey: "AIzaSyBXr5LOHnwm4T9mg3epZmpTnY924F3-4J0",
  authDomain: "lifeline-4ebf0.firebaseapp.com",
  projectId: "lifeline-4ebf0",
  storageBucket: "lifeline-4ebf0.firebasestorage.app",
  messagingSenderId: "624241564606",
  appId: "1:624241564606:web:4672a35b5b7ca6c413f7ef",
  measurementId: "G-1WTCBY1P9L",
};

const VAPID_KEY =
  "BNtodCll0PFmNnzBJ8grhcsGIUMLgM54_miSxfkY8GvR4-upDVIjzuJlh_9JkwhUWrqBs3OdaoDb-43yyB6zI9w";

async function initFCM() {
  // Only attempt once per page load.
  if (window.__lifelineFCMInitAttempted) return;
  window.__lifelineFCMInitAttempted = true;

  if (!lifelineCanUseFCM()) {
    return;
  }

  // Never prompt on page load (hurts UX + Lighthouse). If already granted, proceed.
  if (Notification.permission !== "granted") {
    return;
  }

  // 1) Init Firebase
  try {
    if (!firebase.apps.length) {
      firebase.initializeApp(firebaseConfig);
    }
  } catch (e) {
    // Non-fatal: notifications are optional.
    console.warn("[FCM] Firebase init failed:", e);
    return;
  }

  // 3) Messaging + SW + Token
  try {
    const messaging = firebase.messaging();
    messagingInstance = messaging;

    const reg = await navigator.serviceWorker.register(
      "/firebase-messaging-sw.js"
    );

    const token = await messaging.getToken({
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: reg,
    });

    if (!token) {
      return;
    }

    // 4) Register token with backend (session cookie auth)
    try {
      const resp = await fetch("/api/fcm/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token }),
      });

      const respData = await resp.json().catch(() => ({}));
      // Keep console noise low in production; still useful for debugging.
      // eslint-disable-next-line no-console
      console.log("[FCM] Token register:", resp.status, respData);
    } catch (err) {
      console.warn("[FCM] Failed to register token with backend:", err);
    }

    // 5) Foreground handler (show popup when tab is open)
    messaging.onMessage((payload) => {
      const data = payload?.data || {};
      const title = data.title || payload?.notification?.title || "LifeLine";
      const body =
        data.body ||
        payload?.notification?.body ||
        "You have a new notification";

      if (Notification.permission === "granted") {
        new Notification(title, { body, data });
      }
    });
  } catch (e) {
    // Common in Lighthouse / some browser setups: AbortError: Registration failed - push service error
    const msg = String(e && (e.message || e)).toLowerCase();
    const name = String(e && e.name).toLowerCase();
    const isPushServiceError =
      name.includes("aborterror") ||
      msg.includes("push service") ||
      msg.includes("registration failed");

    if (isPushServiceError) {
      console.warn("[FCM] Push not available in this browser context.");
      return;
    }

    console.warn("[FCM] FCM init error:", e);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  lifelineDefer(() => {
    initFCM().catch(() => {});
  });
});
