document.addEventListener("DOMContentLoaded", function () {
    // --- PART 1: NOTIFICATION BELL & STACK ---
    const notifBtn = document.getElementById("notif-btn");
    const notifDropdown = document.getElementById("notif-dropdown");
    const notifList = document.getElementById("notif-list");
    const notifBadge = document.getElementById("notif-badge");

    let isOpen = false;

    // 1. Toggle Dropdown on Bell Click
    if (notifBtn) {
        notifBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            isOpen = !isOpen;
            
            if (isOpen) {
                if (notifDropdown) notifDropdown.classList.remove("hidden");
                fetchNotifications(); // Fetch data from DB
            } else {
                if (notifDropdown) notifDropdown.classList.add("hidden");
            }
        });
    }

    // 2. Close dropdown when clicking outside
    document.addEventListener("click", (e) => {
        if (isOpen && notifDropdown && !notifDropdown.contains(e.target) && !notifBtn.contains(e.target)) {
            isOpen = false;
            notifDropdown.classList.add("hidden");
        }
    });

    // 3. Fetch Notifications from API
    async function fetchNotifications() {
        try {
            const response = await fetch("/api/notifications");
            if (!response.ok) return;
            const data = await response.json();
            renderStack(data);
            updateBadge(data.length); 
        } catch (error) {
            console.error("Notif Error:", error);
        }
    }

    // 4. Render the Stack HTML
    function renderStack(notifications) {
        if (!notifList) return;

        if (notifications.length === 0) {
            notifList.innerHTML = `
                <div class="flex flex-col items-center justify-center py-6 text-slate-500 opacity-60">
                    <p class="text-[10px] uppercase tracking-widest">No notifications</p>
                </div>`;
            return;
        }

        notifList.innerHTML = notifications.map(n => `
            <a href="${n.link || '#'}" class="block px-4 py-3 hover:bg-slate-800 transition border-l-2 ${getBorderColor(n.type)}">
                <div class="flex justify-between items-start mb-1">
                    <span class="text-[10px] font-bold uppercase tracking-wider ${getTextColor(n.type)}">
                        ${n.type.replace('_', ' ')}
                    </span>
                    <span class="text-[9px] text-slate-500">${n.created_at}</span>
                </div>
                <p class="text-xs text-slate-300 leading-snug">${n.message}</p>
            </a>
        `).join("");
    }

    // 5. Helper Colors
    function getTextColor(type) {
        if (type === 'sos' || type === 'SOS_ALERT') return 'text-rose-400';
        if (type === 'nearby') return 'text-amber-400';
        return 'text-emerald-400';
    }

    function getBorderColor(type) {
        if (type === 'sos' || type === 'SOS_ALERT') return 'border-rose-500';
        if (type === 'nearby') return 'border-amber-500';
        return 'border-transparent';
    }

    function updateBadge(count) {
        if (!notifBadge) return;
        if (count > 0) {
            notifBadge.innerText = count > 9 ? '9+' : count;
            notifBadge.classList.remove("hidden");
        } else {
            notifBadge.classList.add("hidden");
        }
    }

    // --- PART 2: LOCATION PERMISSION & UPDATE ---
    if (navigator.geolocation) {
        // This triggers the browser popup "LifeLine wants to know your location"
        navigator.geolocation.getCurrentPosition(success, error);
    }

    function success(position) {
        const lat = position.coords.latitude;
        const lng = position.coords.longitude;

        // Send to Backend
        fetch('/api/user/location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lat: lat, lng: lng })
        }).then(() => console.log("📍 Location updated."));
    }

    function error() {
        console.log("Location access denied. Nearby features disabled.");
    }

    // Initial Load
    fetchNotifications();
    setInterval(fetchNotifications, 15000); // Poll every 15s
});

// Global Mark Read
async function markAllRead() {
    try {
        await fetch("/api/notifications/read", { method: "POST" });
        const badge = document.getElementById("notif-badge");
        if(badge) badge.classList.add("hidden");
    } catch(e) { console.error(e); }
}