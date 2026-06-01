"""Main Jobs view — drafts (editable) at the top, live jobs below."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import flet as ft
from loguru import logger

from app.core.ai_variations import (
    VariationRequest,
    generate_variations,
    get_stored_api_key,
    store_api_key,
)
from app.core.importer import import_file
from app.core.job import Job, JobStatus, PromptDraft
from app.core.notify import (
    cancel_shutdown,
    schedule_shutdown,
    show_toast,
)
from app.core.queue import BatchRunner
from app.core.storage import (
    delete_terminal_jobs,
    find_job_by_runway_id,
    list_recent_jobs,
    list_unfinished_jobs,
    make_db_log_sink,
    task_id_from_url,
)
from app.core.templates import (
    Template,
    add_template,
    delete_template,
    list_templates,
)
from app.paths import is_web_mode, output_dir
from app.runway.auth import clear_token
from app.runway.models import SEEDANCE_2
from app.security.license_ledger import has_private_key
from app.ui import theme
from app.ui.state import AppState
from app.ui.widgets.job_card import JobCard
from app.ui.widgets.prompt_card import PromptCard


def _make_default_draft() -> PromptDraft:
    return PromptDraft(
        model_task_type=SEEDANCE_2.task_type,
        prompt="",
        duration=15,
        aspect_ratio="9:16",
        resolution="720p",
        audio=True,
        count=1,
    )


def _open_folder(path: str | Path) -> None:
    p = Path(path)
    folder = p.parent if p.is_file() else p
    try:
        if sys.platform == "win32":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not open folder {}: {}", folder, e)


# Live-timer refresh interval for active cards (queue/generating elapsed)
TIMER_INTERVAL_SECONDS = 1.0


def _empty_state(title: str, subtitle: str, icon: str) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(icon, size=44, color=theme.Colors.text_dim),
                ft.Container(height=10),
                ft.Text(title, size=14, color=theme.Colors.text_secondary),
                ft.Text(subtitle, size=12, color=theme.Colors.text_dim),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.alignment.center,
        padding=ft.padding.symmetric(vertical=60),
    )


def build_jobs_view(page: ft.Page, state: AppState) -> ft.View:
    # ── header bits ────────────────────────────────────────────────

    user_label = ft.Text(
        f"Signed in as {state.token.email}" if state.token and state.token.email else "",
        size=12,
        color=theme.Colors.text_dim,
    )

    summary_label = ft.Text(
        "No prompts yet",
        size=14,
        weight=ft.FontWeight.W_500,
        color=theme.Colors.text_secondary,
    )

    batch_progress = ft.ProgressBar(
        value=0,
        color=theme.Colors.primary,
        bgcolor=theme.Colors.surface_3,
        bar_height=4,
        width=300,
        visible=False,
    )

    # ── card containers (two tabs) ────────────────────────────────

    active_column = ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO)
    completed_column = ft.Column(spacing=14, expand=True, scroll=ft.ScrollMode.AUTO)
    prompt_card_index: dict[str, PromptCard] = {}
    job_card_index: dict[str, JobCard] = {}
    completed_jobs_cache: list[Job] = []  # snapshot of completed-from-DB

    # ── batch-completion tracking ─────────────────────────────────
    # We fire a toast (and optionally schedule shutdown) the moment we go
    # from "any-active" to "zero-active" with at least one terminal in this
    # session. `_was_active` lets us detect that edge.
    completion_state: dict[str, bool | int] = {
        "was_active": False,                      # had active jobs in last tick?
        "auto_shutdown_enabled": False,           # toggled in header
        "shutdown_dialog_open": False,            # countdown dialog showing?
    }

    # ── helpers ────────────────────────────────────────────────────

    def remove_draft(draft: PromptDraft) -> None:
        if draft in state.drafts:
            state.drafts.remove(draft)
        rebuild()

    def duplicate_draft(draft: PromptDraft) -> None:
        copy = draft.clone()
        try:
            idx = state.drafts.index(draft)
            state.drafts.insert(idx + 1, copy)
        except ValueError:
            state.drafts.append(copy)
        rebuild()

    def _toast(msg: str) -> None:
        page.snack_bar = ft.SnackBar(
            ft.Text(msg),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()

    def copy_job_prompt(job: Job) -> None:
        text = (job.prompt or "").strip()
        if not text:
            _toast("Prompt is empty — nothing to copy.")
            return
        try:
            page.set_clipboard(text)
        except Exception:
            pass
        _toast("Prompt copied to clipboard.")

    def duplicate_job_as_draft(job: Job) -> None:
        """Turn a completed/active/failed job back into an editable draft.

        Same prompt + model + duration + aspect + resolution + audio +
        output_dir. count is reset to 1 (each Job already represents one
        rendered video; the per-card #N suffix is stripped from name).
        """
        bare_name = (job.name or "").split(" #")[0].strip() or None
        new_draft = PromptDraft(
            prompt=job.prompt,
            model_task_type=job.model_task_type,
            duration=job.duration,
            aspect_ratio=job.aspect_ratio,
            resolution=job.resolution,
            audio=job.audio,
            name=bare_name,
            output_dir=job.output_dir,
            count=1,
        )
        state.drafts.append(new_draft)
        # Jump to the Active tab so the user immediately sees the new draft —
        # matches the behaviour of the same button on the /downloads page,
        # which routes back to /jobs.
        try:
            tabs.selected_index = 0
            tabs.update()
        except Exception:
            pass
        rebuild()
        _toast("Card duplicated — your new draft is at the bottom of Active.")

    # ── AI variations dialog (Claude API) ──────────────────────────

    def open_ai_variations_dialog(source_draft: PromptDraft) -> None:
        if not source_draft.prompt.strip():
            page.snack_bar = ft.SnackBar(
                ft.Text("Add some prompt text first."),
                bgcolor=theme.Colors.surface_2,
            )
            page.snack_bar.open = True
            page.update()
            return

        # If no API key, show key-setup form first
        if get_stored_api_key() is None:
            _open_api_key_setup(source_draft)
            return
        _open_variations_form(source_draft)

    def _open_api_key_setup(source_draft: PromptDraft) -> None:
        key_field = ft.TextField(
            label="Anthropic API key",
            password=True,
            can_reveal_password=True,
            hint_text="sk-ant-...",
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
        )
        error_text = ft.Text("", size=12, color=theme.Colors.state_failed)

        def save_and_continue(_: ft.ControlEvent) -> None:
            v = (key_field.value or "").strip()
            if not v.startswith("sk-ant-"):
                error_text.value = "Key should start with 'sk-ant-'."
                error_text.update()
                return
            store_api_key(v)
            page.close(dialog)
            _open_variations_form(source_draft)

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Text(
                "Claude API key needed",
                color=theme.Colors.text_primary, size=16, weight=ft.FontWeight.W_600,
            ),
            content=ft.Container(
                width=500,
                content=ft.Column(
                    [
                        ft.Text(
                            "AI variations are powered by Claude (Anthropic). "
                            "Paste your API key once — it's stored locally and never "
                            "sent anywhere except api.anthropic.com.",
                            size=12, color=theme.Colors.text_dim,
                        ),
                        ft.Container(height=8),
                        ft.Text(
                            "Get a key:  console.anthropic.com → Settings → API Keys",
                            size=11, color=theme.Colors.text_dim, font_family="monospace",
                        ),
                        ft.Container(height=14),
                        key_field,
                        ft.Container(height=6),
                        error_text,
                    ],
                    spacing=0, tight=True,
                ),
            ),
            actions=[
                ft.Row(
                    [
                        theme.secondary_button("Cancel", on_click=lambda _e: page.close(dialog)),
                        theme.primary_button(
                            "Save & continue", on_click=save_and_continue,
                            icon=ft.Icons.KEY,
                        ),
                    ],
                    spacing=4,
                ),
            ],
        )
        page.open(dialog)

    def _open_variations_form(source_draft: PromptDraft) -> None:
        from app.runway.models import all_profiles, by_task_type

        # Mutable settings — initialized from source draft, user can override
        out_settings = {
            "model_task_type": source_draft.model_task_type,
            "duration": source_draft.duration,
            "aspect_ratio": source_draft.aspect_ratio,
            "resolution": source_draft.resolution,
            "audio": source_draft.audio,
            "videos_per_variation": 1,
            "output_dir": source_draft.output_dir,  # may be None → uses app default
        }

        # FilePicker for output folder — registered to page overlay
        var_dir_picker = ft.FilePicker()
        page.overlay.append(var_dir_picker)

        count_field = ft.TextField(
            label="Number of variations",
            value="3",
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.NumbersOnlyInputFilter(),
            width=180,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
        )
        instructions_field = ft.TextField(
            label="Custom instructions (optional)",
            multiline=True,
            min_lines=2,
            max_lines=4,
            hint_text="e.g. \"vary character clothing only, keep faces and setting identical\"",
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
        )

        # Output settings controls — applied to each new draft when added
        # Built once but rebuilt when model changes (since duration/aspect options differ per model)
        settings_row = ft.Row([], spacing=10, wrap=True)

        # Folder row — re-rendered as needed
        folder_path_label = ft.Text(
            "",
            size=12,
            color=theme.Colors.text_secondary,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )

        def _update_folder_label() -> None:
            if out_settings["output_dir"]:
                folder_path_label.value = f"📁 {out_settings['output_dir']}"
                folder_path_label.tooltip = out_settings["output_dir"]
                folder_clear_btn.visible = True
            else:
                folder_path_label.value = f"📁 default ({output_dir()})"
                folder_path_label.tooltip = "default folder"
                folder_clear_btn.visible = False
            try:
                folder_path_label.update()
                folder_clear_btn.update()
            except Exception:
                pass

        def _on_folder_picked(e: ft.FilePickerResultEvent) -> None:
            if e.path:
                out_settings["output_dir"] = e.path
                _update_folder_label()

        var_dir_picker.on_result = _on_folder_picked

        def _on_browse_folder(_: ft.ControlEvent) -> None:
            var_dir_picker.get_directory_path(
                dialog_title="Choose output folder for these variations",
            )

        def _on_clear_folder(_: ft.ControlEvent) -> None:
            out_settings["output_dir"] = None
            _update_folder_label()

        folder_browse_btn = ft.OutlinedButton(
            text="Browse",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=_on_browse_folder,
            style=ft.ButtonStyle(
                color={"": theme.Colors.text_primary},
                side={"": ft.BorderSide(1, theme.Colors.border)},
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=14, vertical=10),
                text_style=ft.TextStyle(weight=ft.FontWeight.W_500, size=12),
            ),
        )
        folder_clear_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_color=theme.Colors.text_dim,
            tooltip="Reset to default folder",
            visible=False,
            on_click=_on_clear_folder,
            icon_size=14,
        )

        def _build_settings_controls() -> None:
            profile = by_task_type(out_settings["model_task_type"])

            # Snap invalid values to first allowed for the new model
            if out_settings["duration"] not in profile.durations:
                out_settings["duration"] = profile.durations[0]
            if out_settings["aspect_ratio"] not in profile.aspect_ratios:
                out_settings["aspect_ratio"] = profile.aspect_ratios[0]

            def on_model_change(e: ft.ControlEvent) -> None:
                out_settings["model_task_type"] = e.control.value
                _build_settings_controls()
                try:
                    settings_row.update()
                except Exception:
                    pass

            def on_duration_change(e: ft.ControlEvent) -> None:
                try:
                    out_settings["duration"] = int(e.control.value)
                except (TypeError, ValueError):
                    pass

            def on_aspect_change(e: ft.ControlEvent) -> None:
                out_settings["aspect_ratio"] = e.control.value or out_settings["aspect_ratio"]

            def on_audio_change(e: ft.ControlEvent) -> None:
                out_settings["audio"] = bool(e.control.value)

            def on_count_change(e: ft.ControlEvent) -> None:
                try:
                    n = int(e.control.value or "1")
                    out_settings["videos_per_variation"] = max(1, min(50, n))
                except (TypeError, ValueError):
                    out_settings["videos_per_variation"] = 1

            model_dd = ft.Dropdown(
                label="Model",
                value=out_settings["model_task_type"],
                options=[ft.dropdown.Option(p.task_type, p.display_name) for p in all_profiles()],
                on_change=on_model_change,
                bgcolor=theme.Colors.surface_2,
                border_color=theme.Colors.border,
                focused_border_color=theme.Colors.primary,
                color=theme.Colors.text_primary,
                label_style=ft.TextStyle(color=theme.Colors.text_secondary),
                width=180,
            )
            duration_dd = ft.Dropdown(
                label="Duration",
                value=str(out_settings["duration"]),
                options=[ft.dropdown.Option(str(d), f"{d}s") for d in profile.durations],
                on_change=on_duration_change,
                bgcolor=theme.Colors.surface_2,
                border_color=theme.Colors.border,
                focused_border_color=theme.Colors.primary,
                color=theme.Colors.text_primary,
                label_style=ft.TextStyle(color=theme.Colors.text_secondary),
                width=120,
            )
            aspect_dd = ft.Dropdown(
                label="Aspect",
                value=out_settings["aspect_ratio"],
                options=[ft.dropdown.Option(r, r) for r in profile.aspect_ratios],
                on_change=on_aspect_change,
                bgcolor=theme.Colors.surface_2,
                border_color=theme.Colors.border,
                focused_border_color=theme.Colors.primary,
                color=theme.Colors.text_primary,
                label_style=ft.TextStyle(color=theme.Colors.text_secondary),
                width=120,
            )
            audio_sw = ft.Switch(
                label="Audio",
                value=out_settings["audio"],
                on_change=on_audio_change,
                active_color=theme.Colors.primary,
            )
            videos_per_var = ft.TextField(
                label="Videos / variation",
                value=str(out_settings["videos_per_variation"]),
                keyboard_type=ft.KeyboardType.NUMBER,
                input_filter=ft.NumbersOnlyInputFilter(),
                width=140,
                text_align=ft.TextAlign.CENTER,
                on_change=on_count_change,
                bgcolor=theme.Colors.surface_2,
                border_color=theme.Colors.border,
                focused_border_color=theme.Colors.primary,
                color=theme.Colors.text_primary,
                label_style=ft.TextStyle(color=theme.Colors.text_secondary),
                cursor_color=theme.Colors.primary,
                text_size=13,
                tooltip="How many videos to render per variation",
            )

            settings_row.controls = [model_dd, duration_dd, aspect_dd, audio_sw, videos_per_var]

        _build_settings_controls()

        progress = ft.ProgressRing(
            width=18, height=18, stroke_width=2,
            color=theme.Colors.primary, visible=False,
        )
        status_text = ft.Text("", size=12, color=theme.Colors.text_secondary, selectable=True)

        results_column = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)
        results_panel = ft.Container(
            content=results_column,
            visible=False,
            height=240,
            bgcolor=theme.Colors.surface_2,
            border=ft.border.all(1, theme.Colors.border),
            border_radius=8,
            padding=ft.padding.all(8),
        )

        generated_variations: list[str] = []

        async def do_generate(_: ft.ControlEvent) -> None:
            try:
                count = int(count_field.value or "3")
            except ValueError:
                count = 3
            count = max(1, min(20, count))

            req = VariationRequest(
                source_prompt=source_draft.prompt,
                count=count,
                custom_instructions=(instructions_field.value or "").strip() or None,
            )

            generate_btn.disabled = True
            add_btn.disabled = True
            progress.visible = True
            status_text.value = "Calling Claude…"
            status_text.color = theme.Colors.text_secondary
            results_panel.visible = False
            results_column.controls.clear()
            page.update()

            try:
                result = await generate_variations(req)
            except Exception as exc:  # noqa: BLE001
                status_text.value = f"Error: {exc}"
                status_text.color = theme.Colors.state_failed
                logger.exception("AI variations failed: {}", exc)
                generate_btn.disabled = False
                progress.visible = False
                page.update()
                return

            generated_variations.clear()
            generated_variations.extend(result.variations)

            cache_marker = "(cache hit)" if result.cache_hit else "(first run — system prompt cached for next time)"
            status_text.value = (
                f"Generated {len(result.variations)} variations  •  "
                f"in: {result.input_tokens} tok  •  "
                f"out: {result.output_tokens} tok  •  "
                f"cache_read: {result.cache_read_tokens}  {cache_marker}"
            )
            status_text.color = theme.Colors.state_done

            for i, v in enumerate(result.variations, start=1):
                preview = v[:280] + ("…" if len(v) > 280 else "")
                results_column.controls.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(
                                    f"#{i}", size=11, weight=ft.FontWeight.W_700,
                                    color=theme.Colors.primary,
                                ),
                                ft.Text(
                                    preview, size=12,
                                    color=theme.Colors.text_secondary,
                                    selectable=True,
                                ),
                            ],
                            spacing=2,
                        ),
                        bgcolor=theme.Colors.surface_3,
                        border_radius=6,
                        padding=ft.padding.all(8),
                    )
                )
            results_panel.visible = True
            generate_btn.disabled = False
            add_btn.disabled = False
            progress.visible = False
            page.update()

        def add_as_drafts(_: ft.ControlEvent) -> None:
            if not generated_variations:
                return
            # Apply user-chosen output settings (which default to source draft values)
            for v in generated_variations:
                new_draft = PromptDraft(
                    prompt=v,
                    model_task_type=out_settings["model_task_type"],
                    duration=int(out_settings["duration"]),
                    aspect_ratio=out_settings["aspect_ratio"],
                    resolution=out_settings["resolution"],
                    audio=bool(out_settings["audio"]),
                    count=int(out_settings["videos_per_variation"]),
                    output_dir=out_settings["output_dir"],
                )
                state.drafts.append(new_draft)
            total_videos = len(generated_variations) * int(out_settings["videos_per_variation"])
            page.close(dialog)
            rebuild()
            page.snack_bar = ft.SnackBar(
                ft.Text(
                    f"Added {len(generated_variations)} drafts "
                    f"({total_videos} videos total)."
                ),
                bgcolor=theme.Colors.surface_2,
            )
            page.snack_bar.open = True
            page.update()

        generate_btn = theme.primary_button(
            "Generate", on_click=do_generate, icon=ft.Icons.AUTO_AWESOME,
        )
        add_btn = ft.FilledButton(
            text="Add all as drafts",
            icon=ft.Icons.PLAYLIST_ADD,
            on_click=add_as_drafts,
            disabled=True,
            style=ft.ButtonStyle(
                color={"": theme.Colors.bg},
                bgcolor={"": theme.Colors.state_done},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.AUTO_AWESOME, color=theme.Colors.primary, size=22),
                    ft.Text(
                        "Generate prompt variations (Claude)",
                        color=theme.Colors.text_primary, size=16, weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                width=620,
                content=ft.Column(
                    [
                        ft.Text(
                            "Claude will produce N variations of this prompt — same scene, "
                            "same actions, different cast / wardrobe / decor. The system prompt "
                            "is cached, so the second run in 5 minutes is much cheaper.",
                            size=12, color=theme.Colors.text_dim,
                        ),
                        ft.Container(height=14),
                        ft.Container(
                            content=ft.Text(
                                source_draft.prompt[:300]
                                + ("…" if len(source_draft.prompt) > 300 else ""),
                                size=12,
                                color=theme.Colors.text_secondary,
                                selectable=True,
                                font_family="monospace",
                            ),
                            bgcolor=theme.Colors.surface_2,
                            border_radius=6,
                            padding=ft.padding.all(10),
                        ),
                        ft.Container(height=14),
                        ft.Row([count_field], spacing=12),
                        ft.Container(height=10),
                        instructions_field,
                        ft.Container(height=14),
                        # Output settings — applied to each new draft
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        "OUTPUT SETTINGS  (applied to each new draft)",
                                        size=10,
                                        weight=ft.FontWeight.W_700,
                                        color=theme.Colors.text_dim,
                                    ),
                                    ft.Container(height=8),
                                    settings_row,
                                    ft.Container(height=10),
                                    ft.Container(
                                        content=ft.Row(
                                            [folder_path_label, folder_clear_btn, folder_browse_btn],
                                            spacing=8,
                                        ),
                                        bgcolor=theme.Colors.surface_3,
                                        border=ft.border.all(1, theme.Colors.border),
                                        border_radius=6,
                                        padding=ft.padding.symmetric(horizontal=10, vertical=6),
                                    ),
                                ],
                                spacing=0,
                                tight=True,
                            ),
                            bgcolor=theme.Colors.surface_2,
                            border=ft.border.all(1, theme.Colors.border),
                            border_radius=8,
                            padding=ft.padding.all(12),
                        ),
                        ft.Container(height=10),
                        ft.Row([progress, status_text], spacing=8),
                        ft.Container(height=10),
                        results_panel,
                    ],
                    spacing=0, tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.Row(
                    [
                        theme.secondary_button("Close", on_click=lambda _e: page.close(dialog)),
                        ft.Container(expand=True),
                        add_btn,
                        generate_btn,
                    ],
                    spacing=4,
                ),
            ],
        )
        page.open(dialog)
        _update_folder_label()

    def cancel_one_job(job: Job) -> None:
        """Force-cancel a job — works regardless of runner / worker state.

        Two paths:
          - PENDING and never sent to Runway (no runway_task_id, not in
            flight): PURGE — delete row + card entirely, no Runway call,
            no CANCELLED record left behind.
          - Anything else: graceful force_cancel — DELETE on Runway if a
            task_id is known, mark CANCELLED locally.
        """
        logger.info(
            "cancel_one_job clicked  id={} status={} runway_task_id={}",
            job.id, job.status.value, job.runway_task_id,
        )

        # Purge path: job never reached Runway and no worker is on it yet.
        if (
            job.status == JobStatus.PENDING
            and not job.runway_task_id
            and (state.runner is None or not state.runner.is_inflight(job.id))
        ):
            async def _do_purge() -> None:
                from app.core import storage
                logger.info("cancel_one_job: purging PENDING job {}", job.id)
                if state.runner is not None:
                    state.runner.purge_pending(job.id)
                try:
                    await storage.delete_job(job.id)
                except Exception as e:  # noqa: BLE001
                    logger.warning("purge: delete_job failed: {}", e)
                state.active_jobs.pop(job.id, None)
                rebuild()
            page.run_task(_do_purge)
            return

        async def _do() -> None:
            logger.info("cancel_one_job _do running  id={}", job.id)
            from datetime import datetime, timezone
            from app.core import storage
            from app.runway.tasks import cancel_task

            # Re-create runner+client if they were nulled by a previous
            # teardown — this also gives us a fresh client for cancel_task.
            ensure_runner()

            if state.runner is not None:
                # Happy path: runner exists. force_cancel handles both
                # the Runway DELETE and the local CANCELLED transition.
                await state.runner.force_cancel(job)
            else:
                # No runner could be created (no token?). Still try to
                # stop the task on Runway, then mark CANCELLED locally.
                logger.warning("cancel_one_job: state.runner still None — manual fallback")
                if job.runway_task_id and state.client is not None:
                    try:
                        await cancel_task(state.client, job.runway_task_id)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("manual cancel_task failed: {}", e)
                job.status = JobStatus.CANCELLED
                job.completed_at = datetime.now(timezone.utc)
                try:
                    await storage.upsert_job(job)
                except Exception as e:  # noqa: BLE001
                    logger.warning("storage upsert in cancel_one_job: {}", e)
                state.emit_job_update(job)
            rebuild()
        page.run_task(_do)

    def open_for_job(job: Job) -> None:
        # In a hosted web deployment, _open_folder() runs xdg-open on the
        # server — useless for the user. Send them to the /downloads listing
        # instead, where every saved mp4 has a real browser download link.
        if is_web_mode():
            page.go("/downloads")
            return
        _open_folder(job.output_path or job.output_dir or output_dir())

    def is_terminal(j: Job) -> bool:
        return j.status.is_terminal

    def rebuild() -> None:
        # ACTIVE TAB: drafts + non-terminal jobs
        active_column.controls.clear()
        prompt_card_index.clear()
        job_card_index.clear()

        active_jobs_sorted = sorted(
            (j for j in state.active_jobs.values() if not is_terminal(j)),
            key=lambda j: j.created_at,
        )
        for j in active_jobs_sorted:
            card = JobCard(
                j, page,
                on_open_folder=open_for_job,
                on_cancel=cancel_one_job,
                on_copy_prompt=copy_job_prompt,
                on_duplicate_as_draft=duplicate_job_as_draft,
            )
            job_card_index[j.id] = card
            active_column.controls.append(card.control)

        for d in state.drafts:
            pc = PromptCard(
                d, page,
                on_remove=remove_draft,
                on_request_variations=open_ai_variations_dialog,
                on_duplicate=duplicate_draft,
            )
            prompt_card_index[d.id] = pc
            active_column.controls.append(pc.control)

        if not active_column.controls:
            active_column.controls.append(_empty_state(
                "No active prompts.",
                "Click “Add prompt” to start composing.",
                ft.Icons.AUTO_AWESOME,
            ))

        # COMPLETED TAB: terminal in active_jobs + cached from DB
        completed_column.controls.clear()
        # Combine active_jobs[terminal] + completed_jobs_cache, dedupe by id
        seen_ids: set[str] = set()
        merged: list[Job] = []
        for j in state.active_jobs.values():
            if is_terminal(j) and j.id not in seen_ids:
                merged.append(j)
                seen_ids.add(j.id)
        for j in completed_jobs_cache:
            if j.id not in seen_ids:
                merged.append(j)
                seen_ids.add(j.id)
        merged.sort(key=lambda j: j.created_at, reverse=True)

        for j in merged:
            card = JobCard(
                j, page,
                on_open_folder=open_for_job,
                on_copy_prompt=copy_job_prompt,
                on_duplicate_as_draft=duplicate_job_as_draft,
            )
            job_card_index[j.id] = card
            completed_column.controls.append(card.control)

        if not completed_column.controls:
            completed_column.controls.append(_empty_state(
                "No completed jobs yet.",
                "Once jobs finish (success or failure) they'll show up here.",
                ft.Icons.HISTORY_TOGGLE_OFF,
            ))

        completed_count_label.value = f"{len(merged)} completed"
        clear_completed_btn.disabled = len(merged) == 0
        active_count_label.value = f"{sum(1 for j in active_jobs_sorted)} active  •  {len(state.drafts)} drafts"

        # Detect "batch finished" edge: was_active=True → now zero active
        any_active_now = any(j.status.is_active for j in state.active_jobs.values())
        any_terminal_in_session = any(j.status.is_terminal for j in state.active_jobs.values())
        if completion_state["was_active"] and not any_active_now and any_terminal_in_session:
            on_batch_completion_edge()
        completion_state["was_active"] = any_active_now

        update_summary()
        try:
            page.update()
        except Exception:
            pass

    # ── batch-completion notifications + optional auto-shutdown ───

    SHUTDOWN_GRACE_SECONDS = 5 * 60   # 5 minutes

    async def _shutdown_countdown_dialog(secs_total: int) -> None:
        """Modal dialog that counts down before shutdown, lets the user cancel."""
        countdown_text = ft.Text(
            "",
            size=32,
            weight=ft.FontWeight.W_700,
            color=theme.Colors.text_primary,
            text_align=ft.TextAlign.CENTER,
        )
        explainer = ft.Text(
            "All batch jobs are finished. The PC will shut down soon.\n"
            "Click \"Cancel\" if you want to stay logged in.",
            size=12,
            color=theme.Colors.text_secondary,
            text_align=ft.TextAlign.CENTER,
        )

        cancelled = asyncio.Event()

        def cancel(_e: ft.ControlEvent | None = None) -> None:
            cancelled.set()
            try:
                cancel_shutdown()
            except Exception:
                pass
            page.close(dialog)
            completion_state["shutdown_dialog_open"] = False
            page.snack_bar = ft.SnackBar(
                ft.Text("Shutdown cancelled."),
                bgcolor=theme.Colors.surface_2,
            )
            page.snack_bar.open = True
            page.update()

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.POWER_SETTINGS_NEW, color=theme.Colors.state_failed, size=24),
                    ft.Text("Shutting down soon",
                            color=theme.Colors.text_primary, size=16, weight=ft.FontWeight.W_700),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Container(height=10),
                        countdown_text,
                        ft.Container(height=8),
                        explainer,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                width=420,
            ),
            actions=[
                ft.Row(
                    [
                        ft.Container(expand=True),
                        theme.primary_button(
                            "Cancel shutdown",
                            on_click=cancel,
                            icon=ft.Icons.CANCEL,
                        ),
                    ],
                ),
            ],
        )
        page.open(dialog)

        # Trigger Windows shutdown (cancellable via `shutdown /a`)
        scheduled = schedule_shutdown(secs_total)
        if not scheduled:
            countdown_text.value = "(shutdown not supported on this OS)"
            countdown_text.update()
            return

        deadline = asyncio.get_event_loop().time() + secs_total
        while not cancelled.is_set():
            remaining = int(deadline - asyncio.get_event_loop().time())
            if remaining <= 0:
                break
            mins, secs = divmod(remaining, 60)
            countdown_text.value = f"{mins:01d}:{secs:02d}"
            try:
                countdown_text.update()
            except Exception:
                pass
            await asyncio.sleep(1)

        completion_state["shutdown_dialog_open"] = False

    def on_batch_completion_edge() -> None:
        """Fired exactly once when active count goes from >0 to 0."""
        n_done = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.DONE)
        n_failed = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.FAILED)

        try:
            show_toast(
                title="Runway batch complete",
                message=f"{n_done} succeeded, {n_failed} failed",
                duration="long",
            )
        except Exception:
            pass

        if completion_state.get("auto_shutdown_enabled") and not completion_state.get("shutdown_dialog_open"):
            completion_state["shutdown_dialog_open"] = True
            page.run_task(_shutdown_countdown_dialog, SHUTDOWN_GRACE_SECONDS)

    async def reload_completed_from_db() -> None:
        """Pull recent terminal jobs from DB into the cache."""
        try:
            recent = await list_recent_jobs(limit=200)
            completed_jobs_cache.clear()
            completed_jobs_cache.extend(j for j in recent if is_terminal(j))
            rebuild()
        except Exception as e:  # noqa: BLE001
            logger.warning("reload_completed_from_db failed: {}", e)

    def update_summary() -> None:
        n_drafts = sum(d.count for d in state.drafts)
        n_queued = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.QUEUED)
        n_generating = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.GENERATING)
        n_dl = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.DOWNLOADING)
        n_done = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.DONE)
        n_failed = sum(1 for j in state.active_jobs.values() if j.status == JobStatus.FAILED)

        parts: list[str] = []
        if n_drafts:
            parts.append(f"{n_drafts} planned")
        if n_queued:
            parts.append(f"{n_queued} queued")
        if n_generating + n_dl:
            parts.append(f"{n_generating + n_dl} active")
        if n_done:
            parts.append(f"{n_done} done")
        if n_failed:
            parts.append(f"{n_failed} failed")
        summary_label.value = "  •  ".join(parts) if parts else "No prompts yet"

        total = len(state.active_jobs)
        if total:
            batch_progress.value = (n_done + n_failed) / total
            batch_progress.visible = True
        else:
            batch_progress.visible = False

    # ── handlers ───────────────────────────────────────────────────

    def add_prompt(_: ft.ControlEvent) -> None:
        state.drafts.append(_make_default_draft())
        rebuild()

    def on_runner_update(job: Job) -> None:
        """Single shared on_update — used by both Start Batch AND attempt_resume."""
        state.emit_job_update(job)
        card = job_card_index.get(job.id)
        if card is not None:
            card.update_from(job)
        else:
            # New job we haven't drawn yet — full rebuild
            rebuild()
            return
        update_summary()
        if job.status.is_terminal:
            # Move it from Active to Completed without a full DB reload
            page.run_task(reload_completed_from_db)
        try:
            page.update()
        except Exception:
            pass

    def ensure_runner() -> None:
        """Create the persistent runner once per session if not already.

        Also handles the post-disconnect recovery path: if state.client was
        closed by a previous teardown, re-create it from the saved token
        before wiring the runner.
        """
        if not state.ensure_client():
            return
        if state.runner is None:
            if not getattr(state.client, "_db_sink_attached", False):
                state.client.add_sink(make_db_log_sink(task_id_from_url))
                state.client._db_sink_attached = True  # type: ignore[attr-defined]
            state.runner = BatchRunner(state.client, on_update=on_runner_update)

    async def start_batch(_: ft.ControlEvent) -> None:
        valid_drafts = [d for d in state.drafts if d.prompt.strip()]
        if not valid_drafts:
            page.snack_bar = ft.SnackBar(
                ft.Text("Add at least one prompt with text."),
                bgcolor=theme.Colors.surface_2,
            )
            page.snack_bar.open = True
            page.update()
            return

        # If a transient websocket disconnect nulled the client but the JWT
        # is still valid, re-hydrate the client instead of bouncing the user
        # back to /login.
        if not state.ensure_client():
            page.go("/login")
            return

        ensure_runner()

        # Expand drafts → jobs (drafts are removed once expanded)
        all_jobs: list[Job] = []
        for d in valid_drafts:
            all_jobs.extend(d.expand())
            state.drafts.remove(d)
        for j in all_jobs:
            state.active_jobs[j.id] = j

        rebuild()
        cancel_all_btn.disabled = False
        page.update()

        # Fire-and-forget — runner accepts more jobs while it's busy
        async def _submit_now() -> None:
            assert state.runner is not None
            await state.runner.add_jobs(
                all_jobs,
                batch_name=f"batch_{len(state.active_jobs)}",
            )
        page.run_task(_submit_now)

        page.snack_bar = ft.SnackBar(
            ft.Text(
                f"Queued {len(all_jobs)} job{'s' if len(all_jobs) != 1 else ''}. "
                "You can keep adding more anytime."
            ),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()

    def cancel_all_batch(_: ft.ControlEvent) -> None:
        """Force-cancel every non-terminal job — also hits Runway DELETE
        on each one with a task_id, regardless of runner state."""
        async def _do() -> None:
            from datetime import datetime, timezone
            from app.core import storage
            from app.runway.tasks import cancel_task

            active = [j for j in state.active_jobs.values() if not j.status.is_terminal]
            if not active:
                return

            ensure_runner()

            if state.runner is not None:
                await state.runner.force_cancel_all(active)
            else:
                # Manual fallback: per-job Runway DELETE + local cancel.
                logger.warning("cancel_all_batch: state.runner still None — manual fallback")
                for job in active:
                    if job.runway_task_id and state.client is not None:
                        try:
                            await cancel_task(state.client, job.runway_task_id)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("manual cancel_task failed: {}", e)
                    job.status = JobStatus.CANCELLED
                    job.completed_at = datetime.now(timezone.utc)
                    try:
                        await storage.upsert_job(job)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("storage upsert in cancel_all_batch: {}", e)
                    state.emit_job_update(job)
            rebuild()
        page.run_task(_do)

    def open_output(_: ft.ControlEvent) -> None:
        if is_web_mode():
            page.go("/downloads")
            return
        _open_folder(output_dir())

    async def do_sync_with_runway(_: ft.ControlEvent | None = None) -> None:
        """Cross-check active jobs against Runway and patch local state.

        Useful when the local UI shows QUEUED forever while Runway has long
        finished / rejected / lost the task (typical after a session
        reconnect where the polling worker died)."""
        ensure_runner()
        if state.runner is None or state.client is None:
            return
        active = [j for j in state.active_jobs.values() if not j.status.is_terminal]
        if not active:
            return
        try:
            sync_btn.disabled = True
            sync_btn.update()
        except Exception:
            pass
        try:
            summary = await state.runner.sync_with_runway(active)
        except Exception as e:  # noqa: BLE001
            logger.exception("sync_with_runway failed: {}", e)
            page.snack_bar = ft.SnackBar(
                ft.Text(f"Sync failed: {type(e).__name__}: {e}"),
                bgcolor=theme.Colors.state_failed,
            )
            page.snack_bar.open = True
            page.update()
            return
        finally:
            try:
                sync_btn.disabled = False
                sync_btn.update()
            except Exception:
                pass

        msg = (
            f"Synced {summary['checked']} job"
            f"{'s' if summary['checked'] != 1 else ''}"
            f"  •  {summary['updated']} updated"
        )
        if summary["missing"]:
            msg += f"  •  {summary['missing']} missing on Runway"
        page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=theme.Colors.surface_2)
        page.snack_bar.open = True
        rebuild()

    def go_debug(_: ft.ControlEvent) -> None:
        page.go("/debug")

    def go_history(_: ft.ControlEvent) -> None:
        page.go("/history")

    def do_logout(_: ft.ControlEvent) -> None:
        clear_token()
        state.token = None
        page.go("/login")

    # ── CSV/TXT import ─────────────────────────────────────────────

    import_picker = ft.FilePicker(on_result=lambda e: page.run_task(_handle_import_result, e))
    page.overlay.append(import_picker)

    async def _handle_import_result(e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        f = e.files[0]
        try:
            from pathlib import Path as _P
            res = import_file(_P(f.path))
        except Exception as exc:  # noqa: BLE001
            page.snack_bar = ft.SnackBar(
                ft.Text(f"Import failed: {exc}"),
                bgcolor=theme.Colors.state_failed,
            )
            page.snack_bar.open = True
            page.update()
            return

        for d in res.drafts:
            state.drafts.append(d)
        rebuild()

        warn_text = ""
        if res.errors:
            warn_text = f"  •  {len(res.errors)} warning{'s' if len(res.errors) != 1 else ''}"
            for w in res.errors[:3]:
                logger.warning("import: {}", w)
        page.snack_bar = ft.SnackBar(
            ft.Text(f"Imported {len(res.drafts)} prompt{'s' if len(res.drafts) != 1 else ''}{warn_text}"),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()

    def open_import_dialog(_: ft.ControlEvent) -> None:
        # First show a help dialog explaining the format
        help_text = (
            "CSV format (first line = headers):\n"
            "  prompt, model, duration, aspect, resolution, audio, count, output_dir, name\n\n"
            "Required: prompt.   Optional: everything else (defaults applied).\n"
            "  • model:    seedance_2 | kling_3_0_pro | kling_3_0_4k | kling_o3_4k | happyhorse_1_0\n"
            "  • duration: 3 | 5 | 10 | 15  (model-dependent)\n"
            "  • aspect:   16:9 | 9:16 | 1:1 | 3:4 | 4:3  (model-dependent)\n"
            "  • audio:    true | false\n"
            "  • count:    how many videos to generate from this prompt\n\n"
            "TXT format: one prompt per line (lines starting with # are ignored)."
        )
        confirm = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Text("Import prompts from file", color=theme.Colors.text_primary,
                          size=16, weight=ft.FontWeight.W_600),
            content=ft.Container(
                content=ft.Text(help_text, size=12, color=theme.Colors.text_secondary,
                                font_family="monospace", selectable=True),
                width=560,
            ),
            actions=[
                ft.Row(
                    [
                        theme.secondary_button("Cancel", on_click=lambda _e: page.close(confirm)),
                        theme.primary_button(
                            "Choose file…",
                            on_click=lambda _e: (
                                page.close(confirm),
                                import_picker.pick_files(
                                    dialog_title="Import prompts (CSV or TXT)",
                                    allow_multiple=False,
                                    allowed_extensions=["csv", "txt", "text", "md"],
                                ),
                            ),
                            icon=ft.Icons.UPLOAD_FILE,
                        ),
                    ],
                    spacing=4,
                ),
            ],
        )
        page.open(confirm)

    # ── Templates ──────────────────────────────────────────────────

    def open_templates_menu(_: ft.ControlEvent) -> None:
        templates = list_templates()
        list_column = ft.Column(
            [],
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        )

        new_name_field = ft.TextField(
            label="Template name",
            hint_text="e.g. \"Korean Tutor — Seedance 5s 9:16\"",
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
        )

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Text("Prompt templates", color=theme.Colors.text_primary,
                          size=16, weight=ft.FontWeight.W_600),
            content=ft.Container(width=520, content=ft.Column([], spacing=10, tight=True)),
        )

        def refresh_template_list() -> None:
            templates_now = list_templates()
            list_column.controls.clear()
            if not templates_now:
                list_column.controls.append(
                    ft.Text("No templates yet. Save the first draft as a template below.",
                            size=12, color=theme.Colors.text_dim)
                )
            for t in templates_now:
                def make_apply(tpl: Template):
                    def _apply(_e: ft.ControlEvent) -> None:
                        if not state.drafts:
                            state.drafts.append(PromptDraft())
                        for d in state.drafts:
                            if not d.prompt.strip():  # only empty drafts
                                tpl.apply_to(d)
                        rebuild()
                        page.close(dialog)
                        page.snack_bar = ft.SnackBar(
                            ft.Text(f"Applied template '{tpl.name}' to empty drafts."),
                            bgcolor=theme.Colors.surface_2,
                        )
                        page.snack_bar.open = True
                        page.update()
                    return _apply

                def make_delete(tpl_name: str):
                    def _del(_e: ft.ControlEvent) -> None:
                        delete_template(tpl_name)
                        refresh_template_list()
                    return _del

                list_column.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(t.name, size=13, weight=ft.FontWeight.W_600,
                                                color=theme.Colors.text_primary),
                                        ft.Text(
                                            f"{t.model_task_type}  •  {t.duration}s  •  "
                                            f"{t.aspect_ratio}  •  count={t.count}  •  "
                                            f"audio={'on' if t.audio else 'off'}",
                                            size=11,
                                            color=theme.Colors.text_dim,
                                        ),
                                    ],
                                    spacing=2,
                                    expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.PLAYLIST_ADD_CHECK,
                                    icon_color=theme.Colors.primary,
                                    tooltip="Apply to empty drafts",
                                    on_click=make_apply(t),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    icon_color=theme.Colors.text_dim,
                                    tooltip="Delete template",
                                    on_click=make_delete(t.name),
                                ),
                            ],
                        ),
                        bgcolor=theme.Colors.surface_2,
                        border=ft.border.all(1, theme.Colors.border),
                        border_radius=8,
                        padding=ft.padding.symmetric(horizontal=12, vertical=8),
                    )
                )
            try:
                list_column.update()
            except Exception:
                pass

        def save_current_as_template(_e: ft.ControlEvent) -> None:
            name = (new_name_field.value or "").strip()
            if not name:
                new_name_field.error_text = "Name required"
                new_name_field.update()
                return
            if not state.drafts:
                page.snack_bar = ft.SnackBar(
                    ft.Text("No draft to save settings from."),
                    bgcolor=theme.Colors.surface_2,
                )
                page.snack_bar.open = True
                page.update()
                return
            tpl = Template.from_draft(name, state.drafts[-1])
            add_template(tpl)
            new_name_field.value = ""
            new_name_field.error_text = None
            try:
                new_name_field.update()
            except Exception:
                pass
            refresh_template_list()

        save_btn = theme.primary_button(
            "Save current draft as template",
            on_click=save_current_as_template,
            icon=ft.Icons.BOOKMARK_ADD,
        )

        dialog.content = ft.Container(
            width=560,
            content=ft.Column(
                [
                    ft.Text(
                        "Templates store every per-card setting except the prompt text — "
                        "model, duration, aspect, audio, count, and output folder.",
                        size=12, color=theme.Colors.text_dim,
                    ),
                    ft.Container(height=10),
                    ft.Container(
                        content=list_column,
                        height=240,
                    ),
                    ft.Container(height=10),
                    ft.Divider(color=theme.Colors.border),
                    ft.Container(height=10),
                    new_name_field,
                    ft.Container(height=8),
                    save_btn,
                ],
                spacing=0,
                tight=True,
            ),
        )
        dialog.actions = [
            theme.secondary_button("Close", on_click=lambda _e: page.close(dialog)),
        ]
        page.open(dialog)
        refresh_template_list()

    # ── "Check by Task ID" dialog ──────────────────────────────────

    async def check_task_by_id(_: ft.ControlEvent) -> None:
        if not state.ensure_client():
            page.go("/login")
            return

        id_input = ft.TextField(
            label="Runway task ID",
            hint_text="3b7c264e-d6b5-48e1-9465-5dedf145988e",
            autofocus=True,
            bgcolor=theme.Colors.surface_2,
            border_color=theme.Colors.border,
            focused_border_color=theme.Colors.primary,
            color=theme.Colors.text_primary,
            label_style=ft.TextStyle(color=theme.Colors.text_secondary),
            cursor_color=theme.Colors.primary,
            text_size=13,
        )
        result_text = ft.Text("", size=12, color=theme.Colors.text_secondary, selectable=True)
        progress = ft.ProgressRing(width=18, height=18, stroke_width=2, color=theme.Colors.primary, visible=False)

        async def do_check(_e: ft.ControlEvent) -> None:
            tid = (id_input.value or "").strip()
            if not tid:
                result_text.value = "Enter a task ID first."
                result_text.color = theme.Colors.state_failed
                page.update()
                return
            progress.visible = True
            result_text.value = ""
            page.update()
            try:
                from app.runway.tasks import get_task_status
                status = await get_task_status(state.client, tid)
                lines = [
                    f"Status:    {status.status}",
                    f"Progress:  {int(status.progress_ratio * 100)}%",
                ]
                if status.estimated_start_seconds is not None:
                    lines.append(f"Eta start: {status.estimated_start_seconds}s")
                if status.error:
                    lines.append(f"Error:     {status.error}")
                if status.is_success and status.artifacts:
                    art = status.artifacts[0]
                    lines.append(f"File:      {art.get('filename') or '(unknown)'}")
                    lines.append(f"Size:      {int(art.get('fileSize') or 0) // 1024} KB")
                # Check if we already have it locally
                existing = await find_job_by_runway_id(tid)
                if existing:
                    lines.append("")
                    lines.append(f"Locally tracked as job {existing.id}, status {existing.status.value}.")
                    if existing.output_path:
                        lines.append(f"Saved to {existing.output_path}")
                result_text.value = "\n".join(lines)
                result_text.color = (
                    theme.Colors.state_done if status.is_success
                    else theme.Colors.state_failed if status.status == "FAILED"
                    else theme.Colors.text_primary
                )

                # Offer to download if SUCCEEDED and not already saved
                if status.is_success and status.artifacts and (not existing or not existing.output_path):
                    download_btn.visible = True
                    download_btn.data = (tid, status.artifacts[0])
                else:
                    download_btn.visible = False
            except Exception as exc:  # noqa: BLE001
                result_text.value = f"Lookup failed: {exc}"
                result_text.color = theme.Colors.state_failed
            finally:
                progress.visible = False
                page.update()

        async def do_download(e: ft.ControlEvent) -> None:
            data = e.control.data
            if not data:
                return
            tid, artifact = data
            try:
                from app.runway.tasks import download_artifact
                progress.visible = True
                page.update()
                dest = await download_artifact(state.client, artifact, output_dir())
                result_text.value += f"\n\nDownloaded → {dest}"
                result_text.color = theme.Colors.state_done
                download_btn.visible = False
            except Exception as exc:  # noqa: BLE001
                result_text.value += f"\n\nDownload failed: {exc}"
                result_text.color = theme.Colors.state_failed
            finally:
                progress.visible = False
                page.update()

        download_btn = ft.FilledButton(
            text="Download video",
            icon=ft.Icons.CLOUD_DOWNLOAD,
            visible=False,
            on_click=do_download,
            style=ft.ButtonStyle(
                color={"": theme.Colors.bg},
                bgcolor={"": theme.Colors.state_done},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.Colors.surface,
            title=ft.Text("Check task by ID", color=theme.Colors.text_primary, size=16, weight=ft.FontWeight.W_600),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Paste any Runway task ID — we'll query its current status. "
                            "If it's done, you can download the video right here.",
                            size=12,
                            color=theme.Colors.text_dim,
                        ),
                        ft.Container(height=10),
                        id_input,
                        ft.Container(height=10),
                        ft.Container(
                            content=result_text,
                            bgcolor=theme.Colors.surface_2,
                            border_radius=8,
                            padding=ft.padding.all(10),
                            visible=True,
                        ),
                        ft.Container(height=10),
                        download_btn,
                    ],
                    spacing=0,
                    tight=True,
                ),
                width=520,
            ),
            actions=[
                ft.Row(
                    [progress, ft.Container(width=8),
                     theme.secondary_button("Close", on_click=lambda _e: page.close(dialog)),
                     theme.primary_button("Check", on_click=do_check, icon=ft.Icons.SEARCH)],
                    spacing=4,
                ),
            ],
        )
        page.open(dialog)

    # ── header bar ─────────────────────────────────────────────────

    def on_auto_shutdown_toggle(e: ft.ControlEvent) -> None:
        completion_state["auto_shutdown_enabled"] = bool(e.control.value)
        if e.control.value:
            page.snack_bar = ft.SnackBar(
                ft.Text("PC will shut down 5 minutes after the batch finishes "
                        "(you can cancel)."),
                bgcolor=theme.Colors.surface_2,
            )
            page.snack_bar.open = True
            page.update()

    auto_shutdown_switch = ft.Switch(
        label="Auto-shutdown after batch",
        value=False,
        on_change=on_auto_shutdown_toggle,
        active_color=theme.Colors.primary,
        label_style=ft.TextStyle(size=12, color=theme.Colors.text_secondary),
    )

    add_btn = theme.secondary_button("Add prompt", on_click=add_prompt, icon=ft.Icons.ADD)
    start_btn = theme.primary_button("Start batch", on_click=start_batch, icon=ft.Icons.PLAY_ARROW)
    cancel_all_btn = ft.TextButton(
        "Cancel all",
        icon=ft.Icons.STOP_CIRCLE,
        on_click=cancel_all_batch,
        style=ft.ButtonStyle(color={"": theme.Colors.text_dim}),
        disabled=True,
    )
    import_btn = ft.IconButton(
        icon=ft.Icons.UPLOAD_FILE,
        icon_color=theme.Colors.text_secondary,
        tooltip="Import prompts from CSV/TXT",
        on_click=open_import_dialog,
    )
    templates_btn = ft.IconButton(
        icon=ft.Icons.BOOKMARKS,
        icon_color=theme.Colors.text_secondary,
        tooltip="Prompt templates",
        on_click=open_templates_menu,
    )
    check_id_btn = ft.IconButton(
        icon=ft.Icons.SEARCH,
        icon_color=theme.Colors.text_secondary,
        tooltip="Check task by ID",
        on_click=check_task_by_id,
    )
    sync_btn = ft.IconButton(
        icon=ft.Icons.SYNC,
        icon_color=theme.Colors.text_secondary,
        tooltip="Sync active jobs with Runway",
        on_click=lambda e: page.run_task(do_sync_with_runway, e),
    )
    history_btn = ft.IconButton(
        icon=ft.Icons.HISTORY,
        icon_color=theme.Colors.text_secondary,
        tooltip="Job history",
        on_click=go_history,
    )
    license_mgr_btn = ft.IconButton(
        icon=ft.Icons.KEY,
        icon_color=theme.Colors.accent,
        tooltip="License Manager (developer-only)",
        on_click=lambda _: page.go("/license-manager"),
        visible=has_private_key(),
    )
    open_btn = ft.IconButton(
        icon=ft.Icons.FOLDER_OPEN,
        icon_color=theme.Colors.text_secondary,
        tooltip="Open output folder",
        on_click=open_output,
    )
    debug_btn = ft.IconButton(
        icon=ft.Icons.BUG_REPORT_OUTLINED,
        icon_color=theme.Colors.text_secondary,
        tooltip="Debug log",
        on_click=go_debug,
    )
    logout_btn = ft.IconButton(
        icon=ft.Icons.LOGOUT,
        icon_color=theme.Colors.text_secondary,
        tooltip="Sign out",
        on_click=do_logout,
    )

    header = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.AUTO_AWESOME, color=theme.Colors.primary, size=22),
                        ft.Text(
                            "Runway Automation",
                            size=18,
                            weight=ft.FontWeight.W_700,
                            color=theme.Colors.text_primary,
                        ),
                        ft.Container(expand=True),
                        user_label,
                        ft.Container(width=8),
                        import_btn,
                        templates_btn,
                        check_id_btn,
                        sync_btn,
                        history_btn,
                        license_mgr_btn,
                        open_btn,
                        debug_btn,
                        logout_btn,
                    ],
                    spacing=8,
                ),
                ft.Container(height=14),
                ft.Row(
                    [
                        summary_label,
                        ft.Container(expand=True),
                        auto_shutdown_switch,
                        ft.Container(width=12),
                        batch_progress,
                        ft.Container(width=12),
                        add_btn,
                        start_btn,
                        cancel_all_btn,
                    ],
                    spacing=8,
                ),
            ],
            spacing=0,
        ),
        bgcolor=theme.Colors.surface,
        padding=ft.padding.symmetric(horizontal=24, vertical=18),
        border=ft.border.only(bottom=ft.BorderSide(1, theme.Colors.border)),
    )

    # ── Tabs: Active / Completed ──────────────────────────────────

    active_count_label = ft.Text("0 active", size=12, color=theme.Colors.text_dim)
    completed_count_label = ft.Text("0 completed", size=12, color=theme.Colors.text_dim)

    async def do_clear_completed(_: ft.ControlEvent) -> None:
        try:
            removed = await delete_terminal_jobs()
        except Exception as e:  # noqa: BLE001
            page.snack_bar = ft.SnackBar(
                ft.Text(f"Clear failed: {e}"),
                bgcolor=theme.Colors.state_failed,
            )
            page.snack_bar.open = True
            page.update()
            return
        # Drop terminal jobs from in-memory state too
        terminal_ids = [j.id for j in state.active_jobs.values() if j.status.is_terminal]
        for tid in terminal_ids:
            state.active_jobs.pop(tid, None)
        completed_jobs_cache.clear()
        rebuild()
        page.snack_bar = ft.SnackBar(
            ft.Text(f"Cleared {removed} completed jobs from history."),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True
        page.update()

    clear_completed_btn = ft.OutlinedButton(
        text="Clear completed",
        icon=ft.Icons.DELETE_SWEEP,
        on_click=do_clear_completed,
        disabled=True,
        style=ft.ButtonStyle(
            color={"": theme.Colors.text_secondary},
            side={"": ft.BorderSide(1, theme.Colors.border)},
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            text_style=ft.TextStyle(weight=ft.FontWeight.W_500, size=12),
        ),
    )

    active_panel = ft.Container(
        content=active_column,
        padding=ft.padding.symmetric(horizontal=24, vertical=18),
        expand=True,
    )
    completed_panel = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(expand=True),
                        clear_completed_btn,
                    ],
                ),
                ft.Container(height=10),
                completed_column,
            ],
            spacing=0,
            expand=True,
        ),
        padding=ft.padding.symmetric(horizontal=24, vertical=18),
        expand=True,
    )

    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=200,
        indicator_color=theme.Colors.primary,
        label_color=theme.Colors.text_primary,
        unselected_label_color=theme.Colors.text_dim,
        divider_color=theme.Colors.border,
        tabs=[
            ft.Tab(
                tab_content=ft.Row(
                    [
                        ft.Icon(ft.Icons.AUTO_AWESOME, size=14, color=theme.Colors.primary),
                        ft.Text("Active", size=13, weight=ft.FontWeight.W_600),
                        ft.Container(width=4),
                        active_count_label,
                    ],
                    spacing=4,
                ),
                content=active_panel,
            ),
            ft.Tab(
                tab_content=ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE, size=14, color=theme.Colors.state_done),
                        ft.Text("Completed", size=13, weight=ft.FontWeight.W_600),
                        ft.Container(width=4),
                        completed_count_label,
                    ],
                    spacing=4,
                ),
                content=completed_panel,
            ),
        ],
        expand=True,
    )

    body = ft.Container(content=tabs, expand=True)

    # ── live timer for active cards ────────────────────────────────

    async def timer_loop() -> None:
        while True:
            try:
                for card in list(job_card_index.values()):
                    if card.job.status.is_active:
                        card.tick()
                update_summary()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(TIMER_INTERVAL_SECONDS)

    page.run_task(timer_loop)

    # ── initial state ──────────────────────────────────────────────

    if not state.drafts and not state.active_jobs:
        state.drafts.append(_make_default_draft())

    rebuild()

    # Pull recent terminal jobs into the Completed tab on startup
    page.run_task(reload_completed_from_db)

    # ── resume-on-restart: pick up ALL unfinished jobs from a previous run ─
    async def attempt_resume() -> None:
        """Loads every non-terminal job from the DB and feeds them into the
        persistent runner. Idempotent — runner.add_jobs() de-dupes by job.id
        so calling this twice does not produce duplicate cards."""
        if state.client is None or state.token is None:
            return
        try:
            leftovers = await list_unfinished_jobs()
        except Exception as e:  # noqa: BLE001
            logger.warning("list_unfinished_jobs failed: {}", e)
            return
        if not leftovers:
            return

        ensure_runner()
        if state.runner is None:
            return

        # Inject into in-memory state so the UI shows them immediately
        new_ids: list[Job] = []
        for j in leftovers:
            if j.id not in state.active_jobs:
                state.active_jobs[j.id] = j
                new_ids.append(j)
        if not new_ids:
            return  # already known to this session

        n_pending = sum(1 for j in new_ids if j.runway_task_id is None)
        n_active = len(new_ids) - n_pending

        page.snack_bar = ft.SnackBar(
            ft.Text(
                f"Resuming  {n_active} active  +  {n_pending} pending"
                f"  job{'s' if len(new_ids) != 1 else ''}…"
            ),
            bgcolor=theme.Colors.surface_2,
        )
        page.snack_bar.open = True

        rebuild()
        cancel_all_btn.disabled = False
        try:
            await state.runner.resume_jobs(new_ids)
        except Exception as e:  # noqa: BLE001
            logger.exception("resume failed: {}", e)

        # Cross-check resumed jobs against Runway right away — covers the
        # common "worker died, status is frozen" case after a session
        # reconnect. The runner's normal poll loop will also catch this
        # eventually, but the user shouldn't have to wait through a 5s
        # interval per job to see the truth.
        try:
            await state.runner.sync_with_runway(new_ids)
            rebuild()
        except Exception as e:  # noqa: BLE001
            logger.warning("post-resume sync failed: {}", e)

    page.run_task(attempt_resume)

    return ft.View(
        route="/jobs",
        bgcolor=theme.Colors.bg,
        padding=0,
        controls=[
            ft.Column([header, body], spacing=0, expand=True),
        ],
    )
