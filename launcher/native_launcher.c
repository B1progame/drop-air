#include <windows.h>
#include <wchar.h>
#include <wctype.h>

static const wchar_t WINDOW_CLASS[] = L"DropAirNativeStartup";
static const wchar_t CORE_EXE[] = L"DropAirCore.exe";
static const wchar_t INSTANCE_MUTEX[] = L"Local\\B1progame.DropAir.Desktop.Instance";
static const UINT ACTIVATE_DROP_AIR = WM_APP + 41;
static HWND startup_window;
static HANDLE core_process;
static DWORD core_pid;
static ULONGLONG process_exit_time;
static wchar_t app_dir[32768];
static HANDLE instance_mutex;
static BOOL owns_instance_mutex;

static void release_instance_mutex(void) {
    if (!instance_mutex) return;
    if (owns_instance_mutex) ReleaseMutex(instance_mutex);
    CloseHandle(instance_mutex);
    instance_mutex = NULL;
    owns_instance_mutex = FALSE;
}

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
typedef struct { HWND window; DWORD process_id; } ExistingAppSearch;

static BOOL is_dropair_process(DWORD process_id) {
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id);
    if (!process) return FALSE;
    wchar_t path[32768];
    DWORD length = (DWORD)(sizeof(path) / sizeof(path[0]));
    BOOL matches = FALSE;
    if (QueryFullProcessImageNameW(process, 0, path, &length)) {
        const wchar_t *name = wcsrchr(path, L'\\');
        name = name ? name + 1 : path;
        matches = _wcsicmp(name, L"DropAir.exe") == 0 || _wcsicmp(name, CORE_EXE) == 0;
    }
    CloseHandle(process);
    return matches;
}

static BOOL CALLBACK find_existing_dropair_window(HWND window, LPARAM parameter) {
    ExistingAppSearch *search = (ExistingAppSearch *)parameter;
    if (GetWindow(window, GW_OWNER) != NULL) return TRUE;
    wchar_t title[128] = {0};
    GetWindowTextW(window, title, 127);
    if (wcsncmp(title, L"Drop Air", 8) != 0) return TRUE;
    DWORD process_id = 0;
    GetWindowThreadProcessId(window, &process_id);
    if (process_id && is_dropair_process(process_id)) {
        search->window = window;
        search->process_id = process_id;
        return FALSE;
    }
    return TRUE;
}

static ExistingAppSearch find_existing_dropair(void) {
    ExistingAppSearch search = { NULL, 0 };
    EnumWindows(find_existing_dropair_window, (LPARAM)&search);
    return search;
}

static void focus_dropair_window(HWND window, DWORD process_id) {
    if (!window || !IsWindow(window)) return;
    AllowSetForegroundWindow(process_id);
    if (IsIconic(window)) ShowWindow(window, SW_RESTORE);
    else ShowWindow(window, SW_SHOW);
    BringWindowToTop(window);
    SetForegroundWindow(window);
}

static BOOL activate_existing_dropair(void) {
    ExistingAppSearch search = find_existing_dropair();
    if (!search.window) return FALSE;
    focus_dropair_window(search.window, search.process_id);
    return TRUE;
}

static BOOL acquire_instance_mutex(void) {
    instance_mutex = CreateMutexW(NULL, TRUE, INSTANCE_MUTEX);
    if (!instance_mutex) return FALSE;
    DWORD error = GetLastError();
    owns_instance_mutex = error != ERROR_ALREADY_EXISTS;
    if (!owns_instance_mutex) {
        DWORD wait = WaitForSingleObject(instance_mutex, 5000);
        if (wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED) owns_instance_mutex = TRUE;
        else {
            // The first launch still owns the short startup lock. Its skeleton is
            // already visible or will appear momentarily, so never start a twin.
            activate_existing_dropair();
            release_instance_mutex();
            return FALSE;
        }
    }
    return TRUE;
}

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
    const COLORREF bone = RGB(48, 52, 55);
    const COLORREF bone_light = RGB(55, 60, 64);
    const COLORREF panel_fill = RGB(32, 36, 40);
    const COLORREF panel_edge = RGB(48, 55, 59);
    int width = bounds.right - bounds.left;
    int height = bounds.bottom - bounds.top;
    int card_width = width - 40;
    if (card_width > 560) card_width = 560;
    if (card_width < 280) card_width = 280;
    int card_height = 116;
    int gap = 20;
    int total_height = card_height * 3 + gap * 2;
    int left = (width - card_width) / 2;
    int top = (height - total_height) / 2;
    const int title_widths[3] = {218, 204, 232};
    const int line_widths[3][3] = {{330, 394, 286}, {352, 410, 318}, {310, 420, 292}};
    for (int index = 0; index < 3; ++index) {
        int row_y = top + index * (card_height + gap);
        HBRUSH brush = CreateSolidBrush(panel_fill);
        HPEN pen = CreatePen(PS_SOLID, 1, panel_edge);
        HGDIOBJ old_brush = SelectObject(dc, brush);
        HGDIOBJ old_pen = SelectObject(dc, pen);
        RoundRect(dc, left, row_y, left + card_width, row_y + card_height, 10, 10);
        SelectObject(dc, old_pen);
        SelectObject(dc, old_brush);
        DeleteObject(pen);
        DeleteObject(brush);
        rounded(dc, left + 22, row_y + 30, 56, 56, bone_light, 56);
        int text_x = left + 94;
        int max_text_width = card_width - 122;
        int title_width = title_widths[index] < max_text_width ? title_widths[index] : max_text_width;
        rounded(dc, text_x, row_y + 18, title_width, 15, bone, 5);
        for (int line = 0; line < 3; ++line) {
            int line_width = line_widths[index][line] < max_text_width ? line_widths[index][line] : max_text_width;
            rounded(dc, text_x, row_y + 48 + line * 13, line_width, 4, bone_light, 2);
        }
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
    if (command_line && (wcscmp(command_line, L"--version") == 0 || wcscmp(command_line, L"-V") == 0)) {
        if (!launch_core(command_line, TRUE)) return 1;
        return 0;
    }

    if (!acquire_instance_mutex()) return 0;
    ExistingAppSearch existing = find_existing_dropair();
    if (existing.window) {
        BOOL resume = command_line && wcsstr(command_line, L"--resume") != NULL;
        if (!resume) {
            focus_dropair_window(existing.window, existing.process_id);
            release_instance_mutex();
            return 0;
        }
        HANDLE previous_process = OpenProcess(SYNCHRONIZE, FALSE, existing.process_id);
        if (!previous_process) {
            focus_dropair_window(existing.window, existing.process_id);
            release_instance_mutex();
            return 0;
        }
        DWORD previous_exit = WaitForSingleObject(previous_process, 10000);
        CloseHandle(previous_process);
        if (previous_exit != WAIT_OBJECT_0) {
            focus_dropair_window(existing.window, existing.process_id);
            release_instance_mutex();
            return 0;
        }
    }
    if (command_line && *command_line) {
        release_instance_mutex();
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
    if (!startup_window) {
        release_instance_mutex();
        return 1;
    }

    wchar_t icon_path[32768];
    swprintf(icon_path, 32768, L"%ls\\assets\\icon\\drop_air_brand.ico", app_dir);
    HICON icon = (HICON)LoadImageW(NULL, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE | LR_DEFAULTSIZE);
    if (icon) {
        SendMessageW(startup_window, WM_SETICON, ICON_BIG, (LPARAM)icon);
        SendMessageW(startup_window, WM_SETICON, ICON_SMALL, (LPARAM)icon);
    }

    ShowWindow(startup_window, show_command);
    UpdateWindow(startup_window);
    release_instance_mutex();
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
