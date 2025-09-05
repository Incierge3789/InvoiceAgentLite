# launch.py
import os, sys, threading, time, webbrowser, pathlib, platform
import tkinter as tk
from tkinter import ttk, messagebox
import requests

# ---- ログ保存先（書き込み可能なユーザ領域）----
APP_NAME = "InvoiceAgentLite"
if platform.system() == "Windows":
    base_dir = os.getenv("LOCALAPPDATA", os.path.expanduser("~"))
    LOG_DIR = os.path.join(base_dir, APP_NAME, "logs")
else:
    LOG_DIR = os.path.join(os.path.expanduser("~"), "Library", "Logs", APP_NAME)
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "latest.log")

# ---- アプリ本体（Flask）をインポート ----
from waitress import create_server
from app import app  # app.py に Flask app がある前提

HOST = "127.0.0.1"
PORT = int(os.getenv("PORT", "5050"))

server = None
server_thread = None

def start_server():
    global server, server_thread
    server = create_server(app, host=HOST, port=PORT)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

def stop_server():
    try:
        if server:
            server.close()
    except Exception:
        pass

def health_ok(timeout=30):
    url = f"http://{HOST}:{PORT}/healthz"
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=1)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False

# ---- GUI ----
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("InvoiceAgent Lite Launcher")
        self.geometry("460x180")
        self.resizable(False, False)

        self.lbl = ttk.Label(self, text="準備中…（依存読み込み→サーバ起動→ヘルスチェック）")
        self.lbl.pack(pady=10)

        self.pb = ttk.Progressbar(self, mode="indeterminate")
        self.pb.pack(fill="x", padx=16, pady=6)
        self.pb.start(10)

        self.btn_open = ttk.Button(self, text="ブラウザで開く", command=self.open_ui, state="disabled")
        self.btn_retry = ttk.Button(self, text="再試行", command=self.retry, state="disabled")
        self.btn_quit  = ttk.Button(self, text="終了", command=self.quit_all)

        btns = ttk.Frame(self); btns.pack(pady=6)
        self.btn_open.pack(in_=btns, side="left", padx=6)
        self.btn_retry.pack(in_=btns, side="left", padx=6)
        self.btn_quit.pack(in_=btns, side="left", padx=6)

        self.after(100, self.boot)

    def boot(self):
        try:
            start_server()
            if health_ok(30):
                self.pb.stop()
                self.lbl.configure(text=f"起動完了： http://{HOST}:{PORT}/upload")
                self.btn_open.config(state="normal")
                webbrowser.open(f"http://{HOST}:{PORT}/upload")
            else:
                self.pb.stop()
                self.lbl.configure(text="起動に失敗しました。ログを開きます。")
                self.write_log("Health check failed")
                self.open_log()
                self.btn_retry.config(state="normal")
        except Exception as e:
            self.pb.stop()
            self.lbl.configure(text="起動エラー。ログを開きます。")
            self.write_log(f"Exception: {e}")
            self.open_log()
            self.btn_retry.config(state="normal")

    def retry(self):
        self.btn_retry.config(state="disabled")
        self.lbl.configure(text="再起動中…")
        stop_server()
        time.sleep(1)
        self.pb.start(10)
        self.after(100, self.boot)

    def open_ui(self):
        webbrowser.open(f"http://{HOST}:{PORT}/upload")

    def open_log(self):
        try:
            if platform.system() == "Darwin":
                os.system(f'open "{LOG_FILE}"')
            elif platform.system() == "Windows":
                os.startfile(LOG_FILE)
            else:
                os.system(f'xdg-open "{LOG_FILE}"')
        except Exception:
            pass

    def write_log(self, msg):
        pathlib.Path(LOG_FILE).write_text(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n", encoding="utf-8")

    def quit_all(self):
        stop_server()
        self.destroy()
        sys.exit(0)

if __name__ == "__main__":
    # Windows用：見た目を少し整える
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    Launcher().mainloop()
