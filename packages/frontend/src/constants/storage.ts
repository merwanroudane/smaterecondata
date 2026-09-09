/**
 * Local storage keys used throughout the application
 */

export const STORAGE_KEYS = {
  /** JWT authentication token */
  AUTH_TOKEN: 'smatecondata_auth_token',

  /** Anonymous session ID for non-authenticated users */
  SESSION_ID: 'anon_session_id',

  /** User preferences */
  USER_PREFERENCES: 'smatecondata_user_preferences',

  /** Theme preference (light/dark) */
  THEME: 'smatecondata_theme',
} as const;

export type StorageKey = typeof STORAGE_KEYS[keyof typeof STORAGE_KEYS];
