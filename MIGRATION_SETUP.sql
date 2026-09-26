-- ============================================================================
-- Exam Portal Database Migration: Video Security & Flexible Packaging
-- ============================================================================
-- UPDATE: no longer needed to run this by hand. app/__init__.py's
-- _auto_migrate_new_columns() adds these same columns automatically on
-- every app startup (SQLite and MySQL alike), so a fresh `db.create_all()`
-- or an existing database both end up correct without this script. Kept
-- here as a reference for what the columns are and why.
-- ============================================================================
-- This script adds all necessary columns for the new features.
-- Run this script against your database to enable the new functionality.
--
-- Usage:
--   For SQLite: sqlite3 instance/app.db < MIGRATION_SETUP.sql
--   For MySQL: mysql -u root -p your_db < MIGRATION_SETUP.sql
--   For PostgreSQL: psql -U postgres your_db < MIGRATION_SETUP.sql
--
-- Note: If using Flask-Migrate, run this instead:
--   flask db migrate -m "Add video security and package configuration"
--   flask db upgrade
--
-- ============================================================================

-- ============================================================================
-- COURSES TABLE: Add new pricing and package configuration fields
-- ============================================================================

-- Add new pricing fields for separate packages
ALTER TABLE courses ADD COLUMN price_mock_tests FLOAT DEFAULT 0;
ALTER TABLE courses ADD COLUMN price_videos_only FLOAT DEFAULT 0;

-- Add package configuration flags
ALTER TABLE courses ADD COLUMN videos_as_separate_package BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN pdfs_as_separate_package BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN exams_as_separate_package BOOLEAN DEFAULT FALSE;
ALTER TABLE courses ADD COLUMN mock_tests_as_separate_package BOOLEAN DEFAULT TRUE;

-- ============================================================================
-- COURSE_MATERIALS TABLE: Add video upload and security fields
-- ============================================================================

-- Add video file upload flag
ALTER TABLE course_materials ADD COLUMN is_video_file_upload BOOLEAN DEFAULT FALSE;

-- Add security option fields
ALTER TABLE course_materials ADD COLUMN enable_watermark BOOLEAN DEFAULT TRUE;
ALTER TABLE course_materials ADD COLUMN enable_download_protection BOOLEAN DEFAULT TRUE;
ALTER TABLE course_materials ADD COLUMN enable_copy_protection BOOLEAN DEFAULT TRUE;
ALTER TABLE course_materials ADD COLUMN disable_seek BOOLEAN DEFAULT FALSE;

-- ============================================================================
-- VERIFICATION
-- ============================================================================
-- After running this migration, verify the columns were added:
--
-- For SQLite:
--   PRAGMA table_info(courses);
--   PRAGMA table_info(course_materials);
--
-- For MySQL:
--   DESCRIBE courses;
--   DESCRIBE course_materials;
--
-- For PostgreSQL:
--   \d courses;
--   \d course_materials;
--
-- ============================================================================

-- ============================================================================
-- ROLLBACK (if needed)
-- ============================================================================
-- If you need to revert these changes, uncomment and run:
--
-- ALTER TABLE courses DROP COLUMN price_mock_tests;
-- ALTER TABLE courses DROP COLUMN price_videos_only;
-- ALTER TABLE courses DROP COLUMN videos_as_separate_package;
-- ALTER TABLE courses DROP COLUMN pdfs_as_separate_package;
-- ALTER TABLE courses DROP COLUMN exams_as_separate_package;
-- ALTER TABLE courses DROP COLUMN mock_tests_as_separate_package;
--
-- ALTER TABLE course_materials DROP COLUMN is_video_file_upload;
-- ALTER TABLE course_materials DROP COLUMN enable_watermark;
-- ALTER TABLE course_materials DROP COLUMN enable_download_protection;
-- ALTER TABLE course_materials DROP COLUMN enable_copy_protection;
-- ALTER TABLE course_materials DROP COLUMN disable_seek;
--
-- ============================================================================

-- Migration completed successfully!
-- The database is now ready for the enhanced exam portal features.
