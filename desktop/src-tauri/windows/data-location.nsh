; Amadeus NSIS custom data directory page and pre/post-install hooks.
; Aligns with desktop/src-tauri/src/lib.rs::resolve_data_location and
; backend/app/services/data_location.py::read_location / validate_startup.

!ifndef AMADEUS_NSIS_DATA_LOCATION_INCLUDED
!define AMADEUS_NSIS_DATA_LOCATION_INCLUDED

Var AmadeusDataStateInitialized
Var AmadeusDefaultDataDir
Var AmadeusLocationJsonFile
Var AmadeusHadLocationFile
Var AmadeusExistingDataDir
Var AmadeusSelectedDataDir
Var AmadeusCliDataDirProvided
Var AmadeusDataPageDialog
Var AmadeusDataPageInput
Var AmadeusDataPageBrowseBtn
Var AmadeusDataPageResetBtn
Var AmadeusDataPageHintLabel
Var AmadeusReadHadBom

; -----------------------------------------------------------------------------
; Strict JSON helper macros (code-unit based, so every comparison is exact and
; case-sensitive: NSIS "==" / lstrcmpi style comparisons are NOT).
; -----------------------------------------------------------------------------

; OUT = UTF-16 code unit at index POS of TEXT (0 when POS is outside TEXT).
; OUT must not be $0 or $1 (both are preserved around the call).
!macro AmadeusJsonPeek TEXT POS OUT
  Push $0
  Push $1
  StrCpy $0 "${TEXT}"
  StrCpy $1 "${POS}"
  Call AmadeusJsonCodeAt
  StrCpy ${OUT} $0
  Pop $1
  Pop $0
!macroend

; Consume a run of ASCII digits [0-9] starting at POS; COUNT = number consumed,
; CODE = first code unit after the run.
!macro AmadeusJsonDigits TEXT POS CODE COUNT
  StrCpy ${COUNT} 0
  ${Do}
    !insertmacro AmadeusJsonPeek ${TEXT} ${POS} ${CODE}
    ${If} ${CODE} >= 48
    ${AndIf} ${CODE} <= 57
      IntOp ${POS} ${POS} + 1
      IntOp ${COUNT} ${COUNT} + 1
    ${Else}
      ${Break}
    ${EndIf}
  ${Loop}
!macroend

!macroundef MUI_PAGE_DIRECTORY
!macro MUI_PAGE_DIRECTORY
  !verbose push
  !verbose ${MUI_VERBOSE}
  !insertmacro MUI_PAGE_INIT
  !insertmacro MUI_PAGEDECLARATION_DIRECTORY
  !verbose pop
  !insertmacro AMADEUS_INSERT_DATA_LOCATION_PAGE
!macroend

!macro AMADEUS_INSERT_DATA_LOCATION_PAGE
  !ifndef AMADEUS_DATA_LOCATION_PAGE_DEFINED
    !define AMADEUS_DATA_LOCATION_PAGE_DEFINED

    Page custom AmadeusDataDirPageCreate AmadeusDataDirPageLeave

    ; -------------------------------------------------------------------------
    ; UTF-8 file helpers (zero BOM, CP_UTF8 = 65001, no external child process)
    ; -------------------------------------------------------------------------

    ; Input: $0 = path. Output: $0 = decoded UTF-16 string ("" on missing/error/link).
    Function AmadeusReadUtf8File
      System::Store "S"
      StrCpy $R9 ""
      StrCpy $AmadeusReadHadBom "0"

      System::Call 'kernel32::GetFileAttributesW(w r0) i .r1'
      ${If} $1 == -1
        Goto amadeus_read_done
      ${EndIf}
      IntOp $2 $1 & 0x0410 ; FILE_ATTRIBUTE_REPARSE_POINT (0x400) | FILE_ATTRIBUTE_DIRECTORY (0x10)
      ${If} $2 <> 0
        Goto amadeus_read_done
      ${EndIf}

      ; GENERIC_READ=0x80000000, FILE_SHARE_READ=1, OPEN_EXISTING=3, FILE_ATTRIBUTE_NORMAL=0x80
      System::Call 'kernel32::CreateFileW(w r0, i 0x80000000, i 1, p 0, i 3, i 0x80, p 0) p .r2'
      ${If} $2 == -1
      ${OrIf} $2 == 0
        Goto amadeus_read_done
      ${EndIf}

      System::Call 'kernel32::GetFileSize(p r2, p 0) i .r3'
      ${If} $3 <= 0
      ${OrIf} $3 > 65536
        System::Call 'kernel32::CloseHandle(p r2) i .r4'
        Goto amadeus_read_done
      ${EndIf}

      IntOp $4 $3 + 4
      System::Alloc $4
      Pop $5
      ${If} $5 == 0
        System::Call 'kernel32::CloseHandle(p r2) i .r4'
        Goto amadeus_read_done
      ${EndIf}

      System::Call 'kernel32::ReadFile(p r2, p r5, i r3, *i .r6, p 0) i .r7'
      System::Call 'kernel32::CloseHandle(p r2) i .r4'
      ${If} $7 == 0
      ${OrIf} $6 <> $3 ; short read: never validate a partial file
        System::Free $5
        Goto amadeus_read_done
      ${EndIf}

      ; MB_ERR_INVALID_CHARS (8): malformed UTF-8 fails instead of becoming U+FFFD
      System::Call 'kernel32::MultiByteToWideChar(i 65001, i 8, p r5, i r6, p 0, i 0) i .r7'
      ${If} $7 <= 0
        System::Free $5
        Goto amadeus_read_done
      ${EndIf}

      IntOp $8 $7 + 2
      IntOp $8 $8 * 2
      System::Alloc $8
      Pop $9
      ${If} $9 == 0
        System::Free $5
        Goto amadeus_read_done
      ${EndIf}

      System::Call 'kernel32::MultiByteToWideChar(i 65001, i 8, p r5, i r6, p r9, i r7) i .r7'
      ${If} $7 > 0
        System::Call 'kernel32::lstrcpynW(w .R9, p r9, i ${NSIS_MAX_STRLEN}) p .r8'
        ; lstrcpynW silently truncates at the NSIS string limit and stops at an embedded
        ; NUL: any length mismatch means we did not see the whole file -> refuse it.
        StrLen $1 $R9
        ${If} $1 <> $7
          StrCpy $R9 ""
        ${EndIf}
      ${EndIf}
      System::Free $9
      System::Free $5

      ; Strip optional UTF-8 BOM (U+FEFF) if an external editor wrote one
      StrCpy $1 $R9 1
      ${If} $1 == "${U+FEFF}"
        StrCpy $R9 $R9 "" 1
        StrCpy $AmadeusReadHadBom "1"
      ${EndIf}

      amadeus_read_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = file path, $1 = string content. Output: $0 = "1" on success, "0" on failure.
    Function AmadeusWriteUtf8NoBom
      System::Store "S"
      StrCpy $R9 "0"

      System::Call 'kernel32::GetFileAttributesW(w r0) i .r2'
      ${If} $2 <> -1
        IntOp $3 $2 & 0x0410 ; refuse reparse point or directory
        ${If} $3 <> 0
          Goto amadeus_write_done
        ${EndIf}
      ${EndIf}

      System::Call 'kernel32::WideCharToMultiByte(i 65001, i 0, w r1, i -1, p 0, i 0, p 0, p 0) i .r2'
      ${If} $2 <= 0
        Goto amadeus_write_done
      ${EndIf}
      IntOp $3 $2 - 1 ; exclude trailing NUL, no BOM

      System::Alloc $2
      Pop $4
      ${If} $4 == 0
        Goto amadeus_write_done
      ${EndIf}

      System::Call 'kernel32::WideCharToMultiByte(i 65001, i 0, w r1, i -1, p r4, i r2, p 0, p 0) i .r5'
      ${If} $5 <= 0
        System::Free $4
        Goto amadeus_write_done
      ${EndIf}

      ; GENERIC_WRITE=0x40000000, CREATE_ALWAYS=2, FILE_ATTRIBUTE_NORMAL=0x80
      System::Call 'kernel32::CreateFileW(w r0, i 0x40000000, i 0, p 0, i 2, i 0x80, p 0) p .r6'
      ${If} $6 == -1
      ${OrIf} $6 == 0
        System::Free $4
        Goto amadeus_write_done
      ${EndIf}

      StrCpy $8 1
      StrCpy $7 0
      ${If} $3 > 0
        System::Call 'kernel32::WriteFile(p r6, p r4, i r3, *i .r7, p 0) i .r8'
      ${EndIf}
      System::Call 'kernel32::FlushFileBuffers(p r6) i .r9'
      System::Call 'kernel32::CloseHandle(p r6) i .r9'
      System::Free $4

      ${If} $8 <> 0
      ${AndIf} $7 == $3
        StrCpy $R9 "1"
      ${EndIf}

      amadeus_write_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = file path, $1 = string content. Writes via sibling .tmp then MoveFileExW.
    ; Output: $0 = "1" on success, "0" on failure.
    Function AmadeusAtomicWriteUtf8NoBom
      System::Store "S"
      StrCpy $R8 "$0"
      StrCpy $R7 "$0.amadeus-nsis.tmp"
      Delete "$R7"
      StrCpy $0 "$R7"
      Call AmadeusWriteUtf8NoBom
      ${If} $0 != "1"
        Delete "$R7"
        StrCpy $R9 "0"
        Goto amadeus_atomic_done
      ${EndIf}
      ; MOVEFILE_REPLACE_EXISTING (0x1) | MOVEFILE_WRITE_THROUGH (0x8) = 0x9
      System::Call 'kernel32::MoveFileExW(w R7, w R8, i 0x9) i .r2'
      ${If} $2 == 0
        Delete "$R7"
        StrCpy $R9 "0"
      ${Else}
        StrCpy $R9 "1"
      ${EndIf}
      amadeus_atomic_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; -------------------------------------------------------------------------
    ; JSON escaping & top-level "path" field extraction
    ; -------------------------------------------------------------------------

    ; Input: $0 = raw path string. Output: $0 = JSON-escaped string body (without outer quotes).
    Function AmadeusJsonEscapeString
      System::Store "S"
      StrLen $1 $0
      StrCpy $2 0
      StrCpy $R9 ""
      ${While} $2 < $1
        StrCpy $3 $0 1 $2
        ${If} $3 == "\"
          StrCpy $R9 "$R9\\"
        ${ElseIf} $3 == '"'
          StrCpy $R9 '$R9\"'
        ${Else}
          StrCpy $R9 "$R9$3"
        ${EndIf}
        IntOp $2 $2 + 1
      ${EndWhile}
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = JSON string, $1 = start index right after opening '"'.
    ; Output: $0 = decoded string, $1 = index right after closing '"' (-1 on syntax error).
    Function AmadeusDecodeJsonStringAt
      System::Store "S"
      StrLen $2 $0
      StrCpy $3 $1
      StrCpy $R9 ""
      StrCpy $R8 -1

      ${While} $3 < $2
        StrCpy $4 $0 1 $3
        ${If} $4 == '"'
          IntOp $R8 $3 + 1
          ${Break}
        ${ElseIf} $4 == "\"
          IntOp $3 $3 + 1
          ${If} $3 >= $2
            ${Break}
          ${EndIf}
          StrCpy $5 $0 1 $3
          ${If} $5 == "\"
            StrCpy $R9 "$R9\"
          ${ElseIf} $5 == '"'
            StrCpy $R9 '$R9"'
          ${ElseIf} $5 == "/"
            StrCpy $R9 "$R9/"
          ${ElseIf} $5 == "u"
            IntOp $6 $3 + 1
            StrCpy $7 $0 4 $6
            StrLen $8 $7
            ${If} $8 < 4
              ${Break}
            ${EndIf}
            IntFmt $9 "%c" "0x$7"
            StrCpy $R9 "$R9$9"
            IntOp $3 $3 + 4
          ${Else}
            ; Control escapes (\n, \r, \t, \b, \f) cannot appear in valid Windows paths
            StrCpy $R9 "$R9?"
          ${EndIf}
        ${Else}
          StrCpy $R9 "$R9$4"
        ${EndIf}
        IntOp $3 $3 + 1
      ${EndWhile}

      Push $R8
      Push $R9
      System::Store "L"
      Pop $0
      Pop $1
    FunctionEnd

    ; Input: $0 = JSON object text. Output: $0 = top-level "path" string value, or "" if absent/invalid.
    Function AmadeusExtractJsonPathField
      System::Store "S"
      StrCpy $R0 $0
      StrLen $R1 $R0
      StrCpy $R2 0
      StrCpy $R9 ""

      ; Skip leading whitespace
      ${While} $R2 < $R1
        StrCpy $R3 $R0 1 $R2
        ${If} $R3 == " "
        ${OrIf} $R3 == "$\t"
        ${OrIf} $R3 == "$\r"
        ${OrIf} $R3 == "$\n"
          IntOp $R2 $R2 + 1
        ${Else}
          ${Break}
        ${EndIf}
      ${EndWhile}

      StrCpy $R3 $R0 1 $R2
      ${If} $R3 != "{"
        Goto amadeus_json_done
      ${EndIf}
      IntOp $R2 $R2 + 1

      ${While} $R2 < $R1
        ; Skip whitespace and commas between top-level members
        ${While} $R2 < $R1
          StrCpy $R3 $R0 1 $R2
          ${If} $R3 == " "
          ${OrIf} $R3 == "$\t"
          ${OrIf} $R3 == "$\r"
          ${OrIf} $R3 == "$\n"
          ${OrIf} $R3 == ","
            IntOp $R2 $R2 + 1
          ${Else}
            ${Break}
          ${EndIf}
        ${EndWhile}

        ${If} $R2 >= $R1
          ${Break}
        ${EndIf}
        StrCpy $R3 $R0 1 $R2
        ${If} $R3 == "}"
          ${Break}
        ${EndIf}
        ${If} $R3 != '"'
          Goto amadeus_json_done
        ${EndIf}

        ; Read member key
        IntOp $1 $R2 + 1
        StrCpy $0 $R0
        Call AmadeusDecodeJsonStringAt
        ${If} $1 < 0
          Goto amadeus_json_done
        ${EndIf}
        StrCpy $R4 $0 ; member key
        StrCpy $R2 $1

        ; Skip whitespace before ':'
        ${While} $R2 < $R1
          StrCpy $R3 $R0 1 $R2
          ${If} $R3 == " "
          ${OrIf} $R3 == "$\t"
          ${OrIf} $R3 == "$\r"
          ${OrIf} $R3 == "$\n"
            IntOp $R2 $R2 + 1
          ${Else}
            ${Break}
          ${EndIf}
        ${EndWhile}
        StrCpy $R3 $R0 1 $R2
        ${If} $R3 != ":"
          Goto amadeus_json_done
        ${EndIf}
        IntOp $R2 $R2 + 1

        ; Skip whitespace after ':'
        ${While} $R2 < $R1
          StrCpy $R3 $R0 1 $R2
          ${If} $R3 == " "
          ${OrIf} $R3 == "$\t"
          ${OrIf} $R3 == "$\r"
          ${OrIf} $R3 == "$\n"
            IntOp $R2 $R2 + 1
          ${Else}
            ${Break}
          ${EndIf}
        ${EndWhile}

        StrCpy $R3 $R0 1 $R2
        ; Use case-sensitive comparison for exact "path" key
        System::Call 'kernel32::lstrcmpW(w R4, w "path") i .r5'
        ${If} $5 == 0
          ${If} $R3 != '"'
            Goto amadeus_json_done
          ${EndIf}
          IntOp $1 $R2 + 1
          StrCpy $0 $R0
          Call AmadeusDecodeJsonStringAt
          ${If} $1 >= 0
            StrCpy $R9 $0
          ${EndIf}
          Goto amadeus_json_done
        ${EndIf}

        ; Skip non-"path" member value (string, nested container, or primitive)
        ${If} $R3 == '"'
          IntOp $1 $R2 + 1
          StrCpy $0 $R0
          Call AmadeusDecodeJsonStringAt
          ${If} $1 < 0
            Goto amadeus_json_done
          ${EndIf}
          StrCpy $R2 $1
        ${ElseIf} $R3 == "{"
        ${OrIf} $R3 == "["
          StrCpy $R5 0 ; nesting depth
          StrCpy $R6 0 ; in string
          ${While} $R2 < $R1
            StrCpy $R7 $R0 1 $R2
            ${If} $R6 == 1
              ${If} $R7 == "\"
                IntOp $R2 $R2 + 1
              ${ElseIf} $R7 == '"'
                StrCpy $R6 0
              ${EndIf}
            ${Else}
              ${If} $R7 == '"'
                StrCpy $R6 1
              ${ElseIf} $R7 == "{"
              ${OrIf} $R7 == "["
                IntOp $R5 $R5 + 1
              ${ElseIf} $R7 == "}"
              ${OrIf} $R7 == "]"
                IntOp $R5 $R5 - 1
                ${If} $R5 <= 0
                  IntOp $R2 $R2 + 1
                  ${Break}
                ${EndIf}
              ${EndIf}
            ${EndIf}
            IntOp $R2 $R2 + 1
          ${EndWhile}
        ${Else}
          ${While} $R2 < $R1
            StrCpy $R7 $R0 1 $R2
            ${If} $R7 == ","
            ${OrIf} $R7 == "}"
              ${Break}
            ${EndIf}
            IntOp $R2 $R2 + 1
          ${EndWhile}
        ${EndIf}
      ${EndWhile}

      amadeus_json_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; -------------------------------------------------------------------------
    ; Command-line /DATADIR=..., path normalization & safety checks
    ; -------------------------------------------------------------------------

    ; Parses /DATADIR=<path>, /DATADIR="<path>", or "/DATADIR=<path>" from command-line parameters.
    ; Output: $0 = extracted path ("" if not provided), sets $AmadeusCliDataDirProvided ("0" or "1").
    Function AmadeusParseCliDataDir
      System::Store "S"
      StrCpy $AmadeusCliDataDirProvided "0"
      StrCpy $R9 ""
      ${GetParameters} $R0
      StrLen $R1 $R0
      StrCpy $R2 0

      ${While} $R2 < $R1
        ; Skip spaces
        ${While} $R2 < $R1
          StrCpy $R3 $R0 1 $R2
          ${If} $R3 == " "
          ${OrIf} $R3 == "$\t"
            IntOp $R2 $R2 + 1
          ${Else}
            ${Break}
          ${EndIf}
        ${EndWhile}
        ${If} $R2 >= $R1
          ${Break}
        ${EndIf}

        ; Collect one token (tracking quotes so spaces inside quotes stay together)
        StrCpy $R4 "" ; token
        StrCpy $R5 0  ; in_quote
        ${While} $R2 < $R1
          StrCpy $R3 $R0 1 $R2
          ${If} $R3 == '"'
            ${If} $R5 == 0
              StrCpy $R5 1
            ${Else}
              StrCpy $R5 0
            ${EndIf}
            StrCpy $R4 "$R4$R3"
          ${ElseIf} $R5 == 0
            ${If} $R3 == " "
            ${OrIf} $R3 == "$\t"
              ${Break}
            ${EndIf}
            StrCpy $R4 "$R4$R3"
          ${Else}
            StrCpy $R4 "$R4$R3"
          ${EndIf}
          IntOp $R2 $R2 + 1
        ${EndWhile}

        ; Strip outer quotes if the whole argument was quoted ("/DATADIR=D:\My Data")
        StrCpy $R6 $R4 1
        StrCpy $R7 $R4 1 -1
        ${If} $R6 == '"'
        ${AndIf} $R7 == '"'
          StrLen $R8 $R4
          ${If} $R8 >= 2
            IntOp $R8 $R8 - 2
            StrCpy $R4 $R4 $R8 1
          ${EndIf}
        ${EndIf}

        StrCpy $R6 $R4 9
        ${If} $R6 == "/DATADIR="
          StrCpy $AmadeusCliDataDirProvided "1"
          StrCpy $R9 $R4 "" 9
          ; Strip inner quotes if written as /DATADIR="D:\My Data"
          StrCpy $R6 $R9 1
          StrCpy $R7 $R9 1 -1
          ${If} $R6 == '"'
          ${AndIf} $R7 == '"'
            StrLen $R8 $R9
            ${If} $R8 >= 2
              IntOp $R8 $R8 - 2
              StrCpy $R9 $R9 $R8 1
            ${EndIf}
          ${EndIf}
          ${Break}
        ${EndIf}
      ${EndWhile}

      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = raw path. Output: $0 = normalized Windows path (slash->backslash, trimmed).
    Function AmadeusNormalizeWinPath
      System::Store "S"
      StrCpy $R0 $0

      ; Trim leading spaces and quotes
      ${Do}
        StrCpy $R1 $R0 1
        ${If} $R1 == " "
        ${OrIf} $R1 == "$\t"
        ${OrIf} $R1 == '"'
          StrCpy $R0 $R0 "" 1
        ${Else}
          ${ExitDo}
        ${EndIf}
      ${Loop}

      ; Trim trailing spaces and quotes
      ${Do}
        StrLen $R2 $R0
        ${If} $R2 == 0
          ${ExitDo}
        ${EndIf}
        StrCpy $R1 $R0 1 -1
        ${If} $R1 == " "
        ${OrIf} $R1 == "$\t"
        ${OrIf} $R1 == '"'
          StrCpy $R0 $R0 -1
        ${Else}
          ${ExitDo}
        ${EndIf}
      ${Loop}

      ; Normalize '/' -> '\' and collapse duplicate '\' (after index 1)
      StrLen $R2 $R0
      StrCpy $R3 0
      StrCpy $R9 ""
      StrCpy $R8 ""
      ${While} $R3 < $R2
        StrCpy $R1 $R0 1 $R3
        ${If} $R1 == "/"
          StrCpy $R1 "\"
        ${EndIf}
        ${If} $R1 == "\"
        ${AndIf} $R8 == "\"
        ${AndIf} $R3 > 1
          ; skip duplicate backslash
        ${Else}
          StrCpy $R9 "$R9$R1"
          StrCpy $R8 $R1
        ${EndIf}
        IntOp $R3 $R3 + 1
      ${EndWhile}

      ; Strip trailing '\' when longer than drive root "X:\"
      ${Do}
        StrLen $R2 $R9
        ${If} $R2 <= 3
          ${ExitDo}
        ${EndIf}
        StrCpy $R1 $R9 1 -1
        ${If} $R1 == "\"
          StrCpy $R9 $R9 -1
        ${Else}
          ${ExitDo}
        ${EndIf}
      ${Loop}

      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = normalized path. Output: $0 = "1" if clean (no reparse point in existing chain), "0" if link/reparse.
    Function AmadeusCheckNoReparseChain
      System::Store "S"
      StrCpy $R0 $0
      StrCpy $R9 "1"

      ${Do}
        StrLen $R1 $R0
        ${If} $R1 <= 3
          ${ExitDo}
        ${EndIf}
        System::Call 'kernel32::GetFileAttributesW(w R0) i .r2'
        ${If} $2 <> -1
          IntOp $3 $2 & 0x0400 ; FILE_ATTRIBUTE_REPARSE_POINT
          ${If} $3 <> 0
            StrCpy $R9 "0"
            ${ExitDo}
          ${EndIf}
        ${EndIf}
        ${GetParent} "$R0" $R4
        ${If} $R4 == ""
        ${OrIf} $R4 == $R0
          ${ExitDo}
        ${EndIf}
        StrCpy $R0 $R4
      ${Loop}

      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = candidate path, $1 = protected root. Output: $0 = "1" if candidate == root or inside root.
    Function AmadeusPathIsInsideOrEqual
      System::Store "S"
      StrCpy $R0 $0
      StrCpy $R1 $1
      StrCpy $0 $R0
      Call AmadeusNormalizeWinPath
      StrCpy $R0 $0
      StrCpy $0 $R1
      Call AmadeusNormalizeWinPath
      StrCpy $R1 $0

      StrCpy $R9 "0"
      ${If} $R1 == ""
        Goto amadeus_inside_done
      ${EndIf}
      ; NSIS == is case-insensitive
      ${If} $R0 == $R1
        StrCpy $R9 "1"
        Goto amadeus_inside_done
      ${EndIf}
      StrCpy $R2 "$R1\"
      StrLen $R3 $R2
      StrCpy $R4 $R0 $R3
      ${If} $R4 == $R2
        StrCpy $R9 "1"
      ${EndIf}

      amadeus_inside_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = raw user/CLI path.
    ; Output: $0 = normalized path, $1 = "" if valid or Chinese error message if invalid.
    Function AmadeusValidateDataDir
      System::Store "S"
      Call AmadeusNormalizeWinPath
      StrCpy $R0 $0
      StrCpy $R9 ""

      StrLen $R1 $R0
      ${If} $R1 == 0
        StrCpy $R9 "数据储存路径不能为空，请选择本地文件夹绝对路径。"
        Goto amadeus_validate_done
      ${EndIf}

      ; Require absolute drive path X:\<folder> (reject relative paths and bare drive roots like D:\)
      ${If} $R1 < 4
        StrCpy $R9 "请选择具体的子文件夹路径（例如 D:\AmadeusData），不能直接使用磁盘根目录或相对路径。"
        Goto amadeus_validate_done
      ${EndIf}

      StrCpy $R2 $R0 1 0
      StrCpy $R3 $R0 2 1
      ${If} $R3 != ":\"
        StrCpy $R9 "请输入完整的本地磁盘绝对路径（例如 D:\AmadeusData）。"
        Goto amadeus_validate_done
      ${EndIf}

      ; Verify drive letter is A-Z / a-z
      ${StrCase} $R4 $R2 "U"
      System::Call 'user32::CharUpperW(w R4) p .r5'
      StrCpy $R5 "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
      ${StrLoc} $R6 $R5 $R4 ">"
      ${If} $R6 == ""
        StrCpy $R9 "盘符无效，请输入有效的本地磁盘绝对路径（例如 D:\AmadeusData）。"
        Goto amadeus_validate_done
      ${EndIf}

      ; Check subpath segments after "X:\" for illegal chars, "." / "..", or trailing dot/space
      StrCpy $R2 3
      StrCpy $R7 "" ; current segment
      ${While} $R2 <= $R1
        ${If} $R2 == $R1
          StrCpy $R3 "\"
        ${Else}
          StrCpy $R3 $R0 1 $R2
        ${EndIf}

        ${If} $R3 == "\"
          ${If} $R7 == ""
          ${OrIf} $R7 == "."
          ${OrIf} $R7 == ".."
            StrCpy $R9 "数据储存路径包含无效的目录片段（. 或 ..）。"
            Goto amadeus_validate_done
          ${EndIf}
          StrCpy $R8 $R7 1 -1
          ${If} $R8 == "."
          ${OrIf} $R8 == " "
            StrCpy $R9 "文件夹名称末尾不能包含空格或句点。"
            Goto amadeus_validate_done
          ${EndIf}
          StrCpy $R7 ""
        ${Else}
          ${If} $R3 == "<"
          ${OrIf} $R3 == ">"
          ${OrIf} $R3 == ":"
          ${OrIf} $R3 == '"'
          ${OrIf} $R3 == "|"
          ${OrIf} $R3 == "?"
          ${OrIf} $R3 == "*"
            StrCpy $R9 "数据储存路径包含非法文件名字符。"
            Goto amadeus_validate_done
          ${EndIf}
          StrCpy $R7 "$R7$R3"
        ${EndIf}
        IntOp $R2 $R2 + 1
      ${EndWhile}

      ; Reject overlap with $INSTDIR and Windows/Program Files directories
      StrCpy $0 $R0
      StrCpy $1 "$INSTDIR"
      Call AmadeusPathIsInsideOrEqual
      ${If} $0 == "1"
        StrCpy $R9 "数据目录不能设在程序安装目录内，否则升级或卸载程序时会互相干扰。"
        Goto amadeus_validate_done
      ${EndIf}

      ; Also block $INSTDIR inside data directory
      StrCpy $0 "$INSTDIR"
      StrCpy $1 $R0
      Call AmadeusPathIsInsideOrEqual
      ${If} $0 == "1"
        StrCpy $R9 "数据目录不能包含程序安装目录。"
        Goto amadeus_validate_done
      ${EndIf}

      StrCpy $0 $R0
      StrCpy $1 "$WINDIR"
      Call AmadeusPathIsInsideOrEqual
      ${If} $0 == "1"
        StrCpy $R9 "数据目录不能放在 Windows 系统目录内。"
        Goto amadeus_validate_done
      ${EndIf}

      StrCpy $0 $R0
      StrCpy $1 "$PROGRAMFILES"
      Call AmadeusPathIsInsideOrEqual
      ${If} $0 == "1"
        StrCpy $R9 "数据目录不能放在 Program Files 程序目录内。"
        Goto amadeus_validate_done
      ${EndIf}

      StrCpy $0 $R0
      StrCpy $1 "$PROGRAMFILES64"
      Call AmadeusPathIsInsideOrEqual
      ${If} $0 == "1"
        StrCpy $R9 "数据目录不能放在 Program Files 程序目录内。"
        Goto amadeus_validate_done
      ${EndIf}

      ; Check target is not an existing regular file and has no symlink/reparse points in chain
      System::Call 'kernel32::GetFileAttributesW(w R0) i .r2'
      ${If} $2 <> -1
        IntOp $3 $2 & 0x0010 ; FILE_ATTRIBUTE_DIRECTORY
        ${If} $3 == 0
          StrCpy $R9 "所选路径已存在同名文件，请选择文件夹路径。"
          Goto amadeus_validate_done
        ${EndIf}
      ${EndIf}

      StrCpy $0 $R0
      Call AmadeusCheckNoReparseChain
      ${If} $0 != "1"
        StrCpy $R9 "数据目录路径不能包含符号链接或目录联接（Reparse Point）。"
        Goto amadeus_validate_done
      ${EndIf}

      amadeus_validate_done:
      Push $R9
      Push $R0
      System::Store "L"
      Pop $0
      Pop $1
    FunctionEnd

    ; -------------------------------------------------------------------------
    ; State initialization & custom nsDialogs page
    ; -------------------------------------------------------------------------

    Function AmadeusInitDataDirState
      ${If} $AmadeusDataStateInitialized == "1"
        Return
      ${EndIf}
      System::Store "S"

      SetShellVarContext current
      !ifndef AMADEUS_IDENTITY_ROOT
        !define AMADEUS_IDENTITY_ROOT "$LOCALAPPDATA\${BUNDLEID}"
      !endif
      StrCpy $0 "${AMADEUS_IDENTITY_ROOT}\data"
      Call AmadeusNormalizeWinPath
      StrCpy $AmadeusDefaultDataDir $0
      StrCpy $AmadeusLocationJsonFile "${AMADEUS_IDENTITY_ROOT}\data-location.json"
      StrCpy $AmadeusHadLocationFile "0"
      StrCpy $AmadeusExistingDataDir ""
      StrCpy $AmadeusSelectedDataDir $AmadeusDefaultDataDir

      ${If} ${FileExists} "$AmadeusLocationJsonFile"
        StrCpy $0 "$AmadeusLocationJsonFile"
        Call AmadeusReadUtf8File
        ${If} $0 != ""
          Call AmadeusExtractJsonPathField
          ${If} $0 != ""
            Call AmadeusNormalizeWinPath
            StrCpy $AmadeusExistingDataDir $0
            StrCpy $AmadeusSelectedDataDir $0
            StrCpy $AmadeusHadLocationFile "1"
          ${EndIf}
        ${EndIf}
      ${EndIf}

      Call AmadeusParseCliDataDir
      ${If} $AmadeusCliDataDirProvided == "1"
        Call AmadeusNormalizeWinPath
        StrCpy $AmadeusSelectedDataDir $0
      ${EndIf}

      StrCpy $AmadeusDataStateInitialized "1"
      System::Store "L"
    FunctionEnd

    Function AmadeusDataDirPageCreate
      Call AmadeusInitDataDirState

      ; Skip interactive page in passive (/P) or auto-updater (/UPDATE) mode
      ${If} $PassiveMode = 1
      ${OrIf} $UpdateMode = 1
        Abort
      ${EndIf}

      !insertmacro MUI_HEADER_TEXT "数据储存位置" "选择 ${PRODUCTNAME} 存放对话、记忆与备份数据的文件夹。"

      nsDialogs::Create 1018
      Pop $AmadeusDataPageDialog
      ${If} $AmadeusDataPageDialog == error
        Abort
      ${EndIf}

      ${NSD_CreateLabel} 0 0 100% 26u "${PRODUCTNAME} 会将对话记录、记忆数据库、可选语音/素材包与自动备份保存在下方目录中。升级安装默认保持原有位置不变；卸载程序默认不会删除此目录中的数据。"
      Pop $0

      ${NSD_CreateLabel} 0 32u 100% 12u "数据储存文件夹（建议使用空间充足的本地 NTFS 或 exFAT 分区）："
      Pop $0

      ${NSD_CreateDirRequest} 0 46u -68u 14u "$AmadeusSelectedDataDir"
      Pop $AmadeusDataPageInput

      ${NSD_CreateBrowseButton} -62u 45u 62u 15u "浏览(B)..."
      Pop $AmadeusDataPageBrowseBtn
      ${NSD_OnClick} $AmadeusDataPageBrowseBtn AmadeusDataDirBrowse

      ${NSD_CreateButton} 0 66u 86u 15u "恢复默认位置"
      Pop $AmadeusDataPageResetBtn
      ${NSD_OnClick} $AmadeusDataPageResetBtn AmadeusDataDirResetDefault

      ${If} $AmadeusHadLocationFile == "1"
        StrCpy $1 "已检测到现有数据位置配置，默认保持原目录不变。$\r$\n默认位置：$AmadeusDefaultDataDir"
      ${Else}
        StrCpy $1 "默认位置：$AmadeusDefaultDataDir$\r$\n安装完成后，也可随时在应用设置「05 数据」中安全迁移数据目录。"
      ${EndIf}
      ${NSD_CreateLabel} 0 88u 100% 30u "$1"
      Pop $AmadeusDataPageHintLabel

      nsDialogs::Show
    FunctionEnd

    Function AmadeusDataDirBrowse
      ${NSD_GetText} $AmadeusDataPageInput $0
      Call AmadeusNormalizeWinPath
      ${If} $0 == ""
        StrCpy $0 "$AmadeusDefaultDataDir"
      ${EndIf}
      nsDialogs::SelectFolderDialog "选择 ${PRODUCTNAME} 数据储存文件夹" "$0"
      Pop $1
      ${If} $1 != "error"
      ${AndIf} $1 != ""
        StrCpy $0 $1
        Call AmadeusNormalizeWinPath
        ${NSD_SetText} $AmadeusDataPageInput "$0"
      ${EndIf}
    FunctionEnd

    Function AmadeusDataDirResetDefault
      ${NSD_SetText} $AmadeusDataPageInput "$AmadeusDefaultDataDir"
    FunctionEnd

    Function AmadeusDataDirPageLeave
      ${NSD_GetText} $AmadeusDataPageInput $0
      Call AmadeusNormalizeWinPath
      ${If} $AmadeusHadLocationFile == "1"
      ${AndIf} $0 == $AmadeusExistingDataDir
      ${AndIf} $AmadeusCliDataDirProvided != "1"
      ${AndIf} ${FileExists} "$AmadeusLocationJsonFile"
        StrCpy $AmadeusSelectedDataDir $0
        Return
      ${EndIf}
      Call AmadeusValidateDataDir
      ${If} $1 != ""
        MessageBox MB_OK|MB_ICONEXCLAMATION "$1"
        Abort
      ${EndIf}
      StrCpy $AmadeusSelectedDataDir $0
      ${If} $AmadeusSelectedDataDir != $AmadeusDefaultDataDir
        Call AmadeusVerifyExistingDataMarker
        ${If} $0 == "0"
          MessageBox MB_OK|MB_ICONEXCLAMATION "所选目录内已存在不支持或损坏的数据标记文件 amadeus-data.json，请选择其他目录。"
          Abort
        ${EndIf}
      ${EndIf}
    FunctionEnd

    ; -------------------------------------------------------------------------
    ; Apply selected data directory during installation
    ; -------------------------------------------------------------------------

    ; -------------------------------------------------------------------------
    ; Strict RFC 8259 JSON validation for amadeus-data.json (pure NSIS, no child
    ; process). Mirrors lib.rs: serde_json::from_str::<Value>(..) must succeed for
    ; the WHOLE file and marker["format"].as_u64() == Some(1) (duplicate keys: the
    ; LAST value wins, only the exact integer token 1 is valid).
    ; Known deliberate strictness beyond serde_json: nesting depth is capped at 32
    ; (serde_json allows 128) and files that do not fit one NSIS string are refused.
    ; -------------------------------------------------------------------------

    ; Input: $0 = text, $1 = index. Output: $0 = UTF-16 code unit (0 if out of range).
    Function AmadeusJsonCodeAt
      System::Store "S"
      StrLen $2 $0
      StrCpy $R9 0
      ${If} $1 >= 0
      ${AndIf} $1 < $2
        StrCpy $3 $0 1 $1
        System::Call '*(&w2 r3) p .r4'
        System::Call '*$4(&i2 .r5)'
        System::Free $4
        IntOp $R9 $5 & 0xFFFF
      ${EndIf}
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0, $1 = strings. Output: $0 = "1" only if byte-exact equal, else "0".
    ; lstrcmpW is the case-sensitive compare (unlike NSIS "=="); CompareStringOrdinal
    ; additionally refuses linguistic equality (ignorable chars such as U+00AD or
    ; hyphens would otherwise make "for-mat" compare equal to "format").
    Function AmadeusJsonStrEq
      System::Store "S"
      StrCpy $R9 "0"
      System::Call 'kernel32::lstrcmpW(w r0, w r1) i .r2'
      ${If} $2 == 0
        System::Call 'kernel32::CompareStringOrdinal(w r0, i -1, w r1, i -1, i 0) i .r3'
        ${If} $3 == 2
          StrCpy $R9 "1"
        ${EndIf}
      ${EndIf}
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = text, $1 = index of the first of exactly four hex digits.
    ; Output: $0 = value 0..65535, or -1 if not four hex digits.
    Function AmadeusJsonHex4
      System::Store "S"
      StrCpy $R9 -1
      StrCpy $R0 $0
      StrCpy $R1 $1
      StrCpy $2 0
      ${While} $2 < 4
        IntOp $3 $R1 + $2
        !insertmacro AmadeusJsonPeek $R0 $3 $4
        ${If} $4 >= 48
        ${AndIf} $4 <= 57
          ; 0-9
        ${ElseIf} $4 >= 65
        ${AndIf} $4 <= 70
          ; A-F
        ${ElseIf} $4 >= 97
        ${AndIf} $4 <= 102
          ; a-f
        ${Else}
          Goto amadeus_hex4_done
        ${EndIf}
        IntOp $2 $2 + 1
      ${EndWhile}
      StrCpy $5 $R0 4 $R1
      IntOp $R9 0x$5 + 0
      amadeus_hex4_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    ; Input: $0 = text, $1 = index right after the opening '"', $2 = "1" to decode.
    ; Output: $0 = decoded string (only when $2 = "1"), $1 = index right after the
    ; closing '"' or -1 on any error: unterminated, raw control char (< U+0020),
    ; bad escape, bad \uXXXX, lone/unpaired UTF-16 surrogate escape.
    Function AmadeusJsonScanString
      System::Store "S"
      StrCpy $R0 $0
      StrCpy $R1 $1
      StrCpy $R2 $2
      StrCpy $R8 ""
      StrCpy $R9 -1
      ${Do}
        !insertmacro AmadeusJsonPeek $R0 $R1 $3
        ${If} $3 = 0
          Goto amadeus_str_done
        ${ElseIf} $3 = 34
          IntOp $R9 $R1 + 1
          Goto amadeus_str_done
        ${ElseIf} $3 < 32
          Goto amadeus_str_done
        ${ElseIf} $3 = 92
          IntOp $R1 $R1 + 1
          !insertmacro AmadeusJsonPeek $R0 $R1 $3
          ${If} $3 = 34
          ${OrIf} $3 = 92
          ${OrIf} $3 = 47
            StrCpy $4 $R0 1 $R1
            StrCpy $R8 "$R8$4"
            IntOp $R1 $R1 + 1
          ${ElseIf} $3 = 98
          ${OrIf} $3 = 102
          ${OrIf} $3 = 110
          ${OrIf} $3 = 114
          ${OrIf} $3 = 116
            ; \b \f \n \r \t: placeholder keeps the decoded key different from any plain-letter key
            StrCpy $R8 "$R8?"
            IntOp $R1 $R1 + 1
          ${ElseIf} $3 = 117
            IntOp $R1 $R1 + 1
            StrCpy $0 $R0
            StrCpy $1 $R1
            Call AmadeusJsonHex4
            ${If} $0 < 0
              Goto amadeus_str_done
            ${EndIf}
            StrCpy $5 $0
            IntOp $R1 $R1 + 4
            ${If} $5 >= 0xD800
            ${AndIf} $5 <= 0xDBFF
              ; high surrogate must be followed by \uDC00..\uDFFF
              !insertmacro AmadeusJsonPeek $R0 $R1 $3
              ${If} $3 <> 92
                Goto amadeus_str_done
              ${EndIf}
              IntOp $6 $R1 + 1
              !insertmacro AmadeusJsonPeek $R0 $6 $3
              ${If} $3 <> 117
                Goto amadeus_str_done
              ${EndIf}
              IntOp $6 $R1 + 2
              StrCpy $0 $R0
              StrCpy $1 $6
              Call AmadeusJsonHex4
              ${If} $0 < 0xDC00
              ${OrIf} $0 > 0xDFFF
                Goto amadeus_str_done
              ${EndIf}
              IntFmt $7 "%c" $5
              IntFmt $8 "%c" $0
              StrCpy $R8 "$R8$7$8"
              IntOp $R1 $R1 + 6
            ${ElseIf} $5 >= 0xDC00
            ${AndIf} $5 <= 0xDFFF
              Goto amadeus_str_done
            ${ElseIf} $5 = 0
              StrCpy $R8 "$R8?"
            ${Else}
              IntFmt $7 "%c" $5
              StrCpy $R8 "$R8$7"
            ${EndIf}
          ${Else}
            Goto amadeus_str_done
          ${EndIf}
        ${Else}
          StrCpy $4 $R0 1 $R1
          StrCpy $R8 "$R8$4"
          IntOp $R1 $R1 + 1
        ${EndIf}
      ${Loop}
      amadeus_str_done:
      Push $R9
      Push $R8
      System::Store "L"
      Pop $0
      Pop $1
    FunctionEnd

    ; Input: $0 = text, $1 = index of '-' or first digit.
    ; Output: $0 = number token, $1 = index after it, or -1 if the RFC 8259 number
    ; grammar -? (0|[1-9][0-9]*) (.[0-9]+)? ([eE][+-]?[0-9]+)? is violated.
    Function AmadeusJsonScanNumber
      System::Store "S"
      StrCpy $R0 $0
      StrCpy $R1 $1
      StrCpy $R2 $1
      StrCpy $R8 ""
      StrCpy $R9 -1

      !insertmacro AmadeusJsonPeek $R0 $R2 $2
      ${If} $2 = 45
        IntOp $R2 $R2 + 1
        !insertmacro AmadeusJsonPeek $R0 $R2 $2
      ${EndIf}
      ${If} $2 = 48
        IntOp $R2 $R2 + 1
      ${ElseIf} $2 >= 49
      ${AndIf} $2 <= 57
        !insertmacro AmadeusJsonDigits $R0 $R2 $2 $3
      ${Else}
        Goto amadeus_num_done
      ${EndIf}

      !insertmacro AmadeusJsonPeek $R0 $R2 $2
      ${If} $2 = 46
        IntOp $R2 $R2 + 1
        !insertmacro AmadeusJsonDigits $R0 $R2 $2 $3
        ${If} $3 = 0
          Goto amadeus_num_done
        ${EndIf}
      ${EndIf}

      !insertmacro AmadeusJsonPeek $R0 $R2 $2
      ${If} $2 = 101
      ${OrIf} $2 = 69
        IntOp $R2 $R2 + 1
        !insertmacro AmadeusJsonPeek $R0 $R2 $2
        ${If} $2 = 43
        ${OrIf} $2 = 45
          IntOp $R2 $R2 + 1
        ${EndIf}
        !insertmacro AmadeusJsonDigits $R0 $R2 $2 $3
        ${If} $3 = 0
          Goto amadeus_num_done
        ${EndIf}
      ${EndIf}

      IntOp $4 $R2 - $R1
      StrCpy $R8 $R0 $4 $R1
      StrCpy $R9 $R2
      amadeus_num_done:
      Push $R9
      Push $R8
      System::Store "L"
      Pop $0
      Pop $1
    FunctionEnd

    ; Input: $0 = whole file text. Output: $0 = "1" iff the text is exactly one valid
    ; JSON object (only JSON whitespace after the closing brace) whose final top-level
    ; "format" member is the integer token 1; otherwise "0".
    ; States: T top, KO key-or-}, K key, C colon, V value, VA value-or-], N after value,
    ; E end (only whitespace until EOF).
    Function AmadeusJsonStrictMarker
      System::Store "S"
      StrCpy $R1 $0
      StrCpy $R3 0
      StrCpy $R5 ""
      StrCpy $R6 0
      StrCpy $R7 "T"
      StrCpy $R8 "0"
      StrCpy $R9 "0"
      StrCpy $2 "0"

      amadeus_jv_loop:
      ; skip JSON whitespace (space, \t, \n, \r only)
      ${Do}
        !insertmacro AmadeusJsonPeek $R1 $R3 $R4
        ${If} $R4 = 32
        ${OrIf} $R4 = 9
        ${OrIf} $R4 = 10
        ${OrIf} $R4 = 13
          IntOp $R3 $R3 + 1
        ${Else}
          ${Break}
        ${EndIf}
      ${Loop}

      ${If} $R7 == "E"
        ${If} $R4 = 0
        ${AndIf} $R8 == "1"
          StrCpy $R9 "1"
        ${EndIf}
        Goto amadeus_jv_done
      ${EndIf}

      ${If} $R7 == "T"
        ${If} $R4 = 123
          Goto amadeus_jv_open
        ${EndIf}
        Goto amadeus_jv_done
      ${EndIf}

      ${If} $R7 == "KO"
      ${AndIf} $R4 = 125
        Goto amadeus_jv_close
      ${EndIf}
      ${If} $R7 == "KO"
      ${OrIf} $R7 == "K"
        ${If} $R4 <> 34
          Goto amadeus_jv_done
        ${EndIf}
        StrCpy $0 $R1
        IntOp $1 $R3 + 1
        ${If} $R6 = 1
          StrCpy $2 "1"
        ${Else}
          StrCpy $2 "0"
        ${EndIf}
        Call AmadeusJsonScanString
        ${If} $1 < 0
          Goto amadeus_jv_done
        ${EndIf}
        StrCpy $R3 $1
        StrCpy $9 "0"
        ${If} $R6 = 1
          StrCpy $1 "format"
          Call AmadeusJsonStrEq
          StrCpy $9 $0
        ${EndIf}
        StrCpy $R7 "C"
        Goto amadeus_jv_loop
      ${EndIf}

      ${If} $R7 == "C"
        ${If} $R4 <> 58
          Goto amadeus_jv_done
        ${EndIf}
        IntOp $R3 $R3 + 1
        StrCpy $R7 "V"
        Goto amadeus_jv_loop
      ${EndIf}

      ${If} $R7 == "N"
        StrCpy $3 $R5 1 -1
        ${If} $R4 = 44
          IntOp $R3 $R3 + 1
          ${If} $3 == "O"
            StrCpy $R7 "K"
          ${Else}
            StrCpy $R7 "V"
          ${EndIf}
          Goto amadeus_jv_loop
        ${EndIf}
        ${If} $R4 = 125
        ${AndIf} $3 == "O"
          Goto amadeus_jv_close
        ${EndIf}
        ${If} $R4 = 93
        ${AndIf} $3 == "A"
          Goto amadeus_jv_close
        ${EndIf}
        Goto amadeus_jv_done
      ${EndIf}

      ; ---- states V / VA: a JSON value is expected ----
      ${If} $R7 == "VA"
      ${AndIf} $R4 = 93
        Goto amadeus_jv_close
      ${EndIf}
      ${If} $R4 = 123
      ${OrIf} $R4 = 91
        Goto amadeus_jv_open
      ${EndIf}
      StrCpy $5 ""
      ${If} $R4 = 34
        StrCpy $0 $R1
        IntOp $1 $R3 + 1
        StrCpy $2 "0"
        Call AmadeusJsonScanString
        ${If} $1 < 0
          Goto amadeus_jv_done
        ${EndIf}
        StrCpy $R3 $1
        Goto amadeus_jv_value_done
      ${EndIf}
      StrCpy $3 ""
      ${If} $R4 = 116
        StrCpy $3 "true"
      ${ElseIf} $R4 = 102
        StrCpy $3 "false"
      ${ElseIf} $R4 = 110
        StrCpy $3 "null"
      ${EndIf}
      ${If} $3 != ""
        StrLen $4 $3
        StrCpy $0 $R1 $4 $R3
        StrCpy $1 $3
        Call AmadeusJsonStrEq
        ${If} $0 != "1"
          Goto amadeus_jv_done
        ${EndIf}
        IntOp $R3 $R3 + $4
        Goto amadeus_jv_value_done
      ${EndIf}
      StrCpy $3 "0"
      ${If} $R4 = 45
        StrCpy $3 "1"
      ${ElseIf} $R4 >= 48
      ${AndIf} $R4 <= 57
        StrCpy $3 "1"
      ${EndIf}
      ${If} $3 == "1"
        StrCpy $0 $R1
        StrCpy $1 $R3
        Call AmadeusJsonScanNumber
        ${If} $1 < 0
          Goto amadeus_jv_done
        ${EndIf}
        StrCpy $5 $0
        StrCpy $R3 $1
        Goto amadeus_jv_value_done
      ${EndIf}
      Goto amadeus_jv_done

      amadeus_jv_value_done:
      ; a scalar just ended; $5 = number token or "" (string/true/false/null)
      ${If} $R6 = 1
      ${AndIf} $9 == "1"
        StrCpy $R8 "0"
        ${If} $5 == "1"
          StrCpy $R8 "1"
        ${EndIf}
        StrCpy $9 "0"
      ${EndIf}
      StrCpy $R7 "N"
      Goto amadeus_jv_loop

      amadeus_jv_open:
      ; $R4 = 123 ('{') or 91 ('['); nesting limit 32
      ${If} $R6 >= 32
        Goto amadeus_jv_done
      ${EndIf}
      ${If} $R6 = 1
      ${AndIf} $9 == "1"
        ; the "format" member is a container, never the integer 1
        StrCpy $R8 "0"
        StrCpy $9 "0"
      ${EndIf}
      ${If} $R4 = 123
        StrCpy $3 "O"
        StrCpy $R7 "KO"
      ${Else}
        StrCpy $3 "A"
        StrCpy $R7 "VA"
      ${EndIf}
      StrCpy $R5 "$R5$3"
      IntOp $R6 $R6 + 1
      IntOp $R3 $R3 + 1
      Goto amadeus_jv_loop

      amadeus_jv_close:
      IntOp $R6 $R6 - 1
      StrLen $3 $R5
      IntOp $3 $3 - 1
      StrCpy $R5 $R5 $3
      IntOp $R3 $R3 + 1
      ${If} $R6 = 0
        StrCpy $R7 "E"
      ${Else}
        StrCpy $R7 "N"
      ${EndIf}
      Goto amadeus_jv_loop

      amadeus_jv_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

      ; Validate existing amadeus-data.json (strict whole-file JSON + final "format" == 1)
      ; before publishing/updating data-location.json.
      ; Input: $0 = directory path. Output: $0 = "1" valid, "2" marker missing, else "0".
    Function AmadeusVerifyExistingDataMarker
      System::Store "S"
      StrCpy $R0 "$0\amadeus-data.json"
      StrCpy $R9 "0"

      System::Call 'kernel32::GetFileAttributesW(w R0) i .r1'
      ${If} $1 == -1
        ; Missing marker: caller decides whether to create {"format":1}
        StrCpy $R9 "2"
        Goto amadeus_marker_done
      ${EndIf}
      IntOp $2 $1 & 0x0410 ; refuse directory or reparse point
      ${If} $2 <> 0
        Goto amadeus_marker_done
      ${EndIf}

      ; AmadeusReadUtf8File returns "" for empty, oversize/truncated, invalid UTF-8,
      ; short-read or NUL-containing files; serde_json rejects a leading BOM too.
      StrCpy $0 "$R0"
      Call AmadeusReadUtf8File
      StrCpy $R1 "$0"
      ${If} $R1 == ""
        Goto amadeus_marker_done
      ${EndIf}
      ${If} $AmadeusReadHadBom == "1"
        Goto amadeus_marker_done
      ${EndIf}

      StrCpy $0 "$R1"
      Call AmadeusJsonStrictMarker
      ${If} $0 == "1"
        StrCpy $R9 "1"
      ${EndIf}

      amadeus_marker_done:
      Push $R9
      System::Store "L"
      Pop $0
    FunctionEnd

    Function AmadeusApplyDataLocation
      Call AmadeusInitDataDirState
      System::Store "S"

      SetShellVarContext current
      StrCpy $R0 "${AMADEUS_IDENTITY_ROOT}"

      ; Case 0: Preserving an existing registered location during upgrade (neither user nor /DATADIR changed it)
      ${If} $AmadeusHadLocationFile == "1"
      ${AndIf} $AmadeusSelectedDataDir == $AmadeusExistingDataDir
      ${AndIf} $AmadeusCliDataDirProvided != "1"
      ${AndIf} ${FileExists} "$AmadeusLocationJsonFile"
        Goto amadeus_apply_done
      ${EndIf}

      ; Case 1: Selected directory equals default ($LOCALAPPDATA\${BUNDLEID}\data)
      ${If} $AmadeusSelectedDataDir == $AmadeusDefaultDataDir
        ${If} $AmadeusHadLocationFile == "1"
          ; User or explicit /DATADIR=<default> reset a previous custom location back to default
          StrCpy $0 "$R0"
          Call AmadeusCheckNoReparseChain
          ${If} $0 != "1"
            Abort "Refusing symlink or reparse point in LOCALAPPDATA identity directory."
          ${EndIf}
          System::Call 'kernel32::GetFileAttributesW(w "$AmadeusLocationJsonFile") i .r2'
          ${If} $2 <> -1
            IntOp $3 $2 & 0x0400
            ${If} $3 <> 0
              Abort "Refusing symlink or reparse point on data-location.json."
            ${EndIf}
            Delete "$AmadeusLocationJsonFile"
          ${EndIf}
        ${EndIf}
        ; Fresh default install: write nothing so default behavior and install_smoke stay 100% untouched.
        Goto amadeus_apply_done
      ${EndIf}

      ; Validate non-default custom directory before creating/writing anything
      StrCpy $0 "$AmadeusSelectedDataDir"
      Call AmadeusValidateDataDir
      ${If} $1 != ""
        Abort "$1"
      ${EndIf}
      StrCpy $AmadeusSelectedDataDir $0

      ; If target directory already has an amadeus-data.json marker, validate {"format":1} BEFORE creating or modifying pointer
      StrCpy $0 "$AmadeusSelectedDataDir"
      Call AmadeusVerifyExistingDataMarker
      ${If} $0 == "0"
        Abort "所选目录内已存在不支持或损坏的数据标记文件 amadeus-data.json，无法作为数据储存位置。"
      ${EndIf}
      StrCpy $R3 "$0" ; "1" = valid existing marker, "2" = missing marker (create after dir check)

      ; Validate $LOCALAPPDATA\${BUNDLEID} chain has no reparse points
      StrCpy $0 "$R0"
      Call AmadeusCheckNoReparseChain
      ${If} $0 != "1"
        Abort "Refusing symlink or reparse point in LOCALAPPDATA identity directory."
      ${EndIf}

      ; Create <CustomDataDir> and verify it exists as a real non-link directory
      CreateDirectory "$AmadeusSelectedDataDir"
      System::Call 'kernel32::GetFileAttributesW(w "$AmadeusSelectedDataDir") i .r2'
      ${If} $2 == -1
        Abort "无法创建所选的数据目录：$AmadeusSelectedDataDir"
      ${EndIf}
      IntOp $3 $2 & 0x0400
      IntOp $4 $2 & 0x0010
      ${If} $3 <> 0
      ${OrIf} $4 == 0
        Abort "所选数据目录无效或包含符号链接：$AmadeusSelectedDataDir"
      ${EndIf}

      ; Write {"format":1} only when missing; valid existing marker is preserved untouched
      ${If} $R3 == "2"
        StrCpy $0 "$AmadeusSelectedDataDir\amadeus-data.json"
        StrCpy $1 '{"format":1}'
        Call AmadeusAtomicWriteUtf8NoBom
        ${If} $0 != "1"
          Abort "无法在所选数据目录写入标记文件 amadeus-data.json。"
        ${EndIf}
      ${EndIf}

      ; Case 2: /DATADIR=<same existing custom path> -> keep existing data-location.json metadata intact
      ${If} $AmadeusHadLocationFile == "1"
      ${AndIf} $AmadeusSelectedDataDir == $AmadeusExistingDataDir
      ${AndIf} ${FileExists} "$AmadeusLocationJsonFile"
        Goto amadeus_apply_done
      ${EndIf}

      ; Case 3: New custom directory -> create $LOCALAPPDATA\${BUNDLEID} and write UTF-8 no-BOM data-location.json
      CreateDirectory "$R0"
      System::Call 'kernel32::GetFileAttributesW(w R0) i .r2'
      ${If} $2 == -1
        Abort "无法创建本地配置目录：$R0"
      ${EndIf}
      IntOp $3 $2 & 0x0400
      ${If} $3 <> 0
        Abort "本地配置目录不能是符号链接：$R0"
      ${EndIf}

      StrCpy $0 "$AmadeusSelectedDataDir"
      Call AmadeusJsonEscapeString
      StrCpy $R2 '{"path":"$0"}'

      StrCpy $0 "$AmadeusLocationJsonFile"
      StrCpy $1 "$R2"
      Call AmadeusAtomicWriteUtf8NoBom
      ${If} $0 != "1"
        Abort "无法写入数据位置配置文件 data-location.json。"
      ${EndIf}

      amadeus_apply_done:
      System::Store "L"
    FunctionEnd

  !endif
!macroend

!macro NSIS_HOOK_PREINSTALL
  Call AmadeusInitDataDirState
  ${If} $AmadeusHadLocationFile == "1"
  ${AndIf} $AmadeusSelectedDataDir == $AmadeusExistingDataDir
  ${AndIf} $AmadeusCliDataDirProvided != "1"
  ${AndIf} ${FileExists} "$AmadeusLocationJsonFile"
    ; Upgrade keeping existing registered location: do not block or alter it pre-install.
  ${ElseIf} $AmadeusSelectedDataDir != $AmadeusDefaultDataDir
    StrCpy $0 "$AmadeusSelectedDataDir"
    Call AmadeusValidateDataDir
    ${If} $1 != ""
      Abort "$1"
    ${EndIf}
    StrCpy $AmadeusSelectedDataDir $0
    Call AmadeusVerifyExistingDataMarker
    ${If} $0 == "0"
      Abort "所选目录内已存在不支持或损坏的数据标记文件 amadeus-data.json，无法作为数据储存位置。"
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  Call AmadeusApplyDataLocation
!macroend

!endif ; AMADEUS_NSIS_DATA_LOCATION_INCLUDED
