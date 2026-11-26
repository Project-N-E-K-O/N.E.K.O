/**
 * Enter in a one-line field that does something when the key is let go (`@keyup.enter`).
 *
 * With an input method (Chinese, Japanese, ...) Enter also picks the word being typed. Only the
 * way down says so: `isComposing`, or `keyCode === 229` where the browser has ended the
 * composition before the key is told of (Safari). Let go, it is an Enter like any other. So what
 * the key was is noted as it goes down, and letting go of one that picked a word does nothing.
 *
 *   <el-input @keydown="enterKey.down" @keyup.enter="enterKey.up(send)" />
 *
 * One per field: what is noted is the last key pressed in it.
 */
export interface EnterKey {
  /** Every key that goes down in the field. */
  down: (event: KeyboardEvent) => void
  /** Enter let go: `run` is called unless that Enter picked a word. */
  up: (run: () => void) => void
}

export function useEnterKey(): EnterKey {
  let pickedWord = false
  return {
    down(event) {
      if (event.isComposing || event.keyCode === 229) pickedWord = true
      // Another key pressed before Enter is let go (typing on at speed) leaves what Enter was as noted.
      else if (event.key === 'Enter') pickedWord = false
    },
    up(run) {
      const picked = pickedWord
      pickedWord = false
      if (!picked) run()
    },
  }
}
