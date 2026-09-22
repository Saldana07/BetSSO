' Ejecuta un .bat sin ventana de consola. Lo usan las tareas programadas
' (register_task.ps1): sin esto, el Programador de Windows abre una consola
' visible cada vez que corre (cada 2 h la liquidacion), aunque la app no
' se este usando. La salida del .bat va al log que se pasa como argumento.
'
' Uso: wscript.exe //B //Nologo run_hidden.vbs <ruta.bat> <argumentos del bat> <ruta.log>
'   El ultimo argumento es SIEMPRE el log; los del medio se pasan al .bat.
Option Explicit
Dim shell, args, i, bat, logFile, batArgs, cmd
Set shell = CreateObject("WScript.Shell")
Set args = WScript.Arguments
If args.Count < 2 Then
    WScript.Quit 2
End If
bat = args(0)
logFile = args(args.Count - 1)
batArgs = ""
For i = 1 To args.Count - 2
    batArgs = batArgs & " " & args(i)
Next
' 0 = ventana oculta; True = esperar a que termine (el Programador ve el codigo de salida real).
cmd = "cmd.exe /c """"" & bat & """" & batArgs & " >> """ & logFile & """ 2>&1"""
WScript.Quit shell.Run(cmd, 0, True)
