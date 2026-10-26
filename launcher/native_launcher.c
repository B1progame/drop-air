#include <windows.h>
#include <wchar.h>
#include <wctype.h>

static const wchar_t WINDOW_CLASS[] = L"DropAirNativeStartup";
static const wchar_t CORE_EXE[] = L"DropAirCore.exe";
static HWND startup_window;
static HANDLE core_process;
static DWORD core_pid;
static ULONGLONG process_exit_time;
static wchar_t app_dir[32768];

static void rounded(HDC dc, int x, int y, int w, int h, COLORREF color, int radius) {
    HBRUSH brush = CreateSolidBrush(color);
    HGDIOBJ old = SelectObject(dc, brush);
    HPEN pen = CreatePen(PS_NULL, 0, color);
    HGDIOBJ old_pen = SelectObject(dc, pen);
    RoundRect(dc, x, y, x + w, y + h, radius, radius);
    SelectObject(dc, old_pen);
    SelectObject(dc, old);
    DeleteObject(pen);
    DeleteObject(brush);
}

static void panel(HDC dc, int x, int y, int w, int h) {
    HBRUSH brush = CreateSolidBrush(RGB(32, 36, 40));
    HPEN pen = CreatePen(PS_SOLID, 1, RGB(55, 63, 67));
    HGDIOBJ old_brush = SelectObject(dc, brush);
    HGDIOBJ old_pen = SelectObject(dc, pen);
    RoundRect(dc, x, y, x + w, y + h, 8, 8);
    SelectObject(dc, old_pen);
    SelectObject(dc, old_brush);
    DeleteObject(pen);
    DeleteObject(brush);
}

typedef struct { DWORD process_id; HWND window; } WindowSearch;
static BOOL CALLBACK find_core_window_safe(HWND window, LPARAM parameter) {
    WindowSearch *search = (WindowSearch *)parameter;
    DWORD process_id = 0;
    GetWindowThreadProcessId(window, &process_id);
    if (process_id == search->process_id && IsWindowVisible(window) && GetWindow(window, GW_OWNER) == NULL) {
        search->window = window;
        return FALSE;
    }
    return TRUE;
}

static BOOL has_core_window(void) {
    WindowSearch search = { core_pid, NULL };
    EnumWindows(find_core_window_safe, (LPARAM)&search);
    return search.window != NULL;
}

typedef struct { HWND window; } DropAirWindowSearch;
static BOOL CALLBACK find_dropair_window(HWND window, LPARAM parameter) {
    DropAirWindowSearch *search = (DropAirWindowSearch *)parameter;
    if (window != startup_window && IsWindowVisible(window) && GetWindow(window, GW_OWNER) == NULL) {
        wchar_t title[128] = {0};
        GetWindowTextW(window, title, 127);
        if (wcsncmp(title, L"Drop Air", 8) == 0) {
            search->window = window;
            return FALSE;
        }
    }
    return TRUE;
}

static BOOL has_dropair_window(void) {
    DropAirWindowSearch search = { NULL };
    EnumWindows(find_dropair_window, (LPARAM)&search);
    return search.window != NULL;
}

static void draw_skeleton(HDC dc, RECT bounds) {
    const COLORREF bone = RGB(44, 49, 53);
    const COLORREF bone_light = RGB(55, 62, 66);
    const COLORREF edge = RGB(72, 82, 87);
    int width = bounds.right - bounds.left;
    int height = bounds.bottom - bounds.top;
    int margin = width > 760 ? 24 : 16;
    int gap = 16;
    int usable = width - margin * 2;
    int left_width = (usable - gap) / 2;
    int right_x = margin + left_width + gap;
    int right_width = usable - left_width - gap;
    int panel_y = 92;
    int upload_height = height / 3;
    if (upload_height < 240) upload_height = 240;

    rounded(dc, margin, 24, 42, 42, bone_light, 10);
    rounded(dc, margin + 56, 30, 190, 28, bone, 6);
    rounded(dc, width - margin - 110, 34, 110, 24, bone, 12);

    panel(dc, margin, panel_y, left_width, upload_height);
    rounded(dc, margin + 16, panel_y + 16, 112, 18, bone, 5);
    rounded(dc, margin + 16, panel_y + 42, left_width - 32 < 184 ? left_width - 32 : 184, 12, bone, 4);
    rounded(dc, margin + left_width - 74, panel_y + 22, 58, 24, bone, 12);

    int drop_y = panel_y + 82;
    int drop_h = upload_height - 96;
    HPEN dash = CreatePen(PS_DASH, 1, edge);
    HGDIOBJ old_pen = SelectObject(dc, dash);
    HGDIOBJ old_brush = SelectObject(dc, GetStockObject(HOLLOW_BRUSH));
    RoundRect(dc, margin + 8, drop_y, margin + left_width - 8, drop_y + drop_h, 8, 8);
    SelectObject(dc, old_brush);
    SelectObject(dc, old_pen);
    DeleteObject(dash);

    int center = margin + left_width / 2;
    rounded(dc, center - 22, drop_y + drop_h * 28 / 100, 44, 44, bone_light, 10);
    rounded(dc, center - 130, drop_y + drop_h * 28 / 100 + 58, 260, 15, bone, 5);
    rounded(dc, center - 60, drop_y + drop_h * 28 / 100 + 86, 120, 42, bone, 8);

    int text_y = panel_y + upload_height + gap;
    panel(dc, margin, text_y, left_width, 180);
    rounded(dc, margin + 16, text_y + 18, 112, 18, bone, 5);
    rounded(dc, margin + left_width - 74, text_y + 15, 58, 24, bone, 12);
    HPEN outline = CreatePen(PS_SOLID, 1, RGB(55, 63, 67));
    old_pen = SelectObject(dc, outline);
    old_brush = SelectObject(dc, GetStockObject(HOLLOW_BRUSH));
    RoundRect(dc, margin + 8, text_y + 56, margin + left_width - 8, text_y + 164, 8, 8);
    SelectObject(dc, old_brush);
    SelectObject(dc, old_pen);
    DeleteObject(outline);

    int list_height = upload_height + 10;
    panel(dc, right_x, panel_y, right_width, list_height);
    rounded(dc, right_x + 16, panel_y + 16, 112, 18, bone, 5);
    rounded(dc, right_x + 16, panel_y + 42, right_width - 32 < 184 ? right_width - 32 : 184, 12, bone, 4);
    for (int index = 0; index < 3; ++index) {
        int row_y = panel_y + 68 + index * 66;
        if (row_y + 64 > panel_y + list_height) break;
        if (index) {
            HPEN divider = CreatePen(PS_SOLID, 1, RGB(48, 55, 59));
            old_pen = SelectObject(dc, divider);
            MoveToEx(dc, right_x + 1, row_y, NULL);
            LineTo(dc, right_x + right_width - 1, row_y);
            SelectObject(dc, old_pen);
            DeleteObject(divider);
        }
        rounded(dc, right_x + 14, row_y + 12, 36, 36, bone_light, 8);
        rounded(dc, right_x + 62, row_y + 14, right_width - 120 < 230 ? right_width - 120 : 230, 15, bone, 5);
        rounded(dc, right_x + 62, row_y + 36, right_width - 120 < 86 ? right_width - 120 : 86, 10, bone, 4);
    }
}

static void close_core(void) {
    if (!core_pid) return;
    WindowSearch search = { core_pid, NULL };
    EnumWindows(find_core_window_safe, (LPARAM)&search);
    if (search.window) PostMessageW(search.window, WM_CLOSE, 0, 0);
    else if (core_process) TerminateProcess(core_process, 0);
}

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
    case WM_PAINT: {
        PAINTSTRUCT paint;
        HDC dc = BeginPaint(window, &paint);
        RECT bounds;
        GetClientRect(window, &bounds);
        HBRUSH background = CreateSolidBrush(RGB(23, 26, 29));
        FillRect(dc, &bounds, background);
        DeleteObject(background);
        draw_skeleton(dc, bounds);
        EndPaint(window, &paint);
        return 0;
    }
    case WM_TIMER:
        if (has_core_window() || (process_exit_time && has_dropair_window())) {
            KillTimer(window, 1);
            ShowWindow(window, SW_HIDE);
            DestroyWindow(window);
            return 0;
        }
        if (core_process && WaitForSingleObject(core_process, 0) == WAIT_OBJECT_0) {
            if (!process_exit_time) process_exit_time = GetTickCount64();
            if (GetTickCount64() - process_exit_time > 10000) {
                MessageBoxW(window, L"Drop Air could not start. Try reinstalling the app.", L"Drop Air", MB_OK | MB_ICONERROR);
                DestroyWindow(window);
            }
        }
        return 0;
    case WM_CLOSE:
        KillTimer(window, 1);
        close_core();
        DestroyWindow(window);
        return 0;
    case WM_DESTROY:
        if (core_process) CloseHandle(core_process);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static BOOL launch_core(const wchar_t *arguments, BOOL wait_for_exit) {
    wchar_t executable[32768];
    wchar_t command[65536];
    if (swprintf(executable, 32768, L"%ls\\%ls", app_dir, CORE_EXE) < 0) return FALSE;
    if (arguments && *arguments) swprintf(command, 65536, L"\"%ls\" %ls", executable, arguments);
    else swprintf(command, 65536, L"\"%ls\"", executable);

    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    BOOL started = CreateProcessW(executable, command, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT,
                                  NULL, app_dir, &startup, &process);
    if (!started) return FALSE;
    if (wait_for_exit) {
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        return TRUE;
    }
    core_process = process.hProcess;
    core_pid = process.dwProcessId;
    CloseHandle(process.hThread);
    return TRUE;
}

static void app_directory(void) {
    GetModuleFileNameW(NULL, app_dir, (DWORD)(sizeof(app_dir) / sizeof(app_dir[0])));
    wchar_t *separator = wcsrchr(app_dir, L'\\');
    if (separator) *separator = L'\0';
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show_command) {
    (void)previous;
    app_directory();
    if (command_line && *command_line) {
        if (!launch_core(command_line, TRUE)) return 1;
        return 0;
    }

    WNDCLASSEXW window_class = {0};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(NULL, IDC_ARROW);
    window_class.hbrBackground = CreateSolidBrush(RGB(23, 26, 29));
    window_class.lpszClassName = WINDOW_CLASS;
    RegisterClassExW(&window_class);

    int width = 1092;
    int height = 1255;
    RECT frame = {0, 0, width, height};
    AdjustWindowRect(&frame, WS_OVERLAPPEDWINDOW, FALSE);
    int outer_width = frame.right - frame.left;
    int outer_height = frame.bottom - frame.top;
    int x = (GetSystemMetrics(SM_CXSCREEN) - outer_width) / 2;
    int y = (GetSystemMetrics(SM_CYSCREEN) - outer_height) / 2;
    startup_window = CreateWindowExW(WS_EX_APPWINDOW, WINDOW_CLASS, L"Drop Air loading",
                                     WS_OVERLAPPEDWINDOW, x, y, outer_width, outer_height,
                                     NULL, NULL, instance, NULL);
    if (!startup_window) return 1;

    wchar_t icon_path[32768];
    swprintf(icon_path, 32768, L"%ls\\assets\\icon\\drop_air_brand.ico", app_dir);
    HICON icon = (HICON)LoadImageW(NULL, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE | LR_DEFAULTSIZE);
    if (icon) {
        SendMessageW(startup_window, WM_SETICON, ICON_BIG, (LPARAM)icon);
        SendMessageW(startup_window, WM_SETICON, ICON_SMALL, (LPARAM)icon);
    }

    ShowWindow(startup_window, show_command);
    UpdateWindow(startup_window);
    SetTimer(startup_window, 1, 50, NULL);
    if (!launch_core(L"", FALSE)) {
        MessageBoxW(startup_window, L"The Drop Air application files are missing or could not be opened.", L"Drop Air", MB_OK | MB_ICONERROR);
        DestroyWindow(startup_window);
        return 1;
    }

    MSG message;
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    if (icon) DestroyIcon(icon);
    if (window_class.hbrBackground) DeleteObject(window_class.hbrBackground);
    return (int)message.wParam;
}
