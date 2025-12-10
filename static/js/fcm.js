console.log("[FCM] fcm.js loaded");

let messagingInstance = null;

async function initFCM() {
  console.log("[FCM] initFCM called");

  if (!("Notification" in window) || !("serviceWorker" in navigator)) {
    console.log("[FCM] This browser does not support notifications or service workers");
    return;
  }

  // Ask for permission
  const permission = await Notification.requestPermission();
  console.log("[FCM] Notification permission:", permission);
  if (permission !== "granted") {
    console.log("[FCM] Permission not granted, aborting FCM");
    return;
  }

  // Initialize Firebase (same config as your project)
  const firebaseConfig = {
  apiKey: "AIzaSyBXr5LOHnwm4T9mg3epZmpTnY924F3-4J0",
  authDomain: "lifeline-4ebf0.firebaseapp.com",
  projectId: "lifeline-4ebf0",
  storageBucket: "lifeline-4ebf0.firebasestorage.app",
  messagingSenderId: "624241564606",
  appId: "1:624241564606:web:4672a35b5b7ca6c413f7ef",
  measurementId: "G-1WTCBY1P9L",
  };

  if (!firebase.apps.length) {
    firebase.initializeApp(firebaseConfig);
  }

  const messaging = firebase.messaging();
  messagingInstance = messaging;

  // Register service worker
  const reg = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
  console.log("[FCM] Service worker registered", reg);

  const token = await messaging.getToken({
    vapidKey: "BNtodCll0PFmNnzBJ8grhcsGIUMLgM54_miSxfkY8GvR4-upDVIjzuJlh_9JkwhUWrqBs3OdaoDb-43yyB6zI9w",
    serviceWorkerRegistration: reg,
  });

  console.log("[FCM] Got FCM token:", token);

  // Send token to backend
  const resp = await fetch("/api/fcm/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ token }),
  });
  console.log("[FCM] Backend response:", resp);

  // Foreground messages => show OS notification
  messaging.onMessage((payload) => {
    console.log("[FCM] Foreground message:", payload);

    const data = payload.data || {};
    const title =
      data.title ||
      (payload.notification && payload.notification.title) ||
      "LifeLine";
    const body =
      data.body ||
      (payload.notification && payload.notification.body) ||
      "You have a new notification";

    if (Notification.permission === "granted") {
      new Notification(title, {
        body,
        data,
      });
    } else {
      console.log("[FCM] Notification permission is not granted (foreground)");
    }
  });
}

// Call once on DOMContentLoaded
document.addEventListener("DOMContentLoaded", () => {
  console.log("[FCM] DOMContentLoaded, calling initFCM()");
  initFCM().catch((e) => console.error("[FCM] initFCM error", e));
});
