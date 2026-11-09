!macro NSIS_HOOK_PREINSTALL
  ; Tauri's in-place update skips uninstall. Replace the entire packaged
  ; interpreter and suite so removed modules and cases cannot survive.
  ${If} $INSTDIR != ""
  ${AndIf} ${FileExists} "$INSTDIR\runtime\.mskdsp-testbench-runtime"
    !insertmacro CheckIfAppIsRunning "$INSTDIR\${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
    ClearErrors
    RMDir /r "$INSTDIR\runtime"
    ${If} ${Errors}
      Abort "Could not replace the bundled testbench runtime. Close the testbench and retry."
    ${EndIf}
  ${EndIf}
!macroend
