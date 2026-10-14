import * as api from './api.js';
import * as cache from './cache.js';

export const MANAGED_TAG_DEFINITIONS_CACHE_KEY = 'managed_tag_definitions';

/** Load managed definitions, preserving a cached copy for offline editing. */
export async function loadManagedTagDefinitions(online) {
  let cacheWarning = '';
  if (online) {
    try {
      const definitions = await api.tagDefinitions.all();
      try {
        await cache.setMeta(MANAGED_TAG_DEFINITIONS_CACHE_KEY, definitions);
      } catch (error) {
        cacheWarning = `Definitions loaded, but the offline copy could not be saved: ${error.message || 'local storage unavailable'}.`;
      }
      return { definitions, source: 'server', cacheWarning, error: '' };
    } catch (serverError) {
      let cached;
      try {
        cached = await cache.getMeta(MANAGED_TAG_DEFINITIONS_CACHE_KEY);
      } catch (cacheError) {
        return {
          definitions: null,
          source: 'unavailable',
          cacheWarning: '',
          error: `Could not load definitions from the server or local storage: ${cacheError.message || serverError.message || 'request unavailable'}.`,
        };
      }
      if (!cached || !Array.isArray(cached.genres) || !Array.isArray(cached.vibes)) {
        return {
          definitions: null,
          source: 'unavailable',
          cacheWarning: '',
          error: `Could not reach the server and no saved definitions are available: ${serverError.message || 'request unavailable'}.`,
        };
      }
      return {
        definitions: cached,
        source: 'cache',
        cacheWarning: '',
        error: '',
      };
    }
  }

  try {
    const definitions = await cache.getMeta(MANAGED_TAG_DEFINITIONS_CACHE_KEY);
    if (!definitions || !Array.isArray(definitions.genres) || !Array.isArray(definitions.vibes)) {
      return { definitions: null, source: 'unavailable', cacheWarning: '', error: 'No saved definitions are available offline. Connect to load definitions before adding assignments.' };
    }
    return { definitions, source: 'cache', cacheWarning: '', error: '' };
  } catch (error) {
    return {
      definitions: null,
      source: 'unavailable',
      cacheWarning: '',
      error: `Could not read saved definitions: ${error.message || 'local storage unavailable'}. Connect to load definitions before adding assignments.`,
    };
  }
}
