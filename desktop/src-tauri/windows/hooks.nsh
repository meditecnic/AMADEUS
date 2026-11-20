; Tauri v2 NSIS installer hooks entry point.
; Includes the UTF-8 implementation with explicit /CHARSET=UTF8 so Simplified Chinese
; UI copy compiles identically regardless of the build host's ANSI code page.
!include /CHARSET=UTF8 "${__FILEDIR__}\data-location.nsh"
