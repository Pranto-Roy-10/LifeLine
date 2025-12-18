# 🎯 Human Availability Radar - Quick Reference Card

## 🚀 Quick Start (30 seconds)

1. **App Running?** ✅
   - Running at http://127.0.0.1:5000
   - Flask server active
   - Database connected

2. **Navigate to Map** 
   ```
   http://127.0.0.1:5000/map
   ```

3. **Login**
   - Use your account credentials
   - Or sign up for a new account

4. **Click "Availability Radar" Button**
   - Located at top of map
   - Purple gradient button
   - Watch it change to "Radar: ON"

5. **View the Heatmap**
   - Thermal colors show activity
   - Blue markers = helpers
   - Purple markers = members
   - Click for details

---

## 📊 Feature Components at a Glance

### Backend
```
✅ UserActivity Model
   - Stores location + activity metadata
   - Indexed for performance
   
✅ 3 API Endpoints
   - /api/activity/ping (record activity)
   - /api/radar/heatmap (get visualization)
   - /api/radar/active-users (get rankings)
```

### Frontend
```
✅ Radar Button
   - Purple gradient styling
   - Toggle on/off
   
✅ Heatmap Visualization
   - Google Maps heatmap layer
   - Real-time updates
   
✅ User Markers
   - Top 15 most active
   - Clickable for info
   - Size = activity level
```

### Database
```
✅ user_activity Table
   - Stores pings & motion data
   - 2 indexes for speed
   - Auto-purges old data
```

---

## 🔧 Configuration

### Default Settings
| Setting | Value |
|---------|-------|
| Refresh Rate | 3 seconds |
| Search Radius | 3 km |
| Heatmap Window | 5 minutes |
| User Window | 10 minutes |
| Max Users Shown | 50 |
| Heatmap Points | ~50+ |

### Adjustable Parameters
- `radius_km`: Change search radius (frontend)
- `activity_type`: Currently "ping" (expandable)
- `device_motion`: Simulated 30-70 range (use real data)

---

## 🎨 Visual Guide

### Button States
```
┌──────────────────────────────┐
│ ✓ Availability Radar         │  ← OFF
└──────────────────────────────┘

╭══════════════════════════════╮
│ ⊙ Radar: ON (pulses)         │  ← ON
╰══════════════════════════════╯
```

### Marker Colors
```
🔵 Blue Circle    = Verified Helper (is_trusted_helper: true)
🟣 Purple Circle  = Community Member (is_trusted_helper: false)

Size = Activity Level
• Small = Low activity
• Medium = Moderate activity  
• Large = High activity
```

### Heatmap Intensities
```
🔴 Red    → weight 0.8-1.0 (peak activity)
🟠 Orange → weight 0.6-0.8 (high activity)
🟡 Yellow → weight 0.4-0.6 (moderate activity)
🔵 Blue   → weight 0.2-0.4 (light activity)
⚪ Gray   → weight < 0.2   (no activity)
```

---

## 📋 API Quick Reference

### Record Activity
```bash
curl -X POST http://127.0.0.1:5000/api/activity/ping \
  -H "Content-Type: application/json" \
  -d '{"lat": 23.8, "lng": 90.4, "activity_type": "ping", "device_motion": 50}'

Response: {"ok": true, "activity_id": 123}
```

### Get Heatmap
```bash
curl -X POST http://127.0.0.1:5000/api/radar/heatmap \
  -H "Content-Type: application/json" \
  -d '{"user_lat": 23.8, "user_lng": 90.4, "radius_km": 3}'

Response: {
  "ok": true,
  "heatmap_points": [...],
  "total_active_nearby": 12,
  "helpers_nearby": 3
}
```

### Get Active Users
```bash
curl -X POST http://127.0.0.1:5000/api/radar/active-users \
  -H "Content-Type: application/json" \
  -d '{"user_lat": 23.8, "user_lng": 90.4, "radius_km": 3}'

Response: {
  "ok": true,
  "active_users": [...]
}
```

---

## 🔍 Data Flow Summary

```
┌─────────────────────────────────────────┐
│ User Clicks "Availability Radar"        │
└────────────────┬────────────────────────┘
                 ↓
        ┌────────────────────┐
        │ Radar Enabled      │
        └────────┬───────────┘
                 ↓
     ┌──────────────────────────┐
     │ Every 3 Seconds:         │
     │ • Send activity ping     │
     │ • Fetch heatmap data     │
     │ • Update visualization   │
     └──────────────┬───────────┘
                    ↓
       ┌────────────────────────────┐
       │ Display:                   │
       │ • Heatmap layer           │
       │ • 15 user markers         │
       │ • Availability scores     │
       └──────────────┬─────────────┘
                      ↓
        ┌─────────────────────────┐
        │ Repeat Until OFF        │
        └────────────┬────────────┘
                     ↓
        ┌─────────────────────────┐
        │ Radar Disabled          │
        │ Clear visualizations    │
        │ Stop tracking activity  │
        └─────────────────────────┘
```

---

## 🧪 Testing Steps

1. **Check App Status**
   ```bash
   # Should show: "Running on http://127.0.0.1:5000"
   python app.py
   ```

2. **Test API Endpoints**
   ```bash
   python test_radar_api.py
   ```

3. **Manual Browser Test**
   - Open http://127.0.0.1:5000/map
   - Log in
   - Click "Availability Radar"
   - Observe heatmap update
   - Click markers for info
   - Click button again to turn off

4. **Check Database**
   ```python
   from app import db, UserActivity
   # Review recent activities
   activities = UserActivity.query.limit(10).all()
   ```

---

## ⚡ Performance Tips

- **Fast Updates**: 3-second refresh is ideal for real-time feel
- **Low Overhead**: Only loads 5-minute activity window
- **Indexed Queries**: DB queries optimized with indexes
- **Limited Results**: Max 50 users to prevent lag
- **Responsive**: Works smoothly on standard connection

---

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| Button not showing | Ensure logged in, refresh page (F5) |
| Heatmap not updating | Check location permissions, allow access |
| No markers visible | Wait 10-15s for activities to accumulate |
| API errors (4xx) | Check authentication, verify user logged in |
| API errors (5xx) | Check server logs, restart app |
| Performance lag | Disable radar, check network |
| Database errors | Run migrations, check DB connection |

---

## 📚 Documentation Files

| File | Purpose |
|------|---------|
| IMPLEMENTATION_COMPLETE.md | This summary |
| AVAILABILITY_RADAR_GUIDE.md | Technical details |
| RADAR_USER_GUIDE.md | User instructions |
| test_radar_api.py | API testing script |
| IMPLEMENTATION_SUMMARY.md | Original project summary |

---

## 🎯 Key Metrics

### Intensity Calculation
```
activity_intensity = min(100, count × 15) + bonus_for_motion
recency_factor = max(0.3, 1 - age/300)
weight = activity_intensity × recency_factor / 100
```

### Availability Score  
```
score = (recency×100 + activity_count×20 + motion×2) / 3
Max: 100 | Min: 0 | Range: [0-100]
```

---

## 🎓 Learning Paths

### For Users
1. Read RADAR_USER_GUIDE.md
2. Log in and navigate to /map
3. Click radar button
4. Explore heatmap and markers
5. Click markers for details

### For Developers
1. Review AVAILABILITY_RADAR_GUIDE.md
2. Check app.py (lines 337-352, 3223-3380)
3. Review map.html JavaScript functions
4. Run test_radar_api.py
5. Query user_activity table directly

### For DevOps
1. Ensure database migrations run
2. Monitor /api/radar/* endpoints
3. Check user_activity table growth
4. Set up data archival (optional)
5. Monitor performance under load

---

## ✅ Implementation Checklist

- [x] Database model created
- [x] API endpoints implemented
- [x] Frontend button added
- [x] Heatmap visualization working
- [x] Real-time updates functioning
- [x] User markers displaying
- [x] Authentication integrated
- [x] Database migration created
- [x] Documentation complete
- [x] Testing script ready
- [x] App tested and verified
- [x] Error handling in place

---

## 🚀 Deployment Checklist

- [x] Code tested locally
- [x] No breaking changes
- [x] Database migration included
- [x] Error handling complete
- [x] Performance optimized
- [x] Security verified
- [x] Documentation ready
- [x] API documented
- [x] Testing guide included

---

## 📞 Support Resources

- **GitHub Issues**: Report bugs or feature requests
- **Documentation**: See AVAILABILITY_RADAR_GUIDE.md
- **Testing**: Run test_radar_api.py for verification
- **Browser Console**: F12 for client-side errors
- **Server Logs**: Check terminal for Flask output

---

## 🎉 Success Indicators

When everything is working:
- ✅ Radar button visible on /map
- ✅ Button toggles state (ON/OFF)
- ✅ Heatmap appears when ON
- ✅ Markers update every 3 seconds
- ✅ Clicking markers shows info
- ✅ No JavaScript errors in console
- ✅ API endpoints return 200 status
- ✅ Database records activity

**All indicators present? You're good to go! 🎊**

---

## 🔗 Links

- **Live Map**: http://127.0.0.1:5000/map
- **Login**: http://127.0.0.1:5000/login
- **Sign Up**: http://127.0.0.1:5000/signup
- **Dashboard**: http://127.0.0.1:5000/dashboard

---

*Quick Reference v1.0*  
*Generated: December 18, 2025*  
*Status: Complete & Tested ✅*
