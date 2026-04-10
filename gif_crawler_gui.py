#!/usr/bin/env python3
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
import sys
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from bookmark_gif_scraper import ScrapeConfig, run_scrape
from task_history import add_record, list_recent


class GifCrawlerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("GIF 爬虫 / GIF Crawler")
        self.root.geometry("1080x860")
        self.root.minsize(960, 740)

        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = False
        self.stop_requested = False
        self.app_data_dir = self._app_data_dir()
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        default_output = (Path.home() / "Desktop" / "scrape-report.html")
        if not default_output.parent.exists():
            default_output = Path.home() / "scrape-report.html"

        self.bookmark_path = tk.StringVar()
        self.cookie_file_path = tk.StringVar()
        self.blocked_json_path = tk.StringVar()
        self.output_path = tk.StringVar(value=str(default_output))
        self.asset_dir = tk.StringVar(value="gif-assets")
        self.cookie_text = tk.StringVar()

        self.crawl_site = tk.BooleanVar(value=False)
        self.disable_auto_cookie = tk.BooleanVar(value=False)

        self.max_workers = tk.StringVar(value="6")
        self.gif_workers = tk.StringVar(value="4")
        self.max_gifs = tk.StringVar(value="8")
        self.max_gif_mb = tk.StringVar(value="15")
        self.timeout = tk.StringVar(value="20")
        self.max_pages = tk.StringVar(value="30")

        self.progress_value = tk.DoubleVar(value=0.0)
        self.progress_text = tk.StringVar(value="进度 Progress: 0/0")
        self.summary_text = tk.StringVar(value="成功 Success: 0  失败 Failed: 0  GIF: 0")
        self.state_text = tk.StringVar(value="空闲 Idle")

        self.last_metrics = {"total": 0, "ok": 0, "failed": 0, "gif_total": 0}
        self.banner_frames: list[tk.PhotoImage] = []
        self.banner_idx = 0
        self.banner_delay_ms = 120

        self._build_ui()
        self._refresh_history()
        self.root.after(150, self._drain_events)

    def _resource_path(self, name: str) -> Path:
        if hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")) / name
        return Path(__file__).resolve().parent / name

    def _app_data_dir(self) -> Path:
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "THZH-GIF-Crawler"
        if sys.platform.startswith("win"):
            base = Path.home()
            return base / "AppData" / "Roaming" / "THZH-GIF-Crawler"
        return Path.home() / ".local" / "share" / "thzh-gif-crawler"

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        brand_group = ttk.Frame(frame)
        brand_group.pack(fill=tk.X, pady=(0, 8))
        self.banner_image_label = ttk.Label(brand_group)
        self.banner_image_label.pack(side=tk.LEFT, padx=(4, 10))
        brand_text = tk.Label(
            brand_group,
            text="THZH",
            font=("Helvetica", 26, "bold"),
            fg="#B5542F",
        )
        brand_text.pack(side=tk.LEFT, anchor=tk.CENTER)
        ttk.Label(
            brand_group,
            text="THZH GIF 爬虫 / THZH GIF Crawler",
            font=("Helvetica", 12),
        ).pack(side=tk.LEFT, padx=(8, 0), anchor=tk.CENTER)
        self._init_banner()

        input_group = ttk.LabelFrame(frame, text="输入 Input")
        input_group.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(input_group, text="网址 URLs (每行一个 / one per line):").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.url_text = tk.Text(input_group, height=5, wrap=tk.WORD)
        self.url_text.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=8, pady=(0, 8))

        ttk.Label(input_group, text="书签 HTML Bookmark file:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(input_group, textvariable=self.bookmark_path).grid(row=2, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Button(input_group, text="浏览 Browse", command=self._choose_bookmark).grid(row=2, column=2, padx=8, pady=6)
        input_group.columnconfigure(1, weight=1)

        auth_group = ttk.LabelFrame(frame, text="验证 / Cookie Verification")
        auth_group.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(auth_group, text="Cookie 字符串 Cookie string:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(auth_group, textvariable=self.cookie_text).grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=6)
        ttk.Label(auth_group, text="Cookie 文件 Cookie file:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(auth_group, textvariable=self.cookie_file_path).grid(row=1, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Button(auth_group, text="浏览 Browse", command=self._choose_cookie_file).grid(row=1, column=2, padx=8, pady=6)
        ttk.Checkbutton(
            auth_group,
            text="关闭简单验证自动 Cookie 重试 / Disable auto simple-cookie retry",
            variable=self.disable_auto_cookie,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(2, 8))
        auth_group.columnconfigure(1, weight=1)

        out_group = ttk.LabelFrame(frame, text="输出 Output")
        out_group.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(out_group, text="报告 HTML Report file:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(out_group, textvariable=self.output_path).grid(row=0, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Button(out_group, text="浏览 Browse", command=self._choose_output).grid(row=0, column=2, padx=8, pady=6)
        ttk.Label(out_group, text="资源目录 Asset folder:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(out_group, textvariable=self.asset_dir).grid(row=1, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Label(out_group, text="屏蔽清单 JSON Blocklist:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(out_group, textvariable=self.blocked_json_path).grid(row=2, column=1, sticky=tk.EW, padx=8, pady=6)
        ttk.Button(out_group, text="浏览 Browse", command=self._choose_blocked_json).grid(row=2, column=2, padx=8, pady=6)
        out_group.columnconfigure(1, weight=1)

        opts = ttk.LabelFrame(frame, text="高级 Advanced")
        opts.pack(fill=tk.X, pady=(0, 8))
        self._labeled_entry(opts, "页面线程 Page workers", self.max_workers, 0, 0)
        self._labeled_entry(opts, "GIF线程 GIF workers", self.gif_workers, 0, 2)
        self._labeled_entry(opts, "每页 GIF 上限 Max GIF/page", self.max_gifs, 1, 0)
        self._labeled_entry(opts, "单 GIF 大小(MB) Max GIF MB", self.max_gif_mb, 1, 2)
        self._labeled_entry(opts, "超时(秒) Timeout(s)", self.timeout, 2, 0)
        self._labeled_entry(opts, "站内扩展页数 Max crawl pages", self.max_pages, 2, 2)
        ttk.Checkbutton(opts, text="站内扩展爬取 Enable in-site crawling", variable=self.crawl_site).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, padx=8, pady=(4, 8)
        )

        action_row = ttk.Frame(frame)
        action_row.pack(fill=tk.X, pady=(0, 8))
        self.start_btn = ttk.Button(action_row, text="开始抓取 Start", command=self.start_crawl)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(action_row, text="停止 Stop", command=self.stop_crawl, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(action_row, text="打开报告 Open Report", command=self.open_report).pack(side=tk.LEFT, padx=8)
        ttk.Label(action_row, textvariable=self.state_text).pack(side=tk.LEFT, padx=12)

        feedback_group = ttk.LabelFrame(frame, text="任务反馈 Task Feedback")
        feedback_group.pack(fill=tk.X, pady=(0, 8))
        self.progress_bar = ttk.Progressbar(feedback_group, maximum=100, variable=self.progress_value)
        self.progress_bar.pack(fill=tk.X, padx=8, pady=(8, 6))
        ttk.Label(feedback_group, textvariable=self.progress_text).pack(anchor=tk.W, padx=8, pady=(0, 4))
        ttk.Label(feedback_group, textvariable=self.summary_text).pack(anchor=tk.W, padx=8, pady=(0, 8))

        history_group = ttk.LabelFrame(frame, text="任务历史 Task History")
        history_group.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        cols = ("created_at", "status", "total", "ok", "failed", "gif", "output")
        self.history_tree = ttk.Treeview(history_group, columns=cols, show="headings", height=8)
        headers = [
            ("created_at", "时间 Time", 150),
            ("status", "状态 Status", 110),
            ("total", "总数 Total", 80),
            ("ok", "成功 OK", 80),
            ("failed", "失败 Failed", 90),
            ("gif", "GIF 数 GIF", 90),
            ("output", "报告路径 Report", 360),
        ]
        for key, title, width in headers:
            self.history_tree.heading(key, text=title)
            self.history_tree.column(key, width=width, anchor=tk.W)
        self.history_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=(8, 0), pady=8)
        history_scroll = ttk.Scrollbar(history_group, orient=tk.VERTICAL, command=self.history_tree.yview)
        history_scroll.pack(side=tk.LEFT, fill=tk.Y, pady=8, padx=(0, 8))
        self.history_tree.configure(yscrollcommand=history_scroll.set)

        history_actions = ttk.Frame(frame)
        history_actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(history_actions, text="刷新历史 Refresh", command=self._refresh_history).pack(side=tk.LEFT)
        ttk.Button(history_actions, text="打开选中报告 Open Selected", command=self.open_selected_history_report).pack(
            side=tk.LEFT, padx=8
        )

        log_group = ttk.LabelFrame(frame, text="日志 Logs")
        log_group.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_group, height=10, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.log_text.configure(state=tk.DISABLED)

    def _init_banner(self) -> None:
        gif_path = self._resource_path("爱丽丝.gif")
        if not gif_path.exists():
            self.banner_image_label.configure(text="[爱丽丝.gif missing]")
            return
        frames: list[tk.PhotoImage] = []
        i = 0
        while True:
            try:
                frame = tk.PhotoImage(file=str(gif_path), format=f"gif -index {i}")
            except tk.TclError:
                break
            frames.append(frame)
            i += 1
        if not frames:
            self.banner_image_label.configure(text="[gif load failed]")
            return
        self.banner_frames = frames
        self.banner_idx = 0
        self.banner_image_label.configure(image=self.banner_frames[0], text="")
        self.root.after(self.banner_delay_ms, self._animate_banner)

    def _animate_banner(self) -> None:
        if not self.banner_frames:
            return
        self.banner_idx = (self.banner_idx + 1) % len(self.banner_frames)
        self.banner_image_label.configure(image=self.banner_frames[self.banner_idx])
        self.root.after(self.banner_delay_ms, self._animate_banner)

    def _labeled_entry(self, parent: ttk.LabelFrame, label: str, variable: tk.StringVar, row: int, col: int) -> None:
        ttk.Label(parent, text=label + ":").grid(row=row, column=col, sticky=tk.W, padx=8, pady=6)
        ttk.Entry(parent, textvariable=variable, width=12).grid(row=row, column=col + 1, sticky=tk.W, padx=(0, 8), pady=6)

    def _choose_bookmark(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("HTML files", "*.html *.htm"), ("All files", "*.*")])
        if path:
            self.bookmark_path.set(path)

    def _choose_cookie_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.cookie_file_path.set(path)

    def _choose_blocked_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.blocked_json_path.set(path)

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")])
        if path:
            self.output_path.set(path)

    def _parse_urls(self) -> list[str]:
        raw = self.url_text.get("1.0", tk.END)
        return [line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()]

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    self._handle_progress(payload if isinstance(payload, dict) else {})
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def _handle_progress(self, evt: dict[str, object]) -> None:
        stage = str(evt.get("stage", ""))
        if stage == "start":
            total = int(evt.get("total", 0))
            self.last_metrics = {"total": total, "ok": 0, "failed": 0, "gif_total": 0}
            self.progress_value.set(0.0)
            self.progress_text.set(f"进度 Progress: 0/{total}")
            self.summary_text.set("成功 Success: 0  失败 Failed: 0  GIF: 0")
            self.state_text.set("运行中 Running...")
            return
        if stage == "item":
            done = int(evt.get("done", 0))
            total = max(1, int(evt.get("total", 1)))
            ok = int(evt.get("ok", 0))
            failed = int(evt.get("failed", 0))
            gif_total = int(evt.get("gif_total", 0))
            self.last_metrics = {"total": total, "ok": ok, "failed": failed, "gif_total": gif_total}
            self.progress_value.set((done / total) * 100.0)
            self.progress_text.set(f"进度 Progress: {done}/{total}")
            self.summary_text.set(f"成功 Success: {ok}  失败 Failed: {failed}  GIF: {gif_total}")
            return
        if stage == "expanding":
            self.state_text.set("站内扩展 Crawling links...")
            return
        if stage == "cancelled":
            self.state_text.set("取消中 Cancelling...")
            return
        if stage == "done":
            total = int(evt.get("total", 0))
            ok = int(evt.get("ok", 0))
            failed = int(evt.get("failed", 0))
            gif_total = int(evt.get("gif_total", 0))
            self.last_metrics = {"total": total, "ok": ok, "failed": failed, "gif_total": gif_total}
            self.progress_value.set(100.0 if total else 0.0)
            self.progress_text.set(f"进度 Progress: {total}/{total}")
            self.summary_text.set(f"成功 Success: {ok}  失败 Failed: {failed}  GIF: {gif_total}")
            self.state_text.set("已完成 Completed")

    def _safe_int(self, value: str, name: str, minimum: int = 1) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是整数 / must be an integer") from exc
        if parsed < minimum:
            raise ValueError(f"{name} 必须 >= {minimum} / must be >= {minimum}")
        return parsed

    def start_crawl(self) -> None:
        if self.running:
            return
        try:
            urls = self._parse_urls()
            input_path = Path(self.bookmark_path.get()).expanduser().resolve() if self.bookmark_path.get().strip() else None
            output_path = Path(self.output_path.get()).expanduser().resolve()
            blocked_json = Path(self.blocked_json_path.get()).expanduser().resolve() if self.blocked_json_path.get().strip() else None
            cookie_file = Path(self.cookie_file_path.get()).expanduser().resolve() if self.cookie_file_path.get().strip() else None

            config = ScrapeConfig(
                input_path=input_path,
                urls=urls,
                output_path=output_path,
                asset_dir=self.asset_dir.get().strip() or "gif-assets",
                blocked_json=blocked_json,
                max_workers=self._safe_int(self.max_workers.get(), "页面线程 Page workers"),
                gif_workers=self._safe_int(self.gif_workers.get(), "GIF线程 GIF workers"),
                max_gifs=self._safe_int(self.max_gifs.get(), "每页 GIF 上限 Max GIF/page"),
                max_gif_mb=self._safe_int(self.max_gif_mb.get(), "单 GIF 大小 Max GIF MB"),
                timeout=self._safe_int(self.timeout.get(), "超时 Timeout"),
                cookie=self.cookie_text.get().strip(),
                cookie_file=cookie_file,
                disable_auto_simple_cookie=self.disable_auto_cookie.get(),
                crawl_site=self.crawl_site.get(),
                max_pages=self._safe_int(self.max_pages.get(), "站内扩展页数 Max crawl pages"),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("输入错误 / Invalid input", str(exc))
            return

        self.running = True
        self.stop_requested = False
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress_value.set(0.0)
        self.state_text.set("运行中 Running...")
        self._append_log("=== 任务开始 Task started ===")

        def _run() -> None:
            code = run_scrape(
                config,
                log=lambda msg: self.event_queue.put(("log", msg)),
                progress=lambda evt: self.event_queue.put(("progress", evt)),
                should_stop=lambda: self.stop_requested,
            )
            metrics = self.last_metrics.copy()
            if code == 130:
                status = "cancelled"
                note = "user_cancelled"
            else:
                status = "success" if code == 0 else "failed"
                note = ""
            add_record(
                base_dir=self.app_data_dir,
                status=status,
                total=int(metrics.get("total", 0)),
                ok_count=int(metrics.get("ok", 0)),
                failed_count=int(metrics.get("failed", 0)),
                gif_total=int(metrics.get("gif_total", 0)),
                output_path=str(config.output_path),
                note=note,
            )
            self.event_queue.put(("log", f"=== 任务结束 Finished (exit code: {code}) ==="))
            self.event_queue.put(("log", f"报告 Report: {config.output_path}"))
            self.root.after(0, lambda: self._finish_ui(code))

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()

    def stop_crawl(self) -> None:
        if not self.running:
            return
        self.stop_requested = True
        self.state_text.set("停止请求已发送 Stop requested...")
        self._append_log(">>> 请求停止任务 Stop requested.")

    def _finish_ui(self, code: int) -> None:
        self.running = False
        self.stop_requested = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self._refresh_history()
        if code == 0:
            self.state_text.set("已完成 Completed")
            messagebox.showinfo("完成 / Completed", "抓取完成，点击“打开报告”。\nCrawl completed. Click 'Open Report'.")
        elif code == 130:
            self.state_text.set("已取消 Cancelled")
            messagebox.showinfo("已取消 / Cancelled", "任务已取消。\nTask cancelled.")
        else:
            self.state_text.set("失败 Failed")
            messagebox.showwarning("带错误结束 / Completed with errors", "任务已结束但有错误，请查看日志。\nFinished with errors, please check logs.")

    def open_report(self) -> None:
        report = Path(self.output_path.get()).expanduser().resolve()
        if not report.exists():
            messagebox.showwarning("文件不存在 / Not found", "报告文件还不存在。\nReport file does not exist yet.")
            return
        webbrowser.open(report.as_uri())

    def _refresh_history(self) -> None:
        for row_id in self.history_tree.get_children():
            self.history_tree.delete(row_id)
        for row in list_recent(self.app_data_dir, limit=40):
            self.history_tree.insert(
                "",
                tk.END,
                iid=str(row["id"]),
                values=(
                    row["created_at"],
                    row["status"],
                    row["total"],
                    row["ok_count"],
                    row["failed_count"],
                    row["gif_total"],
                    row["output_path"],
                ),
            )

    def open_selected_history_report(self) -> None:
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("请选择 / Select one", "请先选中一条历史任务。\nPlease select one history record first.")
            return
        values = self.history_tree.item(selected[0], "values")
        if not values:
            return
        report_path = Path(str(values[6])).expanduser().resolve()
        if not report_path.exists():
            messagebox.showwarning("文件不存在 / Not found", f"报告不存在:\n{report_path}")
            return
        webbrowser.open(report_path.as_uri())


def main() -> int:
    try:
        root = tk.Tk()
        app = GifCrawlerApp(root)
        app._append_log("准备就绪 Ready. 输入 URL 或导入书签后点击“开始抓取 Start”。")
        root.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001
        err_path = Path.home() / "thzh-gif-crawler-startup-error.log"
        err_path.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror(
                "启动失败 / Startup Failed",
                f"程序启动失败，错误日志已写入:\n{err_path}\n\n{exc}",
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
