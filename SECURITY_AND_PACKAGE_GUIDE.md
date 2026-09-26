# Exam Portal Security & Package Configuration Guide

## Overview

This enhanced version of the Kalyani Exam Hub includes comprehensive security features, DRM-lite video protection, and flexible package configuration that allows admins to customize what content requires separate purchases.

---

## 1. NEW DATABASE FIELDS (Auto-migrated)

### Course Model Enhancements

```python
# New pricing fields for separate packages
price_mock_tests              # Price for mock tests as separate package
price_videos_only             # Price for videos as separate package

# Package configuration booleans
videos_as_separate_package    # True = videos need separate purchase
pdfs_as_separate_package      # True = PDFs need separate purchase  
exams_as_separate_package     # True = exams need separate purchase
mock_tests_as_separate_package # True = mock tests need separate purchase (default: True)
```

### CourseMaterial Model Enhancements

```python
is_video_file_upload           # True if video is uploaded file (not external URL)
enable_watermark              # Add student watermark to videos
enable_download_protection    # Prevent video/PDF downloads
enable_copy_protection        # Disable copy/right-click on PDFs
disable_seek                  # Ultra-strict: prevent video seeking
```

### CoursePurchase Model Updates

```python
# New grant methods based on package config:
grants_mock_tests()           # Check if purchase grants mock test access
```

---

## 2. ADMIN PANEL - COURSE CONFIGURATION

### New Course Setup Options

When creating/editing a course, you'll see new sections:

#### A. Tiered Pricing (Updated)
- Only Exam (₹X)
- Exam + PDF / Notes (₹X)  
- Only Class (Video) (₹X)
- **NEW: Mock Tests Only (₹X)**
- **NEW: Videos Only (₹X)**
- All Access - Exam + PDF + Class (₹X)
- Discount % (applied to all)

#### B. Package Configuration (NEW)
Four toggle options allow you to decide what needs separate purchases:

```
☑ Videos as Separate Package
   → If ON: Videos require "Videos Only" or "All Access" tier
   → If OFF: Videos included in "Class Only" and "All Access"

☑ PDFs as Separate Package  
   → If ON: PDFs require "Exam+PDF" or "All Access" tier
   → If OFF: PDFs included in "Exam+PDF" and "All Access"

☑ Exams as Separate Package
   → If ON: Exams require "Exam Only" or "All Access" tier
   → If OFF: Exams included in most tiers

☑ Mock Tests as Separate Package (Default: ON)
   → If ON: Mock tests require "Mock Tests" or "All Access" tier
   → If OFF: Mock tests included in all tiers
```

### Example Configurations

**Configuration 1: Everything Bundled (Simple)**
```
Videos: OFF
PDFs: OFF
Exams: OFF
Mock Tests: OFF
→ Students choose: Exam Only OR Class Only OR All Access
```

**Configuration 2: Tier Everything (Maximum Flexibility)**
```
Videos: ON
PDFs: ON
Exams: ON
Mock Tests: ON
→ Students can buy: Exam Only, PDF Only, Videos Only, Mock Tests, or All Access
```

**Configuration 3: Current User's Setup (Trial + Paid Model)**
```
Videos: ON
PDFs: OFF
Exams: OFF
Mock Tests: ON
→ Videos as separate, Mock Tests as separate
→ Students choose: Basic (trial demo), Videos Only, Mock Tests, Full Bundle
```

---

## 3. MATERIAL UPLOAD - VIDEO FILES (NEW FEATURE)

### Video Upload Process

1. **Go to**: Admin Panel → Courses → [Course Name] → Course Materials
2. **Upload Type**: Select "Video Class"
3. **Choose Upload Method**:
   - **External URL**: YouTube/Vimeo link (old style - direct iframe)
   - **Upload File**: Direct video upload (NEW - secure streaming)

### For Direct Video Upload:
```
Title: "Advanced Calculus - Integration Methods"
Material Type: Video Class
Upload File: [Select .mp4, .webm, .mov file]
Access Duration: 30/60/90/180/365 days or Lifetime
Enable Watermark: ☑ (adds student name/email overlay)
Enable Download Protection: ☑ (prevents saving locally)
Enable Copy Protection: ☑ (disables right-click)
Disable Seek: ☐ (ultra-strict - prevents video scrubbing)
```

### Video File Support
- **Formats**: MP4, WebM, MOV, AVI, MKV (server will transcode to MP4)
- **Max Size**: 2GB per file (configurable)
- **Storage**: Stored securely in `instance/uploads/course_materials/`
- **Delivery**: Streamed directly with DRM-lite protection

---

## 4. SECURITY FEATURES

### A. Video Streaming Security

#### DRM-Lite Protection
```
✓ Token-based authentication
  - Each video stream requires a signed token
  - Tokens valid for 2 hours only
  - Tied to specific student ID

✓ No Direct Downloads
  - Content-Disposition: inline (prevents "Save as")
  - controlsList="nodownload" on HTML5 player
  - Server-side range request validation

✓ User Watermarking
  - Student name + email embedded in video
  - Timestamp showing when accessed
  - Rotated at -45° to discourage recording
  - Refreshes every 5 seconds to prevent removal

✓ Cache Prevention
  - Cache-Control: no-store, no-cache, max-age=0
  - No browser caching of video segments
  - Re-validated on every access

✓ Anti-Sharing Features
  - Student info logged with every stream
  - Access logs track IP, User-Agent, timestamp
  - Unusual patterns flagged automatically
```

### B. PDF Security

#### Copy/Download Prevention
```
✓ Right-Click Disabled
  - Context menu blocked JavaScript-side
  - Admin can toggle via "Enable Copy Protection"

✓ Download Prevention
  - Content-Disposition: inline (not attachment)
  - Download button hidden in viewer

✓ Client-Side Controls
  - Keyboard shortcuts disabled (Ctrl+C, Ctrl+A, Ctrl+S, Ctrl+X)
  - Printing disabled
  - Developer tools discouraged

✓ Watermarking
  - Student name + email on every PDF page
  - Can be toggled per material
  - Applied server-side or client-side

✓ Secure Viewer
  - PDF.js embedded viewer
  - Security headers prevent framing
  - Content-Security-Policy headers enforced
```

### C. Material Access Control

#### Access Granted Only If:
1. **Subscription Active**: Purchase paid status + not expired
2. **Correct Tier**: Purchase includes access to this material type
3. **Material Expiry**: Material-specific duration not exceeded
4. **Token Valid**: Signed security token present and valid
5. **Student Logged In**: Session validation passed

#### Access Denied If:
- Subscription status is "pending" or "failed"
- Subscription expired  
- Purchase tier doesn't grant access to this material type
- Material access window closed
- Token missing or forged
- Student not authenticated
- IP address flagged as suspicious

---

## 5. MOCK TESTS - PACKAGE GATING

### New Purchase Model for Mock Tests

**Before**: Mock tests were part of "Exam Only" tier
**After**: Mock tests can be separate package

### Implementation Steps

1. **Enable in Course Config**:
   - Go to Course Edit → Package Configuration
   - Check "Mock Tests as Separate Package"
   - Add price for "Mock Tests Only" tier

2. **Student Experience**:
   ```
   Purchase Tier: "Mock Tests Only"
   Access: Can take mock tests
   Cannot: View videos, access PDFs, view exams
   
   Purchase Tier: "Exam Only"
   Access: Can take exams (not mock tests if separate)
   ```

3. **Conditional Gating** (in routes):
   ```python
   purchase.grants_mock_tests()  # True if can access mock tests
   ```

### Adding to Frontend

In student dashboard (my_courses.html):
```html
{% for course in courses %}
  {% if course.mock_tests_as_separate_package %}
    <!-- Show "Mock Tests Only" as separate tier -->
    <button class="btn btn-success">Buy Mock Tests (₹{{ course.price_mock_tests }})</button>
  {% endif %}
{% endfor %}
```

---

## 6. ADMIN ACCESS LOG - AUDIT TRAIL

### Track All Material Access

**Path**: Admin Panel → Material Access Log

Shows every access with:
- Student name + ID
- Material title + ID
- Action type (view / stream)
- IP address
- User-Agent (browser/device)
- Timestamp

**Use Cases**:
- Identify suspicious downloads attempts
- Verify student engagement
- Audit compliance requirements
- Detect unauthorized sharing

---

## 7. STUDENT EXPERIENCE

### Before Purchase
```
Course Detail Page:
[Video] "Intro to Physics" 
→ Click to play
→ Redirected to login/checkout
→ "Subscribe to access"
```

### After Purchase (With Video Tier)
```
My Courses → Physics 101
→ Materials section
→ Click "Video Lecture 1"
→ Secure video player loads
→ Shows watermark: "John Doe | john@email.com"
→ Cannot:
   - Download (no save option)
   - Right-click (context menu disabled)
   - Skip ahead (if disable_seek enabled)
   - Record (watermark visible in any recording)
```

### Access Expiration
```
Subscription expires: 2024-12-31
Material access expires: 2024-10-15 (if 30-day duration)
→ Student loses access 2024-10-15
→ Can renew subscription to regain access
```

---

## 8. DATABASE MIGRATION SCRIPT

### Auto-Apply Changes

Run this after deploying the code:

```bash
# Using Flask CLI
flask db migrate -m "Add video security and package config"
flask db upgrade

# Or manually execute SQL (if needed):
ALTER TABLE courses ADD COLUMN price_mock_tests FLOAT DEFAULT 0;
ALTER TABLE courses ADD COLUMN price_videos_only FLOAT DEFAULT 0;
ALTER TABLE courses ADD COLUMN videos_as_separate_package BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN pdfs_as_separate_package BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN exams_as_separate_package BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN mock_tests_as_separate_package BOOLEAN DEFAULT TRUE;

ALTER TABLE course_materials ADD COLUMN is_video_file_upload BOOLEAN DEFAULT FALSE;
ALTER TABLE course_materials ADD COLUMN enable_watermark BOOLEAN DEFAULT TRUE;
ALTER TABLE course_materials ADD COLUMN enable_download_protection BOOLEAN DEFAULT TRUE;
ALTER TABLE course_materials ADD COLUMN enable_copy_protection BOOLEAN DEFAULT TRUE;
ALTER TABLE course_materials ADD COLUMN disable_seek BOOLEAN DEFAULT FALSE;
```

---

## 9. KEY FILE CHANGES

### New Files Created
```
app/services/video_service.py          # Video streaming with security
app/services/pdf_security_service.py   # PDF protection features
app/templates/student/material_video_secure.html  # Secure video player
```

### Modified Files
```
app/models.py
  - Course: New pricing + config fields
  - CourseMaterial: Video file upload + security options
  - CoursePurchase: New grant methods

app/student/routes.py
  - material_view: Handle secure videos
  - material_stream: Stream videos with DRM headers

app/templates/admin/course_form.html
  - New pricing fields
  - Package configuration toggles
```

---

## 10. API ENDPOINTS REFERENCE

### Video Streaming (Authenticated)
```
GET /student/materials/<material_id>/view
  → Shows secure video player (if file upload)
  → Shows iframe (if external URL)
  
GET /student/materials/<material_id>/stream?token=<token>
  → Serves video file with DRM headers
  → Validates token, subscription, material access
  → Logs access for audit
```

### Admin Course Management
```
POST /admin/courses        → Create course with new config
PUT /admin/courses/<id>    → Update with new fields
GET /admin/material_access_log  → View audit trail
```

---

## 11. SECURITY CHECKLIST

- [ ] Enable watermarking for all videos
- [ ] Enable download protection for sensitive materials
- [ ] Enable copy protection for PDF notes
- [ ] Review material access logs weekly
- [ ] Set appropriate expiry durations (30-180 days)
- [ ] Configure package tiers matching your business model
- [ ] Test each tier's access restrictions
- [ ] Verify mock tests gated correctly
- [ ] Monitor suspicious access patterns
- [ ] Update SSL certificates (HTTPS required)

---

## 12. TROUBLESHOOTING

### Videos Not Playing
```
1. Check file format (MP4 recommended)
2. Verify file uploaded successfully
3. Check material_type == "video"
4. Verify is_video_file_upload == True
5. Test subscription validity
6. Check browser console for CORS errors
```

### Watermark Not Showing
```
1. Verify enable_watermark = True
2. Check browser JavaScript enabled
3. Clear cache and reload
4. Check student is authenticated
```

### Access Denied
```
1. Verify subscription paid status
2. Check purchase tier grants access
3. Verify material expiry not passed
4. Check token in URL valid
5. Review MaterialAccessLog for errors
```

### Download Still Possible
```
1. Verify enable_download_protection = True
2. Check Content-Disposition headers
3. Verify controlsList="nodownload" in HTML5 video
4. Test in different browsers
```

---

## 13. CONFIGURATION EXAMPLES

### Example 1: Free Trial + Paid Tiers
```python
course.price_exam_only = 0          # Trial: exams only, free
course.price_class_only = 299       # Videos: ₹299
course.price_exam_pdf = 399         # Exams + PDFs: ₹399
course.price_mock_tests = 199       # Mock Tests: ₹199
course.price_videos_only = 299      # Videos: ₹299
course.price = 799                  # All Access: ₹799

course.videos_as_separate_package = True
course.mock_tests_as_separate_package = True
course.pdfs_as_separate_package = False
course.exams_as_separate_package = False
```

### Example 2: Everything Separate (Maximum Control)
```python
course.price_exam_only = 199
course.price_videos_only = 299
course.price_exam_pdf = 199
course.price_mock_tests = 99
course.price_class_only = 299
course.price = 799

course.videos_as_separate_package = True
course.pdfs_as_separate_package = True
course.exams_as_separate_package = True
course.mock_tests_as_separate_package = True
```

---

## 14. SUPPORT & NEXT STEPS

### Immediate Actions
1. Run database migration
2. Test login flow
3. Create test course with new config
4. Upload sample video file
5. Test purchase flow for each tier
6. Verify access restrictions working

### Enhancement Ideas
- HLS streaming for better playback
- Video analytics dashboard
- More detailed watermark customization
- Expiry reminder notifications
- Tier upgrade/downgrade mid-subscription
- Family sharing with separate logins

---

**Last Updated**: August 2026
**Version**: 2.1 with Security & Package Config
**Status**: Production Ready ✓
