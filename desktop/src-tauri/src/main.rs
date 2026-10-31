// Приложение для Windows: отдельное окно панели «Очередь публикаций».
// При первом запуске спрашивает адрес сервера и дальше открывает панель сразу.
// Сама панель работает на сервере — приложение только показывает её, как браузер.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, State, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

const WINDOW: &str = "main";

/// Адрес сервера панели; хранится в файле в папке настроек пользователя.
struct Server(Mutex<Option<Url>>);

fn config_file(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|dir| dir.join("server.txt"))
}

fn parse_server(input: &str) -> Result<Url, String> {
    let input = input.trim();
    if input.is_empty() {
        return Err("Укажите адрес панели".into());
    }
    let text = if input.contains("://") {
        input.to_string()
    } else {
        format!("https://{input}")
    };
    let url =
        Url::parse(&text).map_err(|_| "Не похоже на адрес: проверьте написание".to_string())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err("Адрес должен начинаться с https://".into());
    }
    Ok(url)
}

fn saved_server(app: &AppHandle) -> Option<Url> {
    let text = fs::read_to_string(config_file(app)?).ok()?;
    parse_server(&text).ok()
}

/// Страница выбора сервера, встроенная в приложение.
fn setup_page() -> Url {
    let base = if cfg!(windows) {
        "http://tauri.localhost/index.html"
    } else {
        "tauri://localhost/index.html"
    };
    Url::parse(base).expect("адрес встроенной страницы")
}

#[tauri::command]
fn get_server(state: State<Server>) -> Option<String> {
    state.0.lock().unwrap().as_ref().map(Url::to_string)
}

#[tauri::command]
fn connect(app: AppHandle, state: State<Server>, url: String) -> Result<(), String> {
    let url = parse_server(&url)?;
    if let Some(path) = config_file(&app) {
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("Не удалось сохранить адрес: {e}"))?;
        }
        fs::write(&path, url.as_str()).map_err(|e| format!("Не удалось сохранить адрес: {e}"))?;
    }
    *state.0.lock().unwrap() = Some(url.clone());
    if let Some(window) = app.get_webview_window(WINDOW) {
        window.navigate(url).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let back = MenuItem::with_id(app, "back", "Назад", true, None::<&str>)?;
    let reload = MenuItem::with_id(app, "reload", "Обновить", true, Some("F5"))?;
    let server = MenuItem::with_id(app, "server", "Сменить сервер…", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = PredefinedMenuItem::quit(app, Some("Выход"))?;
    let panel = Submenu::with_items(
        app,
        "Панель",
        true,
        &[&back, &reload, &server, &separator, &quit],
    )?;
    Menu::with_items(app, &[&panel])
}

fn on_menu(app: &AppHandle, id: &str) {
    let Some(window) = app.get_webview_window(WINDOW) else {
        return;
    };
    let _ = match id {
        "back" => window.eval("history.back()"),
        "reload" => window.reload(),
        "server" => window.navigate(setup_page()),
        _ => Ok(()),
    };
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![get_server, connect])
        .setup(|app| {
            let handle = app.handle().clone();
            let server = saved_server(&handle);
            let start = match &server {
                Some(url) => WebviewUrl::External(url.clone()),
                None => WebviewUrl::App("index.html".into()),
            };
            app.manage(Server(Mutex::new(server)));

            let opener = handle.clone();
            WebviewWindowBuilder::new(app, WINDOW, start)
                .title("Очередь публикаций")
                .inner_size(1280.0, 860.0)
                .min_inner_size(400.0, 500.0)
                // Ссылки «в новой вкладке» (например, «Открыть в TikTok») — в обычном браузере.
                .on_new_window(move |url, _features| {
                    if matches!(url.scheme(), "http" | "https") {
                        let _ = opener.opener().open_url(url.as_str(), None::<&str>);
                    }
                    NewWindowResponse::Deny
                })
                .build()?;

            app.set_menu(build_menu(&handle)?)?;
            app.on_menu_event(|app, event| on_menu(app, event.id().as_ref()));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("не удалось запустить приложение");
}

#[cfg(test)]
mod tests {
    use super::parse_server;

    #[test]
    fn adds_https_and_rejects_other_schemes() {
        assert_eq!(
            parse_server(" panel.example.com ").unwrap().as_str(),
            "https://panel.example.com/"
        );
        assert_eq!(
            parse_server("http://192.168.1.10:8000").unwrap().as_str(),
            "http://192.168.1.10:8000/"
        );
        assert!(parse_server("").is_err());
        assert!(parse_server("file:///C:/Windows").is_err());
        assert!(parse_server("javascript:alert(1)").is_err());
    }
}
