// Number formatting for the City View and the banners. format.js's compact() already moves a value that would
// round to 10000 of a unit to the next unit (9999.6万 → 1亿), so these are plain re-exports.
export { compact, money, yieldValue, popShort } from "../../format.js";
