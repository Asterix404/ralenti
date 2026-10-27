# Changelog

## 0.11.0 · 2026-10-28
### New
- **Watch live**: see an interpolation job while it renders, in Ralenti or in your own player.
- **Benchmark** (Settings › Performance): measures every installed AI model and encoder on a test clip.
- **Time estimates** in the Interpolate summary, following every setting; they get more accurate as you finish jobs.
### Improved
- Auto concurrency uses the benchmark to know which models already keep your GPU busy.

## 0.10.0 · 2026-10-28
### New
- **Pause and resume** any job instantly, from the queue bar or a job card. Nothing is lost.
- Interpolation renders in parts: a stopped, interrupted or crashed job resumes from its last finished part.
- **Run several jobs at once**: choose 1 to 4, or Auto, which adds jobs while your GPU has headroom and keeps them only if total speed improves.
- When done: **Close Ralenti**.
### Improved
- Clearer queue controls: Start, Pause / Resume, and Stop (after the running jobs, or now with progress kept).
- Audio is processed before the video, so the final step finishes sooner.

## 0.9.5 · 2026-10-27
### Improved
- Update checks now test the full download and install flow.

## 0.9.4 · 2026-10-27
### New
- **In-app updates**: Ralenti downloads the update, closes, installs it and reopens by itself. Settings, presets, queue and extensions are kept.
- **Changelog viewer**: About › Changelog shows every release, and a "What's new" window appears after each update.
### Improved
- The update window lists the changes of every version since yours, with the release date and download size.

## 0.9.3 · 2026-10-27
### New
- **Settings › Extensions**: install, remove and check optional extensions.
### Fixed
- A broken extension no longer prevents Ralenti from starting.

## 0.9.2 · 2026-10-27
### Improved
- Extensions load from the Ralenti data folder and survive app updates.

## 0.9.1 · 2026-10-27
### New
- Extensions can provide their own update instructions.

## 0.9.0 · 2026-10-27 · First public release
### New
- **Interpolate**: smooth slow motion and higher frame rates with RIFE (free, open source, any Vulkan GPU).
- **Preview takes** with before/after and take-vs-take comparison in the player, switchable audio and looping.
- Exact frame rates, natural-pitch or pitch-kept slow-motion audio, and a processing resolution setting.
- Crop with black-bar detection, trim, and presets with a default preset.
- **Encode** to a VMAF target or constant quality; lossless **Trim** and **Merge**.
- Resumable step-based queue with ETA, taskbar progress, and sleep or shut down when done.
- **Downloads** page: one-click install and updates for FFmpeg and RIFE.
- Disk-space prediction, GPU/CPU/RAM meters, collapsible sidebar and accent colors.
- Windows installer (per user, no admin needed) and a portable zip.
