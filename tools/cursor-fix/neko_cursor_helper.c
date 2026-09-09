// neko_cursor_helper.c
// 极简 Win32 光标隐藏/恢复工具，替代 N.E.K.O 桌宠原 PowerShell 子进程方案。
//
// 用法（通过 stdin 一行一命令）：
//   neko_cursor_helper.exe < hide      # 隐藏全部 18 个系统光标
//   neko_cursor_helper.exe < restore   # 通过 SPI_SETCURSORS 恢复默认光标
//   neko_cursor_helper.exe < exit      # 退出（不恢复）
//
// 行为：
//   - 启动后立即执行 hide（消除 PowerShell 冷启动延迟）
//   - 阻塞从 stdin 读命令，hide/restore 切换状态
//   - 收到 exit 或 stdin EOF 时退出
//
// 编译（MSVC）：
//   cl /O2 /W4 neko_cursor_helper.c user32.lib /Fe:neko_cursor_helper.exe

#include <windows.h>
#include <stdio.h>

#define SPI_SETCURSORS 0x0057

static const UINT kCursorIds[] = {
    32512, 32513, 32514, 32515, 32516,
    32640, 32641, 32642, 32643, 32644,
    32645, 32646, 32648, 32649, 32650,
    32651, 32671, 32672,
};
#define CURSOR_ID_COUNT (sizeof(kCursorIds)/sizeof(kCursorIds[0]))

static HCURSOR MakeTransparentCursor(void) {
    // 32x32 1bpp：AND = 全 0xFF（全透明），XOR = 全 0x00（无色）
    // 每行 32 像素 = 4 字节；行数 32；AND/XOR 各 128 字节
    static BYTE andMask[128];
    static BYTE xorMask[128];
    for (int i = 0; i < 128; ++i) {
        andMask[i] = 0xFF;
        xorMask[i] = 0x00;
    }
    return CreateCursor(NULL, 0, 0, 32, 32, andMask, xorMask);
}

static void HideSystemCursors(void) {
    for (size_t i = 0; i < CURSOR_ID_COUNT; ++i) {
        HCURSOR h = MakeTransparentCursor();
        if (h) {
            SetSystemCursor(h, kCursorIds[i]);
        }
    }
    fprintf(stderr, "[neko-cursor-helper] hidden 18 cursors\n");
}

static void RestoreSystemCursors(void) {
    SystemParametersInfo(SPI_SETCURSORS, 0, NULL, 0);
    fprintf(stderr, "[neko-cursor-helper] restored via SPI_SETCURSORS\n");
}

int wmain(void) {
    // 启动即 hide：避免 PowerShell 那种"启动 + .NET 加载 + Add-Type"导致的 500ms+ 延迟
    HideSystemCursors();

    char line[64];
    while (fgets(line, sizeof(line), stdin)) {
        // 去除换行
        for (char *p = line; *p; ++p) {
            if (*p == '\r' || *p == '\n') { *p = '\0'; break; }
        }
        if (_stricmp(line, "restore") == 0) {
            RestoreSystemCursors();
        } else if (_stricmp(line, "hide") == 0) {
            HideSystemCursors();
        } else if (_stricmp(line, "exit") == 0) {
            break;
        } else if (line[0] != '\0') {
            fprintf(stderr, "[neko-cursor-helper] unknown command: %s\n", line);
        }
    }

    // 进程退出前兜底恢复一次（避免父进程异常退出后系统光标永久隐藏）
    RestoreSystemCursors();
    return 0;
}