#!/usr/bin/env python3
"""Small Tkinter GUI for layoutcopy.py.

Place this file beside layoutcopy.py and run:
    python gui.pyw
Python 3.9+. Uses only the standard library.

Vibecoded with ChatGPT with manual review and tweaks.
"""
from __future__ import annotations

import queue
import threading
from contextlib import redirect_stderr, redirect_stdout
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import layoutcopy as transfer


class _QueueWriter:
    """Redirect print output from the worker thread into the GUI log."""

    def __init__(self, messages: queue.Queue[tuple[str, object]]) -> None:
        self.messages = messages

    def write(self, text: str) -> int:
        if text:
            self.messages.put(("log", text))
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class LayoutTransferGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Directory Layout Transfer")
        self.geometry("760x570")
        self.minsize(660, 500)

        self.mode = tk.StringVar(value="Export manifest")
        self.root_path = tk.StringVar()
        self.manifest_path = tk.StringVar()
        self.hash_algorithm = tk.StringVar(value=transfer.DEFAULT_HASH_ALGORITHM)
        self.execute_changes = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False

        self._build_ui()
        self._update_mode()
        self.after(100, self._drain_messages)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(5, weight=1)

        ttk.Label(outer, text="Operation").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        mode_box = ttk.Combobox(
            outer,
            textvariable=self.mode,
            values=("Export manifest", "Apply manifest"),
            state="readonly",
            width=22,
        )
        mode_box.grid(row=0, column=1, sticky="w", pady=5)
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self._update_mode())

        ttk.Label(outer, text="Root directory").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(outer, textvariable=self.root_path).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(outer, text="Browse…", command=self._browse_root).grid(row=1, column=2, padx=(8, 0), pady=5)

        ttk.Label(outer, text="Manifest file").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(outer, textvariable=self.manifest_path).grid(row=2, column=1, sticky="ew", pady=5)
        self.manifest_button = ttk.Button(outer, text="Browse…", command=self._browse_manifest)
        self.manifest_button.grid(row=2, column=2, padx=(8, 0), pady=5)

        self.hash_label = ttk.Label(outer, text="Hash algorithm (blake2b recommended)")  # recommend blake2b (don't wanna bother with tooltips)
        self.hash_label.grid(row=3, column=0, sticky="w", padx=(0, 10), pady=5)
        self.hash_box = ttk.Combobox(
            outer,
            textvariable=self.hash_algorithm,
            values=" ".join(transfer.SUPPORTED_HASH_ALGORITHMS),  # had to fix this
            state="readonly",
            width=22,
        )
        self.hash_box.grid(row=3, column=1, sticky="w", pady=5)

        self.execute_check = ttk.Checkbutton(
            outer,
            text="Actually move files when applying (otherwise dry run)",
            variable=self.execute_changes,
        )
        self.execute_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 10))

        log_frame = ttk.LabelFrame(outer, text="Output", padding=7)
        log_frame.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(0, 8))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(outer)
        bottom.grid(row=6, column=0, columnspan=3, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status).grid(row=0, column=0, sticky="w")
        self.run_button = ttk.Button(bottom, text="Run", command=self._run)
        self.run_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(bottom, text="Clear output", command=self._clear_log).grid(row=0, column=2, padx=(8, 0))

    def _update_mode(self) -> None:
        exporting = self.mode.get() == "Export manifest"
        self.hash_label.configure(state=("normal" if exporting else "disabled"))
        self.hash_box.configure(state=("readonly" if exporting else "disabled"))
        self.execute_check.configure(state=("disabled" if exporting else "normal"))
        self.manifest_button.configure(text=("Save as…" if exporting else "Browse…"))
        if exporting:
            self.status.set("Export creates a manifest outside the selected root directory.")
        else:
            self.status.set("Apply checks that the current files match the manifest before moving anything.")

    def _browse_root(self) -> None:
        current = self.root_path.get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        selected = filedialog.askdirectory(title="Choose root directory", initialdir=initial)
        if selected:
            self.root_path.set(selected)

    def _browse_manifest(self) -> None:
        current = self.manifest_path.get().strip()
        initialdir = str(Path(current).expanduser().parent) if current else str(Path.home())
        if self.mode.get() == "Export manifest":
            selected = filedialog.asksaveasfilename(
                title="Save manifest as",
                initialdir=initialdir,
                initialfile=Path(current).name if current else "layout-manifest.json",
                defaultextension=".json",
                filetypes=(("JSON manifest", "*.json"), ("All files", "*")),
            )
        else:
            selected = filedialog.askopenfilename(
                title="Choose manifest",
                initialdir=initialdir,
                filetypes=(("JSON manifest", "*.json"), ("All files", "*")),
            )
        if selected:
            self.manifest_path.set(selected)

    def _run(self) -> None:
        if self.running:
            return

        root_input = self.root_path.get().strip()
        manifest_input = self.manifest_path.get().strip()
        root_path = Path(root_input).expanduser() if root_input else None
        manifest_path = Path(manifest_input).expanduser() if manifest_input else None

        if root_path is None or not root_path.is_dir():
            messagebox.showerror("Invalid root directory", "Choose an existing root directory.", parent=self)
            return
        if manifest_path is None:
            messagebox.showerror("Missing manifest", "Choose a manifest file path.", parent=self)
            return

        mode = self.mode.get()
        execute = mode == "Apply manifest" and self.execute_changes.get()
        if mode == "Export manifest":
            if manifest_path.exists() and not messagebox.askyesno(
                "Overwrite manifest?",
                f"This file already exists:\n{manifest_path}\n\nOverwrite it?",
                parent=self,
            ):
                return
        elif not manifest_path.is_file():
            messagebox.showerror("Invalid manifest", "Choose an existing manifest file.", parent=self)
            return

        if execute:
            confirmed = messagebox.askyesno(
                "Confirm reorganization",
                "This will move files and replace the selected directory layout.\n\n"
                "The program verifies the complete file set first, but an I/O failure can still occur.\n\n"
                "Proceed with moving files?",
                icon="warning",
                parent=self,
            )
            if not confirmed:
                return

        root = str(root_path.resolve())
        manifest = str(manifest_path.resolve())
        algorithm = self.hash_algorithm.get()
        self._append_log(f"\n--- {mode} ---\nRoot: {root}\nManifest: {manifest}\n")
        if mode == "Export manifest":
            self._append_log(f"Hash algorithm: {algorithm}\n")
        elif execute:
            self._append_log("Execution confirmed; files will be moved if validation succeeds.\n")
        else:
            self._append_log("Dry run; no files will be moved.\n")

        self._set_running(True)
        threading.Thread(
            target=self._worker,
            args=(mode, root, manifest, algorithm, execute),
            daemon=True,
        ).start()

    def _worker(
        self, mode: str, root: str, manifest: str, algorithm: str, execute: bool
    ) -> None:
        writer = _QueueWriter(self.messages)
        try:
            # Call the imported module directly. In a PyInstaller build,
            # sys.executable points to the GUI executable itself, so starting
            # it as a child process would launch another copy of the GUI.
            with redirect_stdout(writer), redirect_stderr(writer):
                if mode == "Export manifest":
                    transfer.export_manifest(root, manifest, algorithm)
                else:
                    # The GUI has already shown and received confirmation.
                    transfer.apply_manifest(root, manifest, execute)
            self.messages.put(("done", 0))
        except Exception as exc:
            self.messages.put(("log", f"ERROR: {exc}\n"))
            self.messages.put(("done", 1))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    code = int(payload)
                    self._set_running(False)
                    if code == 0:
                        self.status.set("Finished successfully.")
                    else:
                        self.status.set(f"Finished with errors (exit code {code}). See output above.")
        except queue.Empty:
            pass
        self.after(100, self._drain_messages)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        if self.running:
            return
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.run_button.configure(state=("disabled" if running else "normal"))
        if running:
            self.status.set("Running…")

    def _on_close(self) -> None:
        if self.running:
            messagebox.showwarning(
                "Operation running",
                "Wait for the current operation to finish before closing this window.",
                parent=self,
            )
            return
        self.destroy()


if __name__ == "__main__":
    LayoutTransferGUI().mainloop()
