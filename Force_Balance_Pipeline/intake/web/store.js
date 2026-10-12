// Where queued reports are kept. IndexedDB is the real store (it survives a closed tab and a restart); MemoryStore is the fallback
// when IndexedDB is unavailable (some private windows), and the page says so, because then a report is lost if the tab closes.
// A store is {list(), put(entry), delete(id)}, all async. IndexedDbStore resolves a write only when its transaction COMPLETES, which
// is when the data is durable, not when the request merely succeeded.

export const DB_NAME = "fbp-intake";
export const STORE_NAME = "queue";
export const DB_VERSION = 1;

export class MemoryStore {
  constructor() {
    this.entries = new Map();
    this.persistent = false;
  }

  async list() {
    return [...this.entries.values()].map((e) => ({ ...e }));
  }

  async put(entry) {
    this.entries.set(entry.report_id, { ...entry });
  }

  async delete(id) {
    this.entries.delete(id);
  }
}

const settle = (request) =>
  new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
  });

export class IndexedDbStore {
  constructor(indexedDB, { name = DB_NAME, storeName = STORE_NAME } = {}) {
    this.indexedDB = indexedDB;
    this.name = name;
    this.storeName = storeName;
    this.persistent = true;
    this._opening = null;
  }

  _db() {
    if (!this._opening) {
      this._opening = new Promise((resolve, reject) => {
        const request = this.indexedDB.open(this.name, DB_VERSION);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains(this.storeName)) {
            request.result.createObjectStore(this.storeName, { keyPath: "report_id" });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("could not open IndexedDB"));
        request.onblocked = () => reject(new Error("IndexedDB is blocked by another tab"));
      });
      this._opening.catch(() => {
        this._opening = null; // try again next time
      });
    }
    return this._opening;
  }

  async _transaction(mode, work) {
    const db = await this._db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(this.storeName, mode);
      let result;
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error || new Error("IndexedDB transaction failed"));
      tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
      work(tx.objectStore(this.storeName))
        .then((value) => {
          result = value;
        })
        .catch(reject);
    });
  }

  list() {
    return this._transaction("readonly", (store) => settle(store.getAll()));
  }

  put(entry) {
    return this._transaction("readwrite", (store) => settle(store.put(entry)));
  }

  delete(id) {
    return this._transaction("readwrite", (store) => settle(store.delete(id)));
  }
}

/** The best store available: IndexedDB if it opens, else memory (store.persistent says which). */
export async function openStore(indexedDB) {
  if (indexedDB) {
    try {
      const store = new IndexedDbStore(indexedDB);
      await store.list(); // opens it now, so a failure shows up here and not on the first report
      return store;
    } catch (e) {
      // fall through to memory
    }
  }
  return new MemoryStore();
}
