# Honor the saved Always on Top setting when creating the macOS character panel

In desktop 0.9.1, turn off Global Always on Top in desktop mode, quit, and relaunch. The character still covers a focused normal window while the chat yields to it. On macOS the new character panel reports `isAlwaysOnTop() === false` even though WindowServer puts it at floating level 3. The coordinator sees the requested value and skips the native level change.

[window-level.patch](window-level.patch) updates the Electron desktop source. When the saved preference is off, create the panel hidden, explicitly reset its native level, and call `showInactive()` after initialization. The visible character then starts at normal level 0. The condition applies only to character windows of type `panel` whose coordinator reports the preference is off.

## Integration

The public desktop build checks out the Electron source separately. Apply the patch in the desktop source checkout and test the resulting app. This public PR carries the patch for that integration. It applies independently of the silent startup proposal in [#3621](https://github.com/Project-N-E-K-O/N.E.K.O/pull/3621).

The patch was prepared against the packaged 0.9.1 source. SHA-256 for `src/window-manager.js` before and after application:

```text
before  3bbaad60a6a37e8f776f237816692c142a326d738c98eabdc0e458fda8ffffe4
after   9ec57b99b2a228678c4c1b5607f3eceeca075681464f3b878aa1c7f34908454d
```

```sh
git apply --check /path/to/window-level.patch
git apply /path/to/window-level.patch
node --check src/window-manager.js
```

## Native verification

1. On macOS, use desktop character mode with Global Always on Top off. Fully quit and relaunch. Focus another normal window overlapping the character. It should cover the character and chat.
2. Turn Global Always on Top on, then off while the character is visible. Check that it floats and then yields. Relaunch with it on and confirm the character still floats.
3. To inspect the native level directly, pass the desktop main process PID to this Swift script. Identify the full work-area character window by its bounds. The expected off/on levels are 0 and 3 respectively.

```swift
import CoreGraphics

let pid = Int(CommandLine.arguments[1])!
let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], 0) as! [[String: Any]]
for window in windows where window[kCGWindowOwnerPID as String] as? Int == pid {
    print(window[kCGWindowLayer as String]!, window[kCGWindowBounds as String]!)
}
```

On a real macOS arm64 desktop 0.9.1 restart with silent startup enabled and Global Always on Top off, the character/chat native levels changed from 3/0 before the patch to 0/0 afterward. An isolated Electron 42.6.1 panel fixture also verified startup with the preference on and off and both runtime toggle directions. Maintainers can repeat the native checks on the current desktop source revision.
