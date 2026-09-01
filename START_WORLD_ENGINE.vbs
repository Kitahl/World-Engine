Option Explicit
Dim shell, fso, root, pythonw, script, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fso.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If
script = fso.BuildPath(root, "world_engine_startup.py")
command = Chr(34) & pythonw & Chr(34) & " " & _
          Chr(34) & script & Chr(34) & " --root " & _
          Chr(34) & root & Chr(34)
' Window style 0 is hidden; False means do not wait or create a second console.
shell.Run command, 0, False
