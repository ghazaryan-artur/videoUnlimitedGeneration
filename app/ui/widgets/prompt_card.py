"""PromptCard — editable draft. Has prompt + model + duration + aspect + audio
+ count (videos to generate) + per-card output folder.

On `Start batch`, a PromptDraft expands into N Jobs (PromptDraft.expand()).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import flet as ft

from app.core.job import PromptDraft
from app.paths import output_dir
from app.runway.models import all_profiles, by_task_type
from app.ui import theme


class PromptCard:
    """Owns a Container root; mutates the draft in place via callbacks."""

    def __init__(
        self,
        draft: PromptDraft,
        page: ft.Page,
        *,
        on_remove: Callable[[PromptDraft], None] | None = None,
        on_request_variations: Callable[[PromptDraft], None] | None = None,
        on_duplicate: Callable[[PromptDraft], None] | None = None,
    ) -> None:
        self.draft = draft
        self.page = page
        self.on_remove = on_remove
        self.on_request_variations = on_request_variations
        self.on_duplicate = on_duplicate

        # FilePicker — must be added to page.overlay
        self._picker = ft.FilePicker(on_result=self._on_path_picked)
        self.page.overlay.append(self._picker)

        # Build controls (cached so we can update them in place)
        self._controls = self._build_controls()
        self.root = ft.Container(content=self._render(), animate=ft.Animation(150, "easeOut"))

    @property
    def control(self) -> ft.Control:
        return self.root

    # ── building blocks ──────────────────────────────────────────

    def _build_controls(self) -> dict[str, ft.Control]:
        profile = by_task_type(self.draft.model_task_type)

        prompt = ft.TextField(
            label="Prompt",
            value=self.draft.prompt,
            multiline=True,
            min_lines=3,
            max_lines=8,
            max_length=profile.max_prompt_chars,
            counter_text=f"{len(self.draft.prompt)} / {profile.max_prompt_chars}",
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            on_change=self._on_prompt_change,
            text_size=13,
        )
        model = ft.Dropdown(
            label="Model",
            value=self.draft.model_task_type,
            options=[ft.dropdown.Option(p.task_type, p.display_name) for p in all_profiles()],
            on_change=self._on_model_change,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            width=200,
        )
        duration = ft.Dropdown(
            label="Duration",
            value=str(self.draft.duration),
            options=[ft.dropdown.Option(str(d), f"{d}s") for d in profile.durations],
            on_change=self._on_duration_change,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            width=120,
        )
        aspect = ft.Dropdown(
            label="Aspect",
            value=self.draft.aspect_ratio,
            options=[ft.dropdown.Option(r, r) for r in profile.aspect_ratios],
            on_change=self._on_aspect_change,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            width=120,
        )
        audio = ft.Switch(
            label="Audio",
            value=self.draft.audio,
            on_change=self._on_audio_change,
            active_color=theme.Colors.primary,
            visible=profile.supports_audio,
        )

        # Count input — number of videos to generate from this prompt
        count = ft.TextField(
            label="Videos",
            value=str(self.draft.count),
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.NumbersOnlyInputFilter(),
            width=110,
            text_align=ft.TextAlign.CENTER,
            on_change=self._on_count_change,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
            tooltip="How many videos to generate from this prompt",
        )

        # Output folder — web: text input; desktop: read-only label + Browse button
        is_web = getattr(self.page, "web", False)

        path_label = ft.Text(
            self._format_path_label(),
            size=12,
            color=theme.Colors.text_secondary,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=self.draft.output_dir or "default folder",
            expand=True,
            visible=not is_web,
        )
        path_input = ft.TextField(
            label="Output subfolder (inside downloads/)",
            value=self.draft.output_dir or "",
            hint_text="e.g. cats/cute  -  leave blank for downloads/ root",
            helper_text="Paths above downloads/ are not allowed and will be clamped.",
            on_change=self._on_path_input_change,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
            expand=True,
            visible=is_web,
        )
        browse_btn = ft.OutlinedButton(
            text="Browse",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._on_browse_click,
            visible=not is_web,
            style=ft.ButtonStyle(
                color={"": theme.Colors.text_primary},
                side={"": ft.BorderSide(1, theme.Colors.border)},
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=14, vertical=10),
                text_style=ft.TextStyle(weight=ft.FontWeight.W_500, size=12),
            ),
        )
        clear_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_color=theme.Colors.text_dim,
            tooltip="Reset to default folder",
            visible=self.draft.output_dir is not None and not is_web,
            on_click=self._on_clear_path,
            icon_size=14,
        )

        ai_btn = ft.IconButton(
            icon=ft.Icons.AUTO_AWESOME,
            icon_color=theme.Colors.primary,
            tooltip="Generate AI variations of this prompt",
            on_click=lambda _: (
                self.on_request_variations(self.draft) if self.on_request_variations else None
            ),
        )

        copy_prompt_btn = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            icon_color=theme.Colors.text_secondary,
            tooltip="Copy prompt text to clipboard",
            on_click=self._on_copy_prompt,
        )

        duplicate_btn = ft.IconButton(
            icon=ft.Icons.FILE_COPY,
            icon_color=theme.Colors.text_secondary,
            tooltip="Duplicate card with the same settings",
            on_click=lambda _: (self.on_duplicate(self.draft) if self.on_duplicate else None),
        )

        remove_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_color=theme.Colors.text_dim,
            tooltip="Remove this prompt",
            on_click=lambda _: (self.on_remove(self.draft) if self.on_remove else None),
        )

        return {
            "prompt": prompt,
            "model": model,
            "duration": duration,
            "aspect": aspect,
            "audio": audio,
            "count": count,
            "path_label": path_label,
            "path_input": path_input,
            "browse_btn": browse_btn,
            "clear_btn": clear_btn,
            "ai_btn": ai_btn,
            "copy_prompt_btn": copy_prompt_btn,
            "duplicate_btn": duplicate_btn,
            "remove_btn": remove_btn,
        }

    def _format_path_label(self) -> str:
        if self.draft.output_dir:
            p = Path(self.draft.output_dir)
            return f"📁 {p}"
        return f"📁 default ({output_dir()})"

    # ── render ───────────────────────────────────────────────────

    def _render(self) -> ft.Control:
        c = self._controls

        return theme.card_container(
            ft.Column(
                [
                    # Top: Draft pill + remove
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Row(
                                    [
                                        ft.Icon(ft.Icons.EDIT_NOTE, color=theme.Colors.primary, size=18),
                                        ft.Text(
                                            "Draft",
                                            size=12,
                                            weight=ft.FontWeight.W_600,
                                            color=theme.Colors.primary,
                                        ),
                                    ],
                                    spacing=4,
                                ),
                                bgcolor=theme.Colors.primary_dim,
                                border_radius=6,
                                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                            ),
                            ft.Container(expand=True),
                            c["ai_btn"],
                            c["copy_prompt_btn"],
                            c["duplicate_btn"],
                            c["remove_btn"],
                        ],
                    ),
                    ft.Container(height=8),
                    c["prompt"],
                    ft.Container(height=10),
                    # Generation params row
                    ft.Row(
                        [c["model"], c["duration"], c["aspect"], c["audio"], c["count"]],
                        spacing=12,
                        wrap=True,
                    ),
                    ft.Container(height=10),
                    # Path row — destination folder
                    ft.Container(
                        content=ft.Row(
                            [
                                c["path_label"],
                                c["path_input"],
                                c["clear_btn"],
                                c["browse_btn"],
                            ],
                            spacing=8,
                        ),
                        bgcolor=theme.Colors.surface_3,
                        border_radius=8,
                        padding=ft.padding.symmetric(horizontal=12, vertical=8),
                        border=ft.border.all(1, theme.Colors.border),
                    ),
                ],
                spacing=0,
            ),
            padding=ft.padding.all(16),
        )

    # ── change handlers ──────────────────────────────────────────

    def _on_prompt_change(self, e: ft.ControlEvent) -> None:
        self.draft.prompt = e.control.value or ""
        e.control.counter_text = (
            f"{len(self.draft.prompt)} / "
            f"{by_task_type(self.draft.model_task_type).max_prompt_chars}"
        )
        try:
            e.control.update()
        except Exception:
            pass

    def _on_model_change(self, e: ft.ControlEvent) -> None:
        self.draft.model_task_type = e.control.value
        new_profile = by_task_type(self.draft.model_task_type)
        if self.draft.duration not in new_profile.durations:
            self.draft.duration = new_profile.durations[0]
        if self.draft.aspect_ratio not in new_profile.aspect_ratios:
            self.draft.aspect_ratio = new_profile.aspect_ratios[0]
        # Rebuild — dropdown options for new model
        self._controls = self._build_controls()
        self.root.content = self._render()
        try:
            self.root.update()
        except Exception:
            pass

    def _on_duration_change(self, e: ft.ControlEvent) -> None:
        try:
            self.draft.duration = int(e.control.value)
        except (TypeError, ValueError):
            pass

    def _on_aspect_change(self, e: ft.ControlEvent) -> None:
        self.draft.aspect_ratio = e.control.value or self.draft.aspect_ratio

    def _on_audio_change(self, e: ft.ControlEvent) -> None:
        self.draft.audio = bool(e.control.value)

    def _on_count_change(self, e: ft.ControlEvent) -> None:
        try:
            n = int(e.control.value or "1")
            self.draft.count = max(1, min(50, n))  # clamp 1..50 to avoid abuse
        except (TypeError, ValueError):
            self.draft.count = 1

    def _on_browse_click(self, _: ft.ControlEvent) -> None:
        self._picker.get_directory_path(dialog_title="Choose output folder for this prompt")

    def _on_path_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.path:
            self.draft.output_dir = e.path
        self._refresh_path_row()

    def _on_path_input_change(self, e: ft.ControlEvent) -> None:
        v = (e.control.value or "").strip()
        self.draft.output_dir = v if v else None

    def _on_clear_path(self, _: ft.ControlEvent) -> None:
        self.draft.output_dir = None
        self._refresh_path_row()

    def _on_copy_prompt(self, _: ft.ControlEvent) -> None:
        text = (self.draft.prompt or "").strip()
        if not text:
            self.page.snack_bar = ft.SnackBar(
                ft.Text("Prompt is empty — nothing to copy."),
                bgcolor=theme.Colors.surface_2,
            )
            self.page.snack_bar.open = True
            self.page.update()
            return
        try:
            self.page.set_clipboard(text)
        except Exception:
            pass
        self.page.snack_bar = ft.SnackBar(
            ft.Text("Prompt copied to clipboard."),
            bgcolor=theme.Colors.surface_2,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _refresh_path_row(self) -> None:
        c = self._controls
        c["path_label"].value = self._format_path_label()
        c["path_label"].tooltip = self.draft.output_dir or "default folder"
        c["clear_btn"].visible = self.draft.output_dir is not None
        try:
            c["path_label"].update()
            c["clear_btn"].update()
        except Exception:
            pass
