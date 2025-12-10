importScripts("https://www.gstatic.com/firebasejs/10.14.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.14.0/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyBXr5LOHnwm4T9mg3epZmpTnY924F3-4J0",
  authDomain: "lifeline-4ebf0.firebaseapp.com",
  projectId: "lifeline-4ebf0",
  storageBucket: "lifeline-4ebf0.firebasestorage.app",
  messagingSenderId: "624241564606",
  appId: "1:624241564606:web:4672a35b5b7ca6c413f7ef",
  measurementId: "G-1WTCBY1P9L",
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  console.log("[SW] Background message", payload);

  const data = payload.data || {};
  const title =
    data.title ||
    (payload.notification && payload.notification.title) ||
    "LifeLine";
  const body =
    data.body ||
    (payload.notification && payload.notification.body) ||
    "You have a new notification";

  const options = {
    body,
    data,
  };

  self.registration.showNotification(title, options);
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  let url = "/";

  if (data.type === "NEW_CHAT_MESSAGE" && data.conversation_id) {
    url = `/chat/${data.conversation_id}`;
  } else if (data.type === "NEARBY_REQUEST" && data.request_id) {
    url = `/requests/${data.request_id}`;
  }

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(url) && "focus" in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});
