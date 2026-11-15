Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
native = fso.BuildPath(ws.CurrentDirectory, "release\SuperBay-Native.exe")
If Not fso.FileExists(native) Then
    native = fso.BuildPath(ws.CurrentDirectory, "target\release\superbay.exe")
End If
If fso.FileExists(native) Then
    If fso.FileExists(fso.BuildPath(ws.CurrentDirectory, "config\config.yaml")) Or _
       fso.FileExists(fso.BuildPath(ws.CurrentDirectory, "config\data.db")) Then
        ws.Environment("PROCESS")("SUPERBAY_DATA_DIR") = ws.CurrentDirectory
    End If
    ws.Run Chr(34) & native & Chr(34), vbNormalFocus, False
Else
    MsgBox "还没有可运行的程序，请先执行 cargo build --release --locked。", vbExclamation, "SuperBay"
End If
