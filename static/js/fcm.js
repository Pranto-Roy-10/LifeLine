// fcm.js - Final (balanced braces + foreground popup)

let messagingInstance = null;

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
  console.log("[FCM] initFCM called");

  // 1) Init Firebase
  try {
    if (!firebase.apps.length) {
      firebase.initializeApp(firebaseConfig);
      console.log("[FCM] Firebase App initialized.");
    }
  } catch (e) {
    console.error("[FCM FATAL] Firebase init failed:", e);
    return;
  }

  // 2) Notification permission
  const permission = await Notification.requestPermission();
  console.log("[FCM] Notification permission:", permission);
  if (permission !== "granted") {
    console.log("[FCM] Permission not granted, aborting FCM");
    return;
  }

  // 3) Messaging + SW + Token
  try {
    const messaging = firebase.messaging();
    messagingInstance = messaging;

    const reg = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
    console.log("[FCM] Service worker registered", reg);

    const token = await messaging.getToken({
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: reg,
    });

    console.log("[FCM] Got FCM token:", token);

    // 4) Register token with backend (session cookie auth)
    try {
      const resp = await fetch("/api/fcm/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token }),
      });

      const respData = await resp.json().catch(() => ({}));
      console.log("[FCM] Backend register response:", resp.status, respData);
    } catch (err) {
      console.error("[FCM] Failed to register token with backend:", err);
    }

    // 5) Foreground handler (show popup when tab is open)
    messaging.onMessage((payload) => {
      console.log("[FCM] Foreground message:", payload);

      const data = payload?.data || {};
      const title = data.title || payload?.notification?.title || "LifeLine";
      const body = data.body || payload?.notification?.body || "You have a new notification";

      if (Notification.permission === "granted") {
        new Notification(title, { body, data });
      }
    });
  } catch (e) {
    console.error("[FCM FATAL] Crash during FCM process:", e);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  console.log("[FCM] DOMContentLoaded, calling initFCM()");
  initFCM().catch((e) => console.error("[FCM] Top-level initFCM error", e));
});
