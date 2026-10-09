// One-time move of what this browser saved for the Control Center (chats, open
// rows, drafts) from the keys LabNet 2.x used to the labnet-* keys of 3.0. Runs
// before shell.js reads anything; a key already in its new place is kept.

(function (root) {
  "use strict";

  const OLD_PREFIX = "pqt-"; // (LabNet 2.x) the keys this file moves
  const NEW_PREFIX = "labnet-";

  function migrateStoredKeys(storage, oldPrefix, newPrefix) {
    const keys = [];
    for (let index = 0; index < storage.length; index += 1) keys.push(storage.key(index));
    const moved = [];
    for (const key of keys) {
      if (typeof key !== "string" || !key.startsWith(oldPrefix)) continue;
      const target = newPrefix + key.slice(oldPrefix.length);
      if (storage.getItem(target) === null) {
        storage.setItem(target, storage.getItem(key));
        moved.push(target);
      }
      storage.removeItem(key);
    }
    return moved;
  }

  root.LabNet = Object.assign(root.LabNet || {}, { migrateStoredKeys });
  for (const area of ["localStorage", "sessionStorage"]) {
    try {
      if (root[area]) migrateStoredKeys(root[area], OLD_PREFIX, NEW_PREFIX);
    } catch (_unavailable) {
      /* private windows and blocked site data: nothing to move */
    }
  }
})(typeof window !== "undefined" ? window : globalThis);
