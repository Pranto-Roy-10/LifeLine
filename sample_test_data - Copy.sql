-- SQL Script to Insert Sample Test Data for Smart Suggestions Demo
-- Run this to test the smart suggestion system with sample requests

-- Sample User Locations (Update existing users)
-- UPDATE user SET lat = 40.7128, lng = -74.0060 WHERE id = 1;
-- UPDATE user SET lat = 40.7150, lng = -74.0050 WHERE id = 2;

-- Sample Emergency Request (Rain-related)
INSERT INTO requests (
  user_id, title, category, description, 
  lat, lng, area, landmark,
  urgency, time_window,
  contact_method, contact_info,
  is_offer, radius_pref,
  created_at, expires_at, status
) VALUES (
  1,
  'Need umbrella delivery immediately',
  'umbrella',
  'Heavy rain expected in next 30 minutes, need an umbrella urgently',
  40.7150, -74.0060,
  'Downtown Manhattan', 'near Grand Central',
  'emergency',
  'anytime_today',
  'lifeline_chat',
  NULL,
  0,
  '2',
  NOW(),
  DATE_ADD(NOW(), INTERVAL 4 HOUR),
  'open'
);

-- Sample High Priority Medical Request
INSERT INTO requests (
  user_id, title, category, description,
  lat, lng, area, landmark,
  urgency, time_window,
  contact_method, contact_info,
  is_offer, radius_pref,
  created_at, expires_at, status
) VALUES (
  1,
  'Senior citizen needs medicine delivery',
  'medicine',
  'Elderly parent needs prescribed medicine urgently. Cannot go out due to weather',
  40.7120, -74.0070,
  'Upper East Side', 'near hospital',
  'high',
  'within_2_hours',
  'lifeline_chat',
  NULL,
  0,
  '3',
  NOW(),
  DATE_ADD(NOW(), INTERVAL 2 HOUR),
  'open'
);

-- Sample Evening Request (Groceries)
INSERT INTO requests (
  user_id, title, category, description,
  lat, lng, area, landmark,
  urgency, time_window,
  contact_method, contact_info,
  is_offer, radius_pref,
  created_at, expires_at, status
) VALUES (
  2,
  'Grocery shopping help needed',
  'groceries',
  'Need help with grocery shopping for dinner preparation',
  40.7145, -74.0055,
  'Midtown', 'near Times Square',
  'normal',
  'this_evening',
  'lifeline_chat',
  NULL,
  0,
  '2',
  NOW(),
  DATE_ADD(NOW(), INTERVAL 6 HOUR),
  'open'
);

-- Sample Morning Request
INSERT INTO requests (
  user_id, title, category, description,
  lat, lng, area, landmark,
  urgency, time_window,
  contact_method, contact_info,
  is_offer, radius_pref,
  created_at, expires_at, status
) VALUES (
  3,
  'Ride to airport needed',
  'ride',
  'Need a ride to airport early morning for flight',
  40.7130, -74.0040,
  'Downtown', 'near Penn Station',
  'normal',
  'morning_6am_to_9am',
  'lifeline_chat',
  NULL,
  0,
  '5',
  NOW(),
  DATE_ADD(NOW(), INTERVAL 12 HOUR),
  'open'
);

-- Sample Cold Weather Request
INSERT INTO requests (
  user_id, title, category, description,
  lat, lng, area, landmark,
  urgency, time_window,
  contact_method, contact_info,
  is_offer, radius_pref,
  created_at, expires_at, status
) VALUES (
  4,
  'Need warm blankets and heating supplies',
  'warm_supplies',
  'Homeless individuals in park need warm blankets and thermal supplies for cold night',
  40.7165, -74.0045,
  'Central Park', 'near south entrance',
  'high',
  'anytime_today',
  'lifeline_chat',
  NULL,
  0,
  '3',
  NOW(),
  DATE_ADD(NOW(), INTERVAL 8 HOUR),
  'open'
);

-- Sample Offer (Helping Request)
INSERT INTO requests (
  user_id, title, category, description,
  lat, lng, area, landmark,
  urgency, time_window,
  contact_method, contact_info,
  is_offer, radius_pref,
  frequency,
  created_at, expires_at, status
) VALUES (
  5,
  'Can help with home repair and maintenance',
  'repair',
  'Experienced with electrical, plumbing, and general home repairs. Available for emergency calls',
  40.7100, -74.0080,
  'West Side', 'near Hudson River',
  'normal',
  'anytime',
  'lifeline_chat',
  NULL,
  1,
  '5',
  'one_time_or_regular',
  NOW(),
  DATE_ADD(NOW(), INTERVAL 30 DAY),
  'open'
);

-- View the inserted data
SELECT 
  id, user_id, title, category, 
  urgency, 
  ROUND(SQRT(POWER(lat-40.7128, 2) + POWER(lng+74.0060, 2)) * 111, 2) as distance_km,
  created_at, expires_at, status
FROM requests
WHERE created_at >= DATE_SUB(NOW(), INTERVAL 1 DAY)
AND status = 'open'
ORDER BY created_at DESC;

-- Enable these lines to update user locations for testing
-- UPDATE user SET lat = 40.7128, lng = -74.0060 WHERE id IN (1, 2, 3, 4, 5);

-- Notes for Testing:
-- 1. Replace user IDs (1-5) with actual user IDs in your database
-- 2. Update user locations to be near test requests
-- 3. Run BEFORE testing the Smart Suggestions API
-- 4. Access dashboard at: http://localhost:5000/suggestions
-- 5. Enable location services in browser
-- 6. API will analyze these requests and suggest the most relevant ones

-- Example API Call:
-- POST /api/suggestions
-- {
--   "lat": 40.7128,
--   "lng": -74.0060,
--   "max_suggestions": 5
-- }

-- Expected Output:
-- The API will rank these requests by relevance score and return top 5
-- Scoring factors:
-- - Distance (closer = higher)
-- - Urgency (emergency > high > normal > low)
-- - Weather conditions (rain + umbrella match = high score)
-- - Time alignment (evening + groceries match = high score)
-- - Freshness (newer requests = higher)
