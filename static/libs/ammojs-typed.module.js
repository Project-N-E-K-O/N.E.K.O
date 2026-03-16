/**
 * ES Module wrapper for ammojs-typed (Ammo.js / Bullet Physics)
 * This wraps the IIFE-style ammo.js as an ES module default export.
 */

// Load the IIFE ammo.js and capture the factory
const scriptUrl = new URL('./ammo.js', import.meta.url).href;

let _AmmoFactory = null;

// Fetch and eval the IIFE to get the Ammo factory
const response = await fetch(scriptUrl);
const text = await response.text();

// ammo.js defines: var Ammo = (function() { ... })();
// We need to capture that value
const fn = new Function(`${text}\nreturn Ammo;`);
_AmmoFactory = fn();

// Export the factory as default (matches `import Ammo from 'ammojs-typed'`)
export default _AmmoFactory;
