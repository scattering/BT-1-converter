// Scientific files/results live only in IndexedDB. Preferences are separate.
const DATABASE = 'bt1-browser-converter';
const VERSION = 1;

export async function openStorage() {
  let database;
  let failure = null;
  try {
    database = await new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) return reject(new Error('IndexedDB is unavailable.'));
      const request = indexedDB.open(DATABASE, VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains('scans')) db.createObjectStore('scans', { keyPath: 'id' });
        if (!db.objectStoreNames.contains('results')) db.createObjectStore('results', { keyPath: 'id' });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('Another tab is blocking browser storage. Close older converter tabs and reload.'));
    });
    database.onversionchange = () => database.close();
  } catch (error) { failure = error.message; }

  async function transaction(stores, mode, operation) {
    if (!database) return { persisted: false, error: failure || 'Storage unavailable' };
    try {
      const value = await new Promise((resolve, reject) => {
        const tx = database.transaction(stores, mode);
        let result;
        tx.oncomplete = () => resolve(result);
        tx.onerror = () => reject(tx.error || new Error('Browser storage failed.'));
        tx.onabort = () => reject(tx.error || new Error('Browser storage transaction was interrupted.'));
        operation(tx, value => { result = value; });
      });
      return { persisted: true, value };
    } catch (error) {
      failure = `${error.name || 'Storage error'}: ${error.message}. New files and results remain in memory for this tab; download them before closing it.`;
      return { persisted: false, error: failure };
    }
  }
  return {
    get available() { return Boolean(database && !failure); },
    get error() { return failure; },
    async load() {
      const result = await transaction(['scans', 'results'], 'readonly', (tx, done) => {
        const value = { scans: [], results: [] };
        tx.objectStore('scans').getAll().onsuccess = event => { value.scans = event.target.result; done(value); };
        tx.objectStore('results').getAll().onsuccess = event => { value.results = event.target.result; done(value); };
      });
      return result.value || { scans: [], results: [] };
    },
    putScan(scan) { return transaction(['scans'], 'readwrite', tx => tx.objectStore('scans').put(scan)); },
    putResult(result) { return transaction(['results'], 'readwrite', tx => tx.objectStore('results').put(result)); },
    removeScans(ids) { return transaction(['scans'], 'readwrite', tx => ids.forEach(id => tx.objectStore('scans').delete(id))); },
    removeResult(id) { return transaction(['results'], 'readwrite', tx => tx.objectStore('results').delete(id)); },
    clear() { return transaction(['scans', 'results'], 'readwrite', tx => { tx.objectStore('scans').clear(); tx.objectStore('results').clear(); }); },
  };
}

const PREFS = 'bt1-browser-preferences-v1';
export function loadPreferences() {
  try { return JSON.parse(localStorage.getItem(PREFS) || '{}'); }
  catch { return {}; }
}
export function savePreferences(preferences) {
  try { localStorage.setItem(PREFS, JSON.stringify(preferences)); return true; }
  catch { return false; }
}
