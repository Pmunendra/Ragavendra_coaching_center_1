# Manual QA Checklist

Run through this after deploying, ideally against a staging DB first.
Nothing here has been run live in this session - only syntax/template
validated - so treat this as the test plan, not a report of results.

## Setup
- [ ] Apply `MIGRATION_NOTES.md` if you have an existing database
- [ ] Set `UPI_PAYEE_VPA` / `UPI_PAYEE_NAME` in your environment
- [ ] `pip install -r requirements.txt`
- [ ] Start the app, confirm no errors on the new tables being created

## Module 1-2: Student accounts
- [ ] Register a new student account with a photo
- [ ] Confirm the OTP email arrives and verifies correctly
- [ ] Log out, log back in
- [ ] Edit profile, change photo, change password
- [ ] Forgot password -> reset via OTP -> log in with new password

## Module 3-4: Admin college search & export
- [ ] Take a test exam via an existing public exam link (with the same
      email as a registered account) so there's data to find
- [ ] Search by college in `/admin/college-students`
- [ ] Export to Excel, PDF, CSV and open each file

## Module 9-10: Courses & payment
- [ ] Create a course as admin
- [ ] Browse to it as a student, click Buy Now
- [ ] Confirm the UPI QR renders and the `upi://pay` link opens correctly
      in a UPI app on a phone
- [ ] Submit a (test) transaction ID
- [ ] As admin, verify the payment in `/admin/payments`
- [ ] Confirm the student's subscription activates with the right expiry
      and the invoice PDF downloads correctly

## Module 5: Explanations
- [ ] Add a detailed explanation, reference, YouTube link, and voice URL
      to a question
- [ ] Take that exam, submit, confirm all four show up on the review page
- [ ] Confirm the voice-explanation button plays audio

## Module 6: Anti-cheat
- [ ] Enable "Strict Anti-Cheat" on a test exam
- [ ] Start the exam, switch tabs -> confirm immediate auto-submit and the
      "Exam Closed Due To Security Policy" message
- [ ] Confirm an activity log entry appears in `/admin/activity`
- [ ] Repeat on a non-strict exam -> confirm the old 3-warning behavior
      still works unchanged

## Module 7: Email reminders
- [ ] Set a exam's Scheduled Date & Time ~24h and ~1h out (test twice)
- [ ] Enroll a student (by college or email) with an exam link attached
- [ ] Run `flask send-exam-reminders` manually and confirm the email sends
      with correct date/time/syllabus/link
- [ ] Confirm re-running the command doesn't send duplicates

## Module 11: PDF viewer
- [ ] Upload a PDF to a course the test student is subscribed to
- [ ] Open it from the student side, confirm right-click/print/save
      shortcuts are blocked and the watermark shows the student's name
- [ ] Manually expire the subscription (edit `expires_at` in the DB) and
      confirm the stream route now returns 403

## Module 12-13: Leaderboard & analytics
- [ ] Confirm the leaderboard ranks correctly across weekly/monthly/overall
- [ ] Confirm analytics charts render with real data (subject marks,
      correct/wrong/skipped, daily progress, strong/weak topics)

## Module 14: Notifications
- [ ] Confirm a notification appears after payment verification
- [ ] Confirm a notification appears after a result is published
- [ ] Confirm the "notify all students" checkbox on new-course creation
      broadcasts correctly (test with a small student list first!)

## Module 16: Dark mode
- [ ] Toggle dark mode in the admin panel and in the student portal,
      confirm both persist across page loads and stay in sync
