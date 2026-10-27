# Third-party notices

Ralenti's own code is MIT-licensed. Release builds include:

| Component | License | Notes |
|---|---|---|
| Qt / PySide6 | LGPL v3 | Dynamically linked; shipped unmodified. |
| qtawesome | MIT | Icon loading. |
| Phosphor Icons | MIT | Main icon set (via qtawesome). |
| Material Design Icons | Apache 2.0 | Fallback icons (via qtawesome). |
| psutil | BSD 3-Clause | CPU and RAM meter. |

The Windows installer is built with Inno Setup (free; its license permits commercial and
non-commercial use).

## Downloaded by the user, not included in releases

These are fetched from their official GitHub releases into %APPDATA%\Ralenti\tools when the user
clicks Install, and run as separate programs.

| Component | License | Notes |
|---|---|---|
| FFmpeg (Gyan's full build) | GPL v3 | Includes libvmaf (BSD-2-Clause-Patent), Rubber Band (GPL) and SVT-AV1 (BSD). Source code is available from the FFmpeg project and the build's own release. |
| rife-ncnn-vulkan | MIT | The RIFE engine for Vulkan GPUs, built on Tencent's ncnn (BSD 3-Clause). |
| RIFE models | MIT | From the RIFE / Practical-RIFE research projects (Huang et al., ECCV 2022). |
