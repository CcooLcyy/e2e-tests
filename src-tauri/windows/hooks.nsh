!define TESTBENCH_RUNTIME_SOURCE "${__FILEDIR__}\..\.bundle\runtime"
!include "${TESTBENCH_RUNTIME_SOURCE}\python-fingerprint.nsh"

!macro NSIS_HOOK_PREINSTALL
  ${If} $INSTDIR == ""
    Abort "The testbench installation directory is missing."
  ${EndIf}
  !insertmacro CheckIfAppIsRunning "$INSTDIR\${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

  ${IfNot} ${FileExists} "$INSTDIR\runtime\.mskdsp-testbench-runtime"
    ${If} ${FileExists} "$INSTDIR\runtime\python\."
    ${OrIf} ${FileExists} "$INSTDIR\runtime\suite\."
      Abort "The installation directory contains an unmanaged Python or test suite directory. Choose another location."
    ${EndIf}
  ${EndIf}

  ; The previous layout stored cases alongside Python directly under runtime.
  ${If} ${FileExists} "$INSTDIR\runtime\.mskdsp-testbench-runtime"
  ${AndIfNot} ${FileExists} "$INSTDIR\runtime\python\.mskdsp-testbench-python"
    ClearErrors
    RMDir /r "$INSTDIR\runtime"
    ${If} ${Errors}
      Abort "Could not remove the old testbench runtime. Close the testbench and retry."
    ${EndIf}
  ${EndIf}

  ; Record ownership before extracting files so an interrupted install can be retried.
  ${IfNot} ${FileExists} "$INSTDIR\runtime\.mskdsp-testbench-runtime"
    ClearErrors
    SetOutPath "$INSTDIR\runtime"
    SetOverwrite try
    File "${TESTBENCH_RUNTIME_SOURCE}\.mskdsp-testbench-runtime"
    SetOverwrite on
    SetOutPath "$INSTDIR"
    ${If} ${Errors}
      Abort "Could not prepare the testbench runtime directory. Retry the installation."
    ${EndIf}
  ${EndIf}

  StrCpy $0 ""
  ${If} ${FileExists} "$INSTDIR\runtime\python\python.exe"
    ClearErrors
    FileOpen $1 "$INSTDIR\runtime\python\.mskdsp-testbench-python" r
    ${IfNot} ${Errors}
      FileRead $1 $0
      FileClose $1
    ${EndIf}
  ${EndIf}

  ${If} $0 == "${TESTBENCH_PYTHON_FINGERPRINT}"
    DetailPrint "Reusing the bundled Python runtime."
  ${Else}
    DetailPrint "Installing the bundled Python runtime."
    ClearErrors
    ${If} ${FileExists} "$INSTDIR\runtime\python\."
      RMDir /r "$INSTDIR\runtime\python"
    ${EndIf}
    ${If} ${Errors}
      Abort "Could not replace the bundled Python runtime. Close the testbench and retry."
    ${EndIf}

    SetOutPath "$INSTDIR\runtime\python"
    SetOverwrite try
    File /r "${TESTBENCH_RUNTIME_SOURCE}\python\*.*"
    SetOverwrite on
    ${If} ${Errors}
      Abort "Could not extract the bundled Python runtime. Retry the installation."
    ${EndIf}
    SetOutPath "$INSTDIR"

    FileOpen $1 "$INSTDIR\runtime\python\.mskdsp-testbench-python" w
    ${If} ${Errors}
      Abort "Could not record the bundled Python version. Retry the installation."
    ${EndIf}
    FileWrite $1 "${TESTBENCH_PYTHON_FINGERPRINT}"
    FileClose $1
    ${If} ${Errors}
      Abort "Could not record the bundled Python version. Retry the installation."
    ${EndIf}
  ${EndIf}

  ; Replace all suite files so removed modules and cases cannot survive an update.
  ClearErrors
  ${If} ${FileExists} "$INSTDIR\runtime\suite\."
    RMDir /r "$INSTDIR\runtime\suite"
  ${EndIf}
  ${If} ${Errors}
    Abort "Could not replace the bundled test suite. Close the testbench and retry."
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  Push $0
  DetailPrint "Preparing the testbench data directory."
  nsExec::ExecToLog '"$INSTDIR\runtime\python\python.exe" -X utf8 -B -m testbench.workspace "$LOCALAPPDATA\${BUNDLEID}" "$INSTDIR\data"'
  Pop $0
  ${If} $0 != "0"
    Pop $0
    Abort "Could not prepare the testbench data directory. Use a writable installation directory and retry."
  ${EndIf}
  DetailPrint "Preparing Microsoft Edge WebDriver."
  nsExec::ExecToLog '"$INSTDIR\runtime\python\python.exe" -X utf8 -B -m testbench.webview_driver "$INSTDIR\data\.tools"'
  Pop $0
  ${If} $0 != "0"
    DetailPrint "WebDriver could not be prepared. Select a matching driver in Test Environment."
  ${EndIf}
  Pop $0
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "$INSTDIR\${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
  ClearErrors
  ${If} ${FileExists} "$INSTDIR\runtime\.mskdsp-testbench-runtime"
  ${OrIf} ${FileExists} "$INSTDIR\runtime\python\.mskdsp-testbench-python"
    RMDir /r "$INSTDIR\runtime"
  ${EndIf}
  ${If} ${Errors}
    Abort "Could not remove the testbench runtime. Close the testbench and retry."
  ${EndIf}
!macroend
