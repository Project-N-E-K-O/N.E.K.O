/**
 * core/download.js — shared "save a fetched binary response" helpers for the
 * testbench export flows (P30 记忆分析导出, P31 角色一键导出).
 *
 * Both export entry points must (a) decode the friendly CJK download filename
 * the SAME way and (b) deliver the bytes through a pre-acquired 另存为 handle
 * with an identical anchor fallback. Keeping ONE copy here stops the two flows
 * from silently drifting on this security/timing-sensitive path.
 *
 * IMPORTANT: the 另存为 picker handle must be acquired by the CALLER *before*
 * the export `await fetch(...)` — `showSaveFilePicker` needs transient user
 * activation that an awaited fetch would consume (L66). These helpers only
 * consume the already-acquired handle; they never open the picker themselves.
 */

/** Parse the download name from a Content-Disposition header, preferring the
 *  RFC 5987 (`filename*=UTF-8''…`) form so a Chinese 角色名 survives; fall back
 *  to the plain `filename="…"`, then to `fallbackName`. */
export function parseDownloadFilename(cd, fallbackName) {
  const header = cd || '';
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star && star[1]) {
    try { return decodeURIComponent(star[1].trim()); } catch { /* fall through */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  if (plain && plain[1]) return plain[1].trim().replace(/"$/, '');
  return fallbackName;
}

/** Write a fetched ZIP `resp` to the pre-acquired 另存为 `saveHandle` if present,
 *  else via a plain anchor download. Returns `{ filename, bytes }`. */
export async function deliverZip(resp, saveHandle, fallbackName) {
  const cd = resp.headers.get('Content-Disposition') || '';
  const blob = await resp.blob();
  if (saveHandle) {
    const writable = await saveHandle.createWritable();
    await writable.write(blob);
    await writable.close();
    return {
      filename: saveHandle.name || parseDownloadFilename(cd, fallbackName),
      bytes: blob.size,
    };
  }
  const filename = parseDownloadFilename(cd, fallbackName);
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objUrl;
  a.download = filename;
  document.body.append(a);
  a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(objUrl); }, 100);
  return { filename, bytes: blob.size };
}
