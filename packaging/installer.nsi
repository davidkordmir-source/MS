Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
Name "Terrain Studio 0.2.0"
OutFile "../dist/installer/TerrainStudio-0.2.0-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\TerrainPolygon"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 8
VIProductVersion "0.2.0.0"
VIAddVersionKey "ProductName" "Terrain Studio"
VIAddVersionKey "FileDescription" "Offline Terrain Polygon Export Installer"
VIAddVersionKey "FileVersion" "0.2.0"
VIAddVersionKey "LegalCopyright" "Terrain Polygon Export contributors"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\TerrainPolygon.exe"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Windows x64 is required."
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "Windows 10 or Windows 11 is required."
    Abort
  ${EndIf}
FunctionEnd

Section "Application"
  SetShellVarContext current
  SetRegView 64
  SetOutPath "$INSTDIR"
  File /r "../dist/TerrainPolygon/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Terrain Polygon Export"
  CreateShortcut "$SMPROGRAMS\Terrain Polygon Export\Terrain Polygon Export.lnk" "$INSTDIR\TerrainPolygon.exe"
  CreateShortcut "$SMPROGRAMS\Terrain Polygon Export\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\Terrain Polygon Export.lnk" "$INSTDIR\TerrainPolygon.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon" "DisplayName" "Terrain Studio 0.2.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon" "DisplayVersion" "0.2.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon" "DisplayIcon" "$INSTDIR\TerrainPolygon.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  !include "uninstall-files.nsh"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\Terrain Polygon Export.lnk"
  Delete "$SMPROGRAMS\Terrain Polygon Export\Terrain Polygon Export.lnk"
  Delete "$SMPROGRAMS\Terrain Polygon Export\Uninstall.lnk"
  RMDir "$SMPROGRAMS\Terrain Polygon Export"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\TerrainPolygon"
SectionEnd
