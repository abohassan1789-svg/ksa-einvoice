' KSA e-Invoice launcher (used by the Desktop / Start Menu shortcuts).
'
' Runs the application from its own virtual environment and points it at the
' connection profile THIS installation wrote (config\connection.json), via the
' PHASEINV2_CONFIG_PATH override. That keeps the app on its own database even if
' another PHASEINV2/CRM configuration happens to exist on the machine.
'
' Launches pythonw.exe hidden, so there is no console window.
Option Explicit
Dim sh, fso, appDir, pyw, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw    = appDir & "\.venv\Scripts\pythonw.exe"

sh.Environment("Process")("PHASEINV2_CONFIG_PATH") = appDir & "\config\connection.json"
sh.CurrentDirectory = appDir

If Not fso.FileExists(pyw) Then
    MsgBox "KSA e-Invoice is not fully installed (missing virtual environment)." & vbCrLf & _
           "Please re-run the installer.", vbCritical, "KSA e-Invoice"
    WScript.Quit 1
End If

cmd = """" & pyw & """ -m app.main"
sh.Run cmd, 0, False
